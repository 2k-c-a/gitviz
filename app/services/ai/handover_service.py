"""인수인계(S12): 인계 대상, 준비 체크폼, 초안 생성, 편집 문서, 내보내기.

- 준비 정보는 항목 6개를 버전마다 통째로 추가 저장한다. 입력하지 않은 항목은 `unknown`이고 `needed`로 바꾸지 않는다.
- 초안은 모델 호출 없이 저장된 자료(대상·준비 버전·AI 결과·피드백·분석 snapshot)를 고정 문구로 옮겨 적는다. 사용한 자료의 버전을 `sources`로 고정한다.
- 생성 원본(handover_generations)은 수정할 수 없고, 사람이 고치는 문서(handover_documents)는 원본과 분리돼 expected_version으로만 바뀐다.
  새 원본이 생겨도 편집한 문서는 덮어쓰지 않는다(편집하지 않은 문서만 새 원본을 따라간다).
- 서비스는 commit까지 하며, 저장소 접근 권한 확인은 호출하는 API가 한다.
"""
import json
import re
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.schemas.ai import (HANDOVER_ITEM_KEYS, ErrorCode, HandoverAdoptRequest, HandoverDocumentPut, HandoverGenerateRequest, HandoverPreparationPut,
                            HandoverTargetCreate, HandoverTargetPatch, canonical_hash)
from app.services.ai import handover_draft, history_context, markdown_safety
from app.services.ai.access import AIError, ensure_owner
from app.models.analysis_snapshot import AnalysisSnapshot

MAX_TARGETS_PER_OWNER = 100
MAX_GENERATIONS_PER_TARGET = 50
MAX_PREPARATION_VERSIONS = 500
MAX_DOCUMENT_VERSIONS = 100
MAX_PATH_CHARS = 500
LINK_RE = re.compile(r'^https?://[^\s<>"`\\\x00-\x1f\x7f]{1,490}$', re.I)
CONTROL_RE = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f‪-‮⁦-⁩​-‏]')
REQUIRED_INFO = {   # 준비 완료로 기록하려면 있어야 하는 정보: 'text' 필수 또는 text/link 중 하나
    'assignee': 'text', 'description': 'text_or_link', 'run_method': 'text_or_link', 'verify_method': 'text_or_link',
    'joint_review': 'text', 'receiver_run': 'text'}
REASON_TEXT = {'target_changed': '인계 대상 정보가 바뀌었습니다.', 'preparation_changed': '준비 정보가 바뀌었습니다.',
               'feedback_changed': 'AI 지적에 대한 피드백이 바뀌었습니다.', 'source_missing': '사용한 AI 결과를 더 이상 읽을 수 없습니다(삭제 또는 보관 기간 만료).'}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _audit(db: Session, owner_id: UUID, repository_id: int, action: str, object_id, request_id: str, details: dict) -> None:
    db.execute(text("""INSERT INTO audit_events (actor_user_id, repository_id, action, object_type, object_id, request_id, details)
                       VALUES (:a, :r, :act, 'handover', :o, :q, CAST(:d AS jsonb))"""),
               {'a': owner_id, 'r': repository_id, 'act': action, 'o': str(object_id), 'q': request_id, 'd': json.dumps(details)})   # 본문은 감사 기록에 넣지 않는다


def _conflict(db: Session, message: str, **details) -> AIError:
    db.rollback()
    return AIError(ErrorCode.VERSION_CONFLICT, message, details=details)


# ── 입력 검증 ───────────────────────────────────────────────────────────

def normalize_paths(db: Session, paths: list[str]) -> list[str]:
    out, seen = [], set()
    for raw in paths:
        p = (raw or '').strip().replace('\\', '/')
        while p.startswith('./'):
            p = p[2:]
        if (not p or len(p) > MAX_PATH_CHARS or p.startswith('/') or CONTROL_RE.search(p) or any(seg in ('', '.', '..') for seg in p.split('/'))):
            raise AIError(ErrorCode.INVALID_SELECTION, '파일 경로가 올바르지 않습니다. 저장소 안의 상대 경로만 쓸 수 있습니다.')
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _clean_memo(value: str | None, limit: int, *, what: str) -> str | None:
    if value is None:
        return None
    v = value.strip()
    if CONTROL_RE.search(v):
        raise AIError(ErrorCode.INVALID_SELECTION, f'{what}에 쓸 수 없는 문자가 있습니다.')
    return v[:limit] or None


def _check_snapshot(db: Session, repository_id: int, snapshot_id: int | None) -> None:
    if snapshot_id is None:
        return
    if not db.execute(text('SELECT 1 FROM analysis_snapshots WHERE id = :i AND repository_id = :r'), {'i': snapshot_id, 'r': repository_id}).scalar():
        raise AIError(ErrorCode.INVALID_SELECTION, '이 저장소의 분석 결과(snapshot)가 아닙니다.')


# ── 대상 ────────────────────────────────────────────────────────────────

def _target_row(db: Session, target_id: UUID, owner_id: UUID, *, lock: bool = False):
    row = db.execute(text('SELECT * FROM handover_targets WHERE id = :t' + (' FOR UPDATE' if lock else '')), {'t': target_id}).one_or_none()
    try:
        if row is None:
            raise AIError(ErrorCode.RESOURCE_NOT_FOUND, '인계 대상을 찾을 수 없습니다.')
        ensure_owner(row.owner_user_id, owner_id)               # 남의 대상은 존재를 숨긴다
    except AIError:
        db.rollback()
        raise
    return row


def _latest_prep(db: Session, target_id: UUID):
    return db.execute(text('SELECT * FROM handover_preparation_versions WHERE target_id = :t ORDER BY version DESC LIMIT 1'), {'t': target_id}).one_or_none()


def target_view(db: Session, row) -> dict:
    prep = _latest_prep(db, row.id)
    has_doc = bool(db.execute(text('SELECT 1 FROM handover_documents WHERE target_id = :t'), {'t': row.id}).scalar())
    return {'target_id': row.id, 'repository_id': row.repository_id, 'name': row.name, 'purpose': row.purpose, 'giver_name': row.giver_name,
            'receiver_name': row.receiver_name, 'selected_paths': list(row.selected_paths), 'snapshot_id': row.snapshot_id, 'version': row.version,
            'scope_version': row.scope_version, 'preparation_version': prep.version if prep else 0,
            'needs_reconfirm': bool(prep and prep.target_version < row.scope_version), 'has_document': has_doc,
            'created_at': row.created_at, 'updated_at': row.updated_at}


def create_target(db: Session, owner_id: UUID, body: HandoverTargetCreate, *, request_id: str = '-') -> dict:
    paths = normalize_paths(db, body.selected_paths)
    _check_snapshot(db, body.repository_id, body.snapshot_id)
    name = _clean_memo(body.name, 120, what='이름')
    if not name:
        raise AIError(ErrorCode.INVALID_SELECTION, '이름을 입력해 주세요.')
    if db.execute(text('SELECT count(*) FROM handover_targets WHERE owner_user_id = :o'), {'o': owner_id}).scalar() >= MAX_TARGETS_PER_OWNER:
        raise AIError(ErrorCode.USER_LIMIT, f'인계 대상은 {MAX_TARGETS_PER_OWNER}개까지 만들 수 있습니다.', retryable=False)
    row = db.execute(text("""INSERT INTO handover_targets (owner_user_id, repository_id, name, purpose, giver_name, receiver_name, selected_paths, snapshot_id)
                             VALUES (:o, :r, :n, :p, :g, :rc, CAST(:paths AS jsonb), :s) RETURNING *"""),
                     {'o': owner_id, 'r': body.repository_id, 'n': name, 'p': _clean_memo(body.purpose, 500, what='목적'),
                      'g': _clean_memo(body.giver_name, 100, what='인계자'), 'rc': _clean_memo(body.receiver_name, 100, what='인수자'),
                      'paths': json.dumps(paths), 's': body.snapshot_id}).one()
    _audit(db, owner_id, row.repository_id, 'handover.target_created', row.id, request_id, {'paths': len(paths)})
    db.commit()
    return target_view(db, row)


def list_targets(db: Session, owner_id: UUID, repository_id: int) -> list[dict]:
    rows = db.execute(text('SELECT * FROM handover_targets WHERE owner_user_id = :o AND repository_id = :r ORDER BY updated_at DESC, id LIMIT 100'),
                      {'o': owner_id, 'r': repository_id}).all()
    return [target_view(db, r) for r in rows]


def get_target(db: Session, owner_id: UUID, target_id: UUID) -> dict:
    return target_view(db, _target_row(db, target_id, owner_id))


def patch_target(db: Session, owner_id: UUID, target_id: UUID, body: HandoverTargetPatch, *, request_id: str = '-') -> dict:
    row = _target_row(db, target_id, owner_id, lock=True)
    if body.expected_version != row.version:
        raise _conflict(db, '다른 곳에서 인계 대상이 바뀌었습니다. 최신 내용을 확인해 주세요.', reason='TARGET_VERSION', current_version=row.version)
    changes, scope_changed = {}, False
    if body.name is not None:
        name = _clean_memo(body.name, 120, what='이름')
        if not name:
            db.rollback()
            raise AIError(ErrorCode.INVALID_SELECTION, '이름을 입력해 주세요.')
        changes['name'] = name
    for field, limit, what in (('purpose', 500, '목적'), ('giver_name', 100, '인계자'), ('receiver_name', 100, '인수자')):
        value = getattr(body, field)
        if value is not None:
            changes[field] = _clean_memo(value, limit, what=what)
    if body.selected_paths is not None:
        paths = normalize_paths(db, body.selected_paths)
        if paths != list(row.selected_paths):
            changes['selected_paths'], scope_changed = paths, True
    if body.clear_snapshot and body.snapshot_id is not None:
        db.rollback()
        raise AIError(ErrorCode.INVALID_SELECTION, 'snapshot을 지정하면서 해제할 수 없습니다.')
    new_snapshot = None if body.clear_snapshot else (body.snapshot_id if body.snapshot_id is not None else row.snapshot_id)
    if new_snapshot != row.snapshot_id:
        _check_snapshot(db, row.repository_id, new_snapshot)
        changes['snapshot_id'], scope_changed = new_snapshot, True
    changes = {k: v for k, v in changes.items() if v != (list(row.selected_paths) if k == 'selected_paths' else getattr(row, k))}
    if not changes:
        db.rollback()
        return target_view(db, row)
    sets = ', '.join(f'{k} = CAST(:{k} AS jsonb)' if k == 'selected_paths' else f'{k} = :{k}' for k in changes)
    params = {k: (json.dumps(v) if k == 'selected_paths' else v) for k, v in changes.items()}
    updated = db.execute(text(f"""UPDATE handover_targets SET {sets}, version = version + 1,
                                  scope_version = CASE WHEN :scope THEN version + 1 ELSE scope_version END, updated_at = now()
                                  WHERE id = :t RETURNING *"""), {**params, 'scope': scope_changed, 't': target_id}).one()
    _audit(db, owner_id, row.repository_id, 'handover.target_updated', target_id, request_id, {'fields': sorted(changes), 'version': updated.version})
    db.commit()
    return target_view(db, updated)


def delete_target(db: Session, owner_id: UUID, target_id: UUID, *, request_id: str = '-') -> None:
    row = _target_row(db, target_id, owner_id, lock=True)
    db.execute(text('DELETE FROM handover_targets WHERE id = :t'), {'t': target_id})
    _audit(db, owner_id, row.repository_id, 'handover.target_deleted', target_id, request_id, {})
    db.commit()


# ── 준비 체크폼 ─────────────────────────────────────────────────────────

def _empty_item() -> dict:
    return {'status': 'unknown', 'text': None, 'link': None, 'updated_version': None, 'updated_at': None}


def _normalize_item(key: str, raw) -> dict:
    status = raw.status if raw is not None else 'unknown'
    value = (raw.text.strip() if raw is not None and raw.text else '') or None
    link = (raw.link.strip() if raw is not None and raw.link else '') or None
    label = handover_draft.label_of(key)
    for v, what in ((value, '내용'), (link, '링크')):
        if v and CONTROL_RE.search(v):
            raise AIError(ErrorCode.INVALID_SELECTION, f'{label}의 {what}에 쓸 수 없는 문자가 있습니다.', details={'item': key})
    if link and not LINK_RE.match(link):
        raise AIError(ErrorCode.INVALID_SELECTION, f'{label}의 링크는 http 또는 https 주소여야 합니다.', details={'item': key})
    if status == 'unknown' and (value or link):
        raise AIError(ErrorCode.INVALID_SELECTION, f'{label}: 내용이나 링크가 있으면 상태를 "준비 필요" 또는 "준비 완료"로 정해 주세요. 미확인 항목에는 내용을 둘 수 없습니다.',
                      details={'item': key})
    if status == 'ready':
        need = REQUIRED_INFO[key]
        if (need == 'text' and not value) or (need == 'text_or_link' and not (value or link)):
            raise AIError(ErrorCode.INVALID_SELECTION, f'{label}: 준비 완료로 기록하려면 필요한 내용을 먼저 입력해 주세요.', details={'item': key})
    return {'status': status, 'text': value, 'link': link}


def preparation_view(db: Session, target, prep) -> dict:
    items_raw = (prep.items if prep else {}) or {}
    items = []
    for key in HANDOVER_ITEM_KEYS:
        item = {**_empty_item(), **(items_raw.get(key) or {})}
        items.append({'key': key, 'label': handover_draft.label_of(key), 'status': item['status'], 'status_label': handover_draft.status_label(item['status']),
                      'text': item.get('text'), 'link': item.get('link'), 'updated_version': item.get('updated_version'),
                      'updated_at': item.get('updated_at')})
    return {'target_id': target.id, 'version': prep.version if prep else 0, 'target_version': prep.target_version if prep else None,
            'needs_reconfirm': bool(prep and prep.target_version < target.scope_version), 'items': items, 'created_at': prep.created_at if prep else None}


def get_preparation(db: Session, owner_id: UUID, target_id: UUID) -> dict:
    target = _target_row(db, target_id, owner_id)
    return preparation_view(db, target, _latest_prep(db, target_id))


def list_preparation_versions(db: Session, owner_id: UUID, target_id: UUID) -> list[dict]:
    target = _target_row(db, target_id, owner_id)
    rows = db.execute(text('SELECT * FROM handover_preparation_versions WHERE target_id = :t ORDER BY version DESC LIMIT 100'), {'t': target_id}).all()
    return [preparation_view(db, target, r) for r in rows]


def put_preparation(db: Session, owner_id: UUID, target_id: UUID, body: HandoverPreparationPut, *, request_id: str = '-') -> dict:
    target = _target_row(db, target_id, owner_id, lock=True)
    unknown_keys = set(body.items) - set(HANDOVER_ITEM_KEYS)
    if unknown_keys:
        db.rollback()
        raise AIError(ErrorCode.INVALID_SELECTION, '알 수 없는 준비 항목이 있습니다.', details={'items': sorted(unknown_keys)})
    if body.confirmed_target_version != target.version:
        raise _conflict(db, '인계 대상이 바뀌었습니다. 대상 정보를 다시 확인한 뒤 준비 정보를 저장해 주세요.', reason='TARGET_CHANGED', current_target_version=target.version)
    latest = _latest_prep(db, target_id)
    current = latest.version if latest else 0
    if body.expected_version != current:
        raise _conflict(db, '다른 곳에서 준비 정보가 바뀌었습니다. 최신 내용을 확인해 주세요.', reason='PREPARATION_VERSION', current_version=current)
    if current >= MAX_PREPARATION_VERSIONS:
        db.rollback()
        raise AIError(ErrorCode.USER_LIMIT, '준비 정보 저장 횟수 한도에 도달했습니다.', retryable=False)
    try:
        normalized = {key: _normalize_item(key, body.items.get(key)) for key in HANDOVER_ITEM_KEYS}      # 보내지 않은 항목은 미확인
    except AIError:
        db.rollback()
        raise
    now = _now()
    previous = (latest.items if latest else {}) or {}
    new_version = current + 1
    for key, item in normalized.items():
        before = previous.get(key) or _empty_item()
        same = all(before.get(f) == item[f] for f in ('status', 'text', 'link'))
        item['updated_version'] = before.get('updated_version') if same else new_version
        item['updated_at'] = before.get('updated_at') if same else now.isoformat()
        if same and item['updated_version'] is None:
            item['updated_version'] = None
    try:
        row = db.execute(text("""INSERT INTO handover_preparation_versions (target_id, owner_user_id, repository_id, version, target_version, items, author_user_id)
                                 VALUES (:t, :o, :r, :v, :tv, CAST(:items AS jsonb), :o) RETURNING *"""),
                         {'t': target_id, 'o': owner_id, 'r': target.repository_id, 'v': new_version, 'tv': target.version,
                          'items': json.dumps(normalized)}).one()
    except IntegrityError:
        db.rollback()
        raise AIError(ErrorCode.VERSION_CONFLICT, '다른 곳에서 준비 정보가 바뀌었습니다. 최신 내용을 확인해 주세요.', details={'reason': 'PREPARATION_VERSION'})
    counts = {s: sum(1 for i in normalized.values() if i['status'] == s) for s in ('unknown', 'needed', 'ready')}
    _audit(db, owner_id, target.repository_id, 'handover.preparation_saved', target_id, request_id, {'version': new_version, **counts})
    db.commit()
    return preparation_view(db, target, row)


# ── 근거 자료 ───────────────────────────────────────────────────────────

def _source_result(db: Session, owner_id: UUID, repository_id: int, result_id: UUID, kind: str, now: datetime):
    row = db.execute(text("""
        SELECT r.id, r.owner_user_id, r.repository_id, r.payload, r.payload_hash, r.expires_at, j.kind, j.input_id, i.manifest_hash,
               rev.pr_number, rev.base_sha, rev.head_sha
        FROM ai_results r JOIN ai_jobs j ON j.id = r.job_id AND j.deleted_at IS NULL
        JOIN ai_inputs i ON i.id = j.input_id JOIN pr_revisions rev ON rev.id = i.revision_id WHERE r.id = :r"""), {'r': result_id}).one_or_none()
    if row is None:
        raise AIError(ErrorCode.RESOURCE_NOT_FOUND, '결과를 찾을 수 없습니다.')
    ensure_owner(row.owner_user_id, owner_id)
    if row.repository_id != repository_id:
        raise AIError(ErrorCode.INVALID_SELECTION, '인계 대상과 같은 저장소의 결과만 쓸 수 있습니다.')
    if row.kind != kind:
        raise AIError(ErrorCode.INVALID_SELECTION, f'{"변경 설명" if kind == "summary" else "코드 리뷰"} 결과가 아닙니다.')
    if row.expires_at <= now:
        raise AIError(ErrorCode.RESULT_EXPIRED, '결과의 보관 기간이 지났습니다.')
    return row


def _pr_pin(row) -> dict:
    return {'number': row.pr_number, 'base_sha': row.base_sha, 'head_sha': row.head_sha, 'input_id': str(row.input_id), 'manifest_hash': (row.manifest_hash or '').strip()}


def feedback_basis(db: Session, owner_id: UUID, result_id: UUID, paths: list[str]) -> tuple[list[dict], int, str]:
    """대상 파일에 해당하는 지적과 이 사용자의 피드백(판정·버전). (지적 목록, 대상 밖 지적 수, 피드백 기준 digest)."""
    rows = db.execute(text("""
        SELECT f.id, f.ordinal, f.category, f.priority, f.title, f.detail->'location'->>'path' AS path, f.start_line, f.end_line, ff.verdict, ff.version
        FROM ai_findings f LEFT JOIN finding_feedback ff ON ff.finding_id = f.id AND ff.author_user_id = :o
        WHERE f.result_id = :r ORDER BY f.ordinal"""), {'o': owner_id, 'r': result_id}).all()
    wanted = set(paths)
    mine = [{'finding_id': str(r.id), 'ordinal': r.ordinal, 'title': r.title, 'category': r.category, 'priority': r.priority, 'path': r.path,
             'start_line': r.start_line, 'end_line': r.end_line, 'verdict': r.verdict or 'unverified', 'feedback_version': r.version or 0}
            for r in rows if r.path in wanted]
    digest = canonical_hash([[f['finding_id'], f['verdict'], f['feedback_version']] for f in mine])
    return mine, len(rows) - len(mine), digest


def _summary_context(row, paths: list[str]) -> dict:
    payload = row.payload or {}
    changes = [c for c in (payload.get('file_changes') or []) if c.get('path') in set(paths)]
    return {'result_id': str(row.id), 'payload_hash': row.payload_hash.strip(), 'pr': _pr_pin(row), 'overview': payload.get('overview') or '',
            'author_intent': payload.get('author_intent'), 'unknowns': list(payload.get('unknowns') or [])[:10],
            'file_changes': [{'path': c['path'], 'summary': c.get('summary') or ''} for c in changes],
            'omitted_file_changes': max(len(payload.get('file_changes') or []) - len(changes), 0)}


def _history_context(db: Session, target, now: datetime) -> dict | None:
    if target.snapshot_id is None:
        return None
    snapshot = db.get(AnalysisSnapshot, target.snapshot_id)
    if snapshot is None or snapshot.repository_id != target.repository_id:
        return {'status': 'excluded', 'reasons': ['payload_incomplete'], 'reason_texts': [history_context.REASON_TEXT['payload_incomplete']]}
    reasons = history_context.snapshot_eligibility(db, snapshot, as_of=now)
    if reasons:
        return {'status': 'excluded', 'snapshot_id': snapshot.id, 'reasons': reasons, 'reason_texts': [history_context.REASON_TEXT.get(r, r) for r in reasons]}
    return history_context.snapshot_paths_evidence(snapshot, list(target.selected_paths))


def generation_view(row, *, stale: list[str] | None = None, reused: bool = False) -> dict:
    return {'generation_id': row.id, 'target_id': row.target_id, 'number': row.number, 'target_version': row.target_version,
            'preparation_version': row.preparation_version, 'template_version': row.template_version, 'sources': row.sources,
            'stale_reasons': stale or [], 'markdown': row.markdown, 'created_at': row.created_at, 'reused': reused}


def stale_reasons(db: Session, owner_id: UUID, target, generation) -> list[str]:
    """생성 원본이 고정한 기준이 지금과 달라졌는지. 원본 자체는 바뀌지 않는다."""
    reasons = []
    if target.version != generation.target_version:
        reasons.append('target_changed')
    prep = _latest_prep(db, target.id)
    if (prep.version if prep else 0) != generation.preparation_version:
        reasons.append('preparation_changed')
    review = (generation.sources or {}).get('review')
    now = _now()
    if review:
        row = db.execute(text("""SELECT r.expires_at FROM ai_results r JOIN ai_jobs j ON j.id = r.job_id AND j.deleted_at IS NULL WHERE r.id = :r"""),
                         {'r': review['result_id']}).one_or_none()
        if row is None or row.expires_at <= now:
            reasons.append('source_missing')
        else:
            _, _, digest = feedback_basis(db, owner_id, UUID(review['result_id']), list(generation.sources['target']['selected_paths']))
            if digest != review['feedback_digest']:
                reasons.append('feedback_changed')
    summary = (generation.sources or {}).get('summary')
    if summary:
        row = db.execute(text("""SELECT r.expires_at FROM ai_results r JOIN ai_jobs j ON j.id = r.job_id AND j.deleted_at IS NULL WHERE r.id = :r"""),
                         {'r': summary['result_id']}).one_or_none()
        if (row is None or row.expires_at <= now) and 'source_missing' not in reasons:
            reasons.append('source_missing')
    return reasons


# ── 초안 생성 ───────────────────────────────────────────────────────────

def generate(db: Session, owner_id: UUID, target_id: UUID, body: HandoverGenerateRequest, *, request_id: str = '-') -> dict:
    target = _target_row(db, target_id, owner_id, lock=True)                    # 같은 대상의 생성·편집을 직렬화
    if body.expected_target_version != target.version:
        raise _conflict(db, '인계 대상이 바뀌었습니다. 최신 내용을 확인한 뒤 다시 생성해 주세요.', reason='TARGET_CHANGED', current_target_version=target.version)
    prep = _latest_prep(db, target_id)
    prep_version = prep.version if prep else 0
    if body.expected_preparation_version != prep_version:
        raise _conflict(db, '준비 정보가 바뀌었습니다. 최신 내용을 확인한 뒤 다시 생성해 주세요.', reason='PREPARATION_CHANGED', current_version=prep_version)
    now = _now()
    paths = list(target.selected_paths)
    try:
        summary_row = _source_result(db, owner_id, target.repository_id, body.summary_result_id, 'summary', now) if body.summary_result_id else None
        review_row = _source_result(db, owner_id, target.repository_id, body.review_result_id, 'review', now) if body.review_result_id else None
    except AIError:
        db.rollback()
        raise
    summary_ctx = _summary_context(summary_row, paths) if summary_row else None
    review_ctx = None
    if review_row:
        findings, omitted, digest = feedback_basis(db, owner_id, review_row.id, paths)
        review_ctx = {'result_id': str(review_row.id), 'payload_hash': review_row.payload_hash.strip(), 'pr': _pr_pin(review_row), 'findings': findings,
                      'omitted': omitted, 'feedback_digest': digest}
    history = _history_context(db, target, now)
    view = preparation_view(db, target, prep)
    prep_ctx = {'version': view['version'], 'target_version': view['target_version'], 'needs_reconfirm': view['needs_reconfirm'],
                'items': {i['key']: {'status': i['status'], 'text': i['text'], 'link': i['link']} for i in view['items']}}
    sources = {'target': {'id': str(target.id), 'version': target.version, 'scope_version': target.scope_version, 'selected_paths': paths},
               'preparation': {'version': prep_version, 'needs_reconfirm': prep_ctx['needs_reconfirm']},
               'summary': {k: summary_ctx[k] for k in ('result_id', 'payload_hash', 'pr')} if summary_ctx else None,
               'review': {k: review_ctx[k] for k in ('result_id', 'payload_hash', 'pr', 'feedback_digest')} if review_ctx else None,
               'history': ({'snapshot_id': target.snapshot_id, 'status': history['status'], 'reasons': history.get('reasons', []),
                            'snapshot_sha256': (history.get('basis') or {}).get('payload_sha256')} if history else None),
               'template_version': handover_draft.TEMPLATE_VERSION}
    fingerprint = canonical_hash(sources)
    latest = db.execute(text('SELECT * FROM handover_generations WHERE target_id = :t ORDER BY number DESC LIMIT 1'), {'t': target_id}).one_or_none()
    document = db.execute(text('SELECT * FROM handover_documents WHERE target_id = :t FOR UPDATE'), {'t': target_id}).one_or_none()
    if latest is not None and latest.fingerprint.strip() == fingerprint and not body.force:
        db.rollback()
        return {'generation': generation_view(latest, stale=[], reused=True), 'document_id': document.id if document else None, 'document_updated': False}
    count = latest.number if latest else 0
    if count >= MAX_GENERATIONS_PER_TARGET:
        db.rollback()
        raise AIError(ErrorCode.USER_LIMIT, f'초안은 대상마다 {MAX_GENERATIONS_PER_TARGET}번까지 만들 수 있습니다.', retryable=False)
    ctx = {'target': {'name': target.name, 'purpose': target.purpose, 'giver_name': target.giver_name, 'receiver_name': target.receiver_name,
                      'selected_paths': paths, 'version': target.version},
           'preparation': prep_ctx, 'history': history, 'summary': summary_ctx, 'review': review_ctx, 'generated_at': now.strftime('%Y-%m-%d %H:%M UTC')}
    body_json, markdown = handover_draft.render(ctx)
    issues = markdown_safety.validate_markdown(markdown)
    if issues:                                                                # 렌더러가 만든 문서는 항상 안전해야 한다. 아니면 저장하지 않는다.
        db.rollback()
        raise AIError(ErrorCode.INVALID_STATE, '초안을 안전하게 만들 수 없습니다.', details={'issues': issues})
    row = db.execute(text("""INSERT INTO handover_generations (target_id, owner_user_id, repository_id, number, fingerprint, target_version, preparation_version,
                                 sources, body, markdown, body_hash, template_version)
                             VALUES (:t, :o, :r, :n, :f, :tv, :pv, CAST(:s AS jsonb), CAST(:b AS jsonb), :m, :h, :tpl) RETURNING *"""),
                     {'t': target_id, 'o': owner_id, 'r': target.repository_id, 'n': count + 1, 'f': fingerprint, 'tv': target.version, 'pv': prep_version,
                      's': json.dumps(sources), 'b': json.dumps(body_json), 'm': markdown, 'h': canonical_hash(body_json), 'tpl': handover_draft.TEMPLATE_VERSION}).one()
    document_updated = False
    if document is None:
        document = db.execute(text("""INSERT INTO handover_documents (target_id, owner_user_id, repository_id, base_generation_id, markdown)
                                      VALUES (:t, :o, :r, :g, :m) RETURNING *"""),
                              {'t': target_id, 'o': owner_id, 'r': target.repository_id, 'g': row.id, 'm': markdown}).one()
        _add_document_version(db, document.id, 1, 'generated', markdown, row.id, owner_id)
        document_updated = True
    elif not document.edited:                                                 # 편집하지 않은 문서만 새 원본을 따라간다
        document = db.execute(text("""UPDATE handover_documents SET base_generation_id = :g, markdown = :m, version = version + 1, updated_at = now()
                                      WHERE id = :d RETURNING *"""), {'g': row.id, 'm': markdown, 'd': document.id}).one()
        _add_document_version(db, document.id, document.version, 'generated', markdown, row.id, owner_id)
        document_updated = True
    _audit(db, owner_id, target.repository_id, 'handover.generated', row.id, request_id,
           {'number': row.number, 'target_version': target.version, 'preparation_version': prep_version, 'force': body.force,
            'has_summary': bool(summary_row), 'has_review': bool(review_row), 'document_updated': document_updated})
    db.commit()
    return {'generation': generation_view(row, stale=[]), 'document_id': document.id, 'document_updated': document_updated}


def list_generations(db: Session, owner_id: UUID, target_id: UUID) -> list[dict]:
    target = _target_row(db, target_id, owner_id)
    rows = db.execute(text('SELECT * FROM handover_generations WHERE target_id = :t ORDER BY number DESC LIMIT 50'), {'t': target_id}).all()
    return [generation_view(r, stale=stale_reasons(db, owner_id, target, r)) for r in rows]


def get_generation(db: Session, owner_id: UUID, generation_id: UUID) -> tuple[dict, object]:
    row = db.execute(text('SELECT * FROM handover_generations WHERE id = :g'), {'g': generation_id}).one_or_none()
    if row is None:
        raise AIError(ErrorCode.RESOURCE_NOT_FOUND, '초안을 찾을 수 없습니다.')
    ensure_owner(row.owner_user_id, owner_id)
    target = _target_row(db, row.target_id, owner_id)
    return generation_view(row, stale=stale_reasons(db, owner_id, target, row)), target


# ── 편집 문서 ───────────────────────────────────────────────────────────

def _add_document_version(db: Session, document_id: UUID, version: int, kind: str, markdown: str, generation_id: UUID, author_id: UUID) -> None:
    db.execute(text("""INSERT INTO handover_document_versions (document_id, version, kind, markdown, base_generation_id, author_user_id)
                       VALUES (:d, :v, :k, :m, :g, :a)"""), {'d': document_id, 'v': version, 'k': kind, 'm': markdown, 'g': generation_id, 'a': author_id})
    db.execute(text("""DELETE FROM handover_document_versions WHERE document_id = :d AND version <= :cut"""),
               {'d': document_id, 'cut': version - MAX_DOCUMENT_VERSIONS})                    # 오래된 편집 이력만 정리한다(현재 문서는 handover_documents에 있다)


def _document_row(db: Session, document_id: UUID, owner_id: UUID, *, lock: bool = False):
    row = db.execute(text('SELECT * FROM handover_documents WHERE id = :d' + (' FOR UPDATE' if lock else '')), {'d': document_id}).one_or_none()
    try:
        if row is None:
            raise AIError(ErrorCode.RESOURCE_NOT_FOUND, '문서를 찾을 수 없습니다.')
        ensure_owner(row.owner_user_id, owner_id)
    except AIError:
        db.rollback()
        raise
    return row


def document_view(db: Session, owner_id: UUID, row) -> dict:
    target = _target_row(db, row.target_id, owner_id)
    base = db.execute(text('SELECT * FROM handover_generations WHERE id = :g'), {'g': row.base_generation_id}).one()
    newest = db.execute(text('SELECT id, number FROM handover_generations WHERE target_id = :t ORDER BY number DESC LIMIT 1'), {'t': row.target_id}).one()
    newer = newest if newest.number > base.number else None
    return {'document_id': row.id, 'target_id': row.target_id, 'version': row.version, 'edited': row.edited, 'markdown': row.markdown,
            'base_generation_id': base.id, 'base_generation_number': base.number, 'stale_reasons': stale_reasons(db, owner_id, target, base),
            'newer_generation_id': newer.id if newer else None, 'newer_generation_number': newer.number if newer else None, 'updated_at': row.updated_at}


def get_document(db: Session, owner_id: UUID, target_id: UUID) -> dict:
    target = _target_row(db, target_id, owner_id)
    row = db.execute(text('SELECT * FROM handover_documents WHERE target_id = :t'), {'t': target.id}).one_or_none()
    if row is None:
        raise AIError(ErrorCode.RESOURCE_NOT_FOUND, '아직 만든 인수인계 문서가 없습니다. 먼저 초안을 생성해 주세요.')
    return document_view(db, owner_id, row)


def get_document_by_id(db: Session, owner_id: UUID, document_id: UUID) -> dict:
    return document_view(db, owner_id, _document_row(db, document_id, owner_id))


def put_document(db: Session, owner_id: UUID, document_id: UUID, body: HandoverDocumentPut, *, request_id: str = '-') -> dict:
    row = _document_row(db, document_id, owner_id, lock=True)
    if body.expected_version != row.version:
        raise _conflict(db, '다른 곳에서 문서가 바뀌었습니다. 최신 내용을 확인해 주세요.', reason='DOCUMENT_VERSION', current_version=row.version)
    markdown = body.markdown.replace('\r\n', '\n').replace('\r', '\n')
    issues = markdown_safety.validate_markdown(markdown)
    if issues:
        db.rollback()
        raise AIError(ErrorCode.INVALID_SELECTION, '문서에 저장할 수 없는 내용이 있습니다: ' + ' '.join(markdown_safety.ISSUE_TEXT[i] for i in issues),
                      details={'issues': issues})
    if markdown == row.markdown:
        db.rollback()
        return document_view(db, owner_id, row)
    updated = db.execute(text("""UPDATE handover_documents SET markdown = :m, version = version + 1, edited = TRUE, updated_at = now()
                                 WHERE id = :d RETURNING *"""), {'m': markdown, 'd': document_id}).one()
    _add_document_version(db, document_id, updated.version, 'edited', markdown, updated.base_generation_id, owner_id)
    target = _target_row(db, updated.target_id, owner_id)
    _audit(db, owner_id, target.repository_id, 'handover.document_edited', document_id, request_id, {'version': updated.version, 'chars': len(markdown)})
    db.commit()
    return document_view(db, owner_id, updated)


def adopt_generation(db: Session, owner_id: UUID, document_id: UUID, body: HandoverAdoptRequest, *, request_id: str = '-') -> dict:
    """더 새로운 생성 원본으로 문서를 바꾼다. 사용자가 명시적으로 요청할 때만 하며 이전 편집본은 편집 이력에 남는다."""
    row = _document_row(db, document_id, owner_id, lock=True)
    if body.expected_version != row.version:
        raise _conflict(db, '다른 곳에서 문서가 바뀌었습니다. 최신 내용을 확인해 주세요.', reason='DOCUMENT_VERSION', current_version=row.version)
    gen = db.execute(text('SELECT * FROM handover_generations WHERE id = :g AND target_id = :t'), {'g': body.generation_id, 't': row.target_id}).one_or_none()
    if gen is None:
        db.rollback()
        raise AIError(ErrorCode.RESOURCE_NOT_FOUND, '초안을 찾을 수 없습니다.')
    updated = db.execute(text("""UPDATE handover_documents SET markdown = :m, base_generation_id = :g, version = version + 1, edited = FALSE, updated_at = now()
                                 WHERE id = :d RETURNING *"""), {'m': gen.markdown, 'g': gen.id, 'd': document_id}).one()
    _add_document_version(db, document_id, updated.version, 'adopted', gen.markdown, gen.id, owner_id)
    target = _target_row(db, updated.target_id, owner_id)
    _audit(db, owner_id, target.repository_id, 'handover.generation_adopted', document_id, request_id, {'version': updated.version, 'generation': gen.number})
    db.commit()
    return document_view(db, owner_id, updated)


def export_markdown(db: Session, owner_id: UUID, document_id: UUID, *, request_id: str = '-') -> tuple[str, str]:
    """(파일 이름, 내용). 내보내기 직전에 다시 검증하고, 기준 정보와 주의 문구를 덧붙인다."""
    row = _document_row(db, document_id, owner_id)
    issues = markdown_safety.validate_markdown(row.markdown)
    if issues:
        db.rollback()
        raise AIError(ErrorCode.INVALID_STATE, '문서에 내보낼 수 없는 내용이 있습니다. 수정한 뒤 다시 시도해 주세요: ' + ' '.join(markdown_safety.ISSUE_TEXT[i] for i in issues),
                      details={'issues': issues})
    base = db.execute(text('SELECT number FROM handover_generations WHERE id = :g'), {'g': row.base_generation_id}).one()
    content = row.markdown.rstrip('\n') + '\n' + handover_draft.export_footer(document_id=str(row.id), version=row.version, generation_number=base.number)
    target = _target_row(db, row.target_id, owner_id)
    _audit(db, owner_id, target.repository_id, 'handover.document_exported', document_id, request_id, {'version': row.version, 'bytes': len(content.encode('utf-8'))})
    db.commit()
    return f'handover-{str(row.id)[:8]}-v{row.version}.md', content
