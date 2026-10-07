"""GitViz 이력 근거: 리뷰 결과의 코드 지적 경로에 대해 분석 snapshot의 이력(공동 변경·기여자 분포·관측 수·수집 품질)을 붙인다.

원칙
- **저장된 값만 쓴다.** 숫자는 불변 snapshot payload에서 그대로 옮긴다. 지표를 다시 계산하지 않고, 원본 DB·모델·외부 호출을 쓰지 않는다
  (AI가 만든 값이 아니며 전송도 일어나지 않는다).
- **검증하지 못한 snapshot은 근거로 쓰지 않는다.** 해시·저장 형식·계산 버전·기준 SHA·저장소 식별·구조 일관성·시점(미래 자료)을
  확인해 하나라도 어긋나면 이유와 함께 `excluded`로 기록하고 값은 싣지 않는다.
- **코드 지적과 분리한다.** 별도 테이블(ai_evidence)이며 결과·지적은 바꾸지 않는다. 공동 변경 파일은 "확인 후보"일 뿐
  "이 PR에서 누락·오류"라고 단정하지 않는다. 미식별 분모·표본 부족·이력 없는 새 파일·이름 변경·PR 기준과 snapshot 기준의 차이를
  숨기지 않고 표시한다.
- 이력이 없어도(snapshot이 없거나 제외돼도) MVP 리뷰는 그대로 동작한다.
"""
import hashlib
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.ai_evidence import AIEvidence
from app.models.analysis_snapshot import AnalysisSnapshot
from app.schemas.ai import ErrorCode, canonical_hash
from app.services.ai.access import AIError
from app.services.analysis_snapshots import payload_digest
from app.services.metric_policy import FORMULA_VERSION, LOW_SUPPORT_THRESHOLD

logger = logging.getLogger('gitviz.ai.history')

HISTORY_TEXT_VERSION = 'history-v1'
SCHEMA_VERSION = 'history-evidence-v1'
TEXT_FILE = Path(__file__).parent / 'prompts' / 'history_v1.txt'
TEXT_DIGEST = 'd0a42855832c0df57f80b834477fd11a222795250ec26b911a5dda7245531d29'   # 테스트가 현재 파일과 대조한다(내용을 바꾸면 새 버전 파일을 만든다)
MAX_CO_CHANGE = 5
MAX_TOP_SHARES = 3
HASH_FORMAT = 'canonical-json-numbers-v1'
SHA_RE = re.compile(r'^[0-9a-f]{40}$')
REQUIRED_PAYLOAD_KEYS = ('summary', 'tree', 'coupling', 'ownership', 'risk-scores', 'file_index', 'data_quality')

REASON_TEXT = {
    'integrity_failed': '저장된 결과의 해시가 맞지 않습니다.',
    'legacy_format': '이전 저장 형식의 결과입니다(해시 형식이 갱신되지 않았습니다).',
    'formula_unsupported': '지원하지 않는 계산 버전(이전 정책)의 결과입니다.',
    'target_sha_missing': '분석 기준 SHA를 확인할 수 없습니다.',
    'repository_identity_missing': '결과에 저장소 식별 정보(id·url)가 없습니다.',
    'repository_identity_mismatch': '결과의 저장소 식별 정보가 이 저장소와 맞지 않습니다.',
    'payload_incomplete': '결과에 필요한 항목이 없거나 구조가 일관되지 않습니다.',
    'future_data': '리뷰 결과가 만들어진 뒤(또는 지정한 평가 시점 이후)의 이력이 포함돼 있어 쓸 수 없습니다.',
}


def load_texts() -> dict[str, str]:
    out = {}
    for line in TEXT_FILE.read_text(encoding='utf-8').splitlines():
        if line.startswith('#') or ':' not in line:
            continue
        key, _, value = line.partition(':')
        out[key.strip()] = value.strip()
    return out


def text_digest() -> str:
    return hashlib.sha256(TEXT_FILE.read_bytes()).hexdigest()


TEXTS = load_texts()


def _canonical_url(url: str | None) -> str:
    return (url or '').strip().lower().removesuffix('.git').rstrip('/')


# ── snapshot 검증 ────────────────────────────────────────────────────────

def snapshot_eligibility(db: Session, snapshot: AnalysisSnapshot, *, as_of: datetime | None = None) -> list[str]:
    """이력 근거로 쓸 수 없는 이유 코드 목록. 비어 있으면 검증을 통과한 snapshot이다. 값을 다시 계산하지 않는다."""
    reasons = []
    payload = snapshot.payload if isinstance(snapshot.payload, dict) else {}
    if (snapshot.parameters or {}).get('hash_format') != HASH_FORMAT:
        reasons.append('legacy_format')
    elif payload_digest(snapshot.payload) != snapshot.payload_sha256:
        reasons.append('integrity_failed')
    if snapshot.formula_version != FORMULA_VERSION:
        reasons.append('formula_unsupported')
    summary_repo = ((payload.get('summary') or {}).get('repository') or {})
    if not SHA_RE.match(snapshot.target_sha or '') or summary_repo.get('target_sha') != snapshot.target_sha:
        reasons.append('target_sha_missing')
    row = db.execute(text('SELECT id, url FROM repositories WHERE id = :i'), {'i': snapshot.repository_id}).one_or_none()
    if summary_repo.get('id') is None or not summary_repo.get('url'):
        reasons.append('repository_identity_missing')
    elif row is None or summary_repo['id'] != row.id or _canonical_url(summary_repo['url']) != _canonical_url(row.url):
        reasons.append('repository_identity_mismatch')
    tree_files = ((payload.get('tree') or {}).get('files'))
    quality = payload.get('data_quality') or {}
    if (any(k not in payload for k in REQUIRED_PAYLOAD_KEYS) or not isinstance(tree_files, list)
            or quality.get('collected_target_file_count') != len(tree_files or [])):
        reasons.append('payload_incomplete')
    if as_of is not None and snapshot.window_end is not None and snapshot.window_end > as_of:
        reasons.append('future_data')
    return reasons


# ── 근거 만들기 ──────────────────────────────────────────────────────────

def _result_context(db: Session, result_id: UUID):
    row = db.execute(text("""
        SELECT res.id, res.owner_user_id, res.repository_id, res.created_at, j.kind, i.manifest, r.base_sha, r.head_sha, r.merge_base_sha
        FROM ai_results res JOIN ai_jobs j ON j.id = res.job_id JOIN ai_inputs i ON i.id = j.input_id
        JOIN pr_revisions r ON r.id = i.revision_id WHERE res.id = :r AND j.deleted_at IS NULL"""), {'r': result_id}).one_or_none()
    if row is None:
        raise AIError(ErrorCode.RESOURCE_NOT_FOUND, '결과를 찾을 수 없습니다.')
    return row


def _finding_paths(db: Session, result_id: UUID) -> list[str]:
    rows = db.execute(text("""SELECT detail->'location'->>'path' AS path FROM ai_findings WHERE result_id = :r ORDER BY ordinal"""),
                      {'r': result_id}).all()
    seen, out = set(), []
    for r in rows:
        if r.path and r.path not in seen:
            seen.add(r.path)
            out.append(r.path)
    return out


def _r(value, digits=4):
    return round(value, digits) if isinstance(value, (int, float)) and not isinstance(value, bool) else value


def _path_evidence(path: str, entry: dict | None, payload: dict, by_path: dict, pr_paths: set[str]) -> dict:
    from app.api.analytics import _coupled_files_for
    from app.services.analysis_snapshots import file_metric_fields
    old_path = (entry or {}).get('old_path')
    fid, match, limits = by_path.get(path), 'path', []
    if fid is None and old_path and old_path in by_path:
        fid, match = by_path[old_path], 'old_path'
        limits.append(TEXTS['RENAMED'])
    if fid is None:
        added = (entry or {}).get('change_type') == 'added'
        return {'path': path, 'matched_path': None, 'match': 'none', 'status': 'no_history' if added else 'not_in_snapshot', 'file_id': None,
                'observations': None, 'ownership': None, 'co_change': [], 'co_change_total': 0,
                'limitations': [TEXTS['NEW_FILE'] if added else TEXTS['NOT_IN_SNAPSHOT']]}
    metrics = file_metric_fields(payload, fid)
    own = payload['ownership'].get(str(fid)) or {}
    observed = own.get('observation_count', 0) or 0
    observations = {k: _r(metrics.get(k)) for k in ('change_freq', 'churn', 'bugfix_count', 'unidentified_share', 'evidence_status', 'window_start', 'window_end')}
    ownership = {'observation_count': own.get('observation_count', 0), 'contributor_count': own.get('contributor_count', 0), 'top_owner_share': _r(own.get('top_owner_share')),
                 'bus_factor_80': own.get('bus_factor'), 'entropy_normalized': _r(own.get('entropy_normalized')), 'low_support': own.get('low_support', True),
                 'top_shares': [{'name': s.get('name'), 'share': _r(s.get('share'))} for s in (own.get('shares') or [])[:MAX_TOP_SHARES]]}
    if ownership['low_support'] or metrics.get('evidence_status') == 'low_support':
        limits.append(TEXTS['LOW_SUPPORT'])
    if (metrics.get('unidentified_share') or 0) > 0:
        limits.append(TEXTS['UNIDENTIFIED'])
    coupled = _coupled_files_for({'pairs': payload['coupling']['pairs']}, fid, 'coupling')
    co_change = []
    for c in coupled[:MAX_CO_CHANGE]:
        co_change.append({'file_id': c['file_id'], 'path': c['path'], 'co_change_count': c['co_change_count'], 'coupling_score': _r(c['coupling_score']),
                          'selected_change_count': c['selected_change_count'], 'other_change_count': c['other_change_count'],
                          'selected_to_other_ratio': _r(c['selected_to_other_ratio']), 'other_to_selected_ratio': _r(c['other_to_selected_ratio']),
                          'low_support': c['low_support'], 'changed_in_pr': c['path'] in pr_paths})
    return {'path': path, 'matched_path': path if match == 'path' else old_path, 'match': match, 'file_id': fid,
            'status': 'observed' if (observed or metrics.get('evidence_status') in ('observed', 'low_support')) else 'no_observations', 'observations': observations, 'ownership': ownership,
            'co_change': co_change, 'co_change_total': len(coupled),
            'limitations': limits + ([TEXTS['NO_OBSERVATIONS']] if not (observed or metrics.get('evidence_status') in ('observed', 'low_support')) else [])}


def _build_payload(db: Session, ctx, snapshot: AnalysisSnapshot, paths: list[str]) -> dict:
    payload = snapshot.payload
    manifest_files = ((ctx.manifest or {}).get('files') or [])
    entries = {f['path']: f for f in manifest_files}
    pr_paths = {f['path'] for f in manifest_files} | {f['old_path'] for f in manifest_files if f.get('old_path')}
    by_path = {f['path']: f['id'] for f in payload['file_index']}
    evidence_paths = [_path_evidence(p, entries.get(p), payload, by_path, pr_paths) for p in paths]
    q = payload['data_quality']
    quality = {k: q.get(k) for k in ('commit_count', 'unidentified_commits', 'contributor_count', 'github_account_unlinked', 'merged_pr_count', 'prs_with_files',
                                    'prs_detail_complete', 'prs_detail_partial', 'prs_detail_failed', 'prs_detail_unknown', 'low_support_threshold',
                                    'target_tree_file_count', 'collected_target_file_count', 'coverage_scope')}
    meta = payload['coupling'].get('meta') or {}
    quality.update({'co_change_unit': meta.get('co_change_unit'), 'co_change_fallback_reason': meta.get('fallback_reason'),
                    'pr_file_coverage': meta.get('pr_file_coverage'), 'co_change_total_units': meta.get('total_units')})
    relation = 'same_as_pr_base' if snapshot.target_sha == ctx.base_sha else 'differs_from_pr_base'
    notes = [TEXTS['SEPARATE'], TEXTS['NOT_AN_ERROR'], TEXTS['BASIS_SAME'] if relation == 'same_as_pr_base' else TEXTS['BASIS_DIFFERS']]
    if not paths:
        notes.append(TEXTS['NO_FINDINGS'])
    return {
        'schema': SCHEMA_VERSION, 'text_version': HISTORY_TEXT_VERSION,
        'basis': {'snapshot_id': snapshot.id, 'target_sha': snapshot.target_sha, 'formula_version': snapshot.formula_version,
                  'window_start': snapshot.window_start.isoformat() if snapshot.window_start else None, 'window_end': snapshot.window_end.isoformat(),
                  'analyzed_at': snapshot.analyzed_at.isoformat(), 'payload_sha256': snapshot.payload_sha256,
                  'pr_base_sha': ctx.base_sha, 'pr_head_sha': ctx.head_sha, 'pr_merge_base_sha': ctx.merge_base_sha, 'relation': relation,
                  'low_support_threshold': LOW_SUPPORT_THRESHOLD},
        'paths': evidence_paths, 'data_quality': quality, 'notes': notes,
    }


def _excluded_payload(snapshot: AnalysisSnapshot, ctx, reasons: list[str]) -> dict:
    return {'schema': SCHEMA_VERSION, 'text_version': HISTORY_TEXT_VERSION,
            'basis': {'snapshot_id': snapshot.id, 'target_sha': snapshot.target_sha, 'formula_version': snapshot.formula_version,
                      'window_end': snapshot.window_end.isoformat() if snapshot.window_end else None,
                      'pr_base_sha': ctx.base_sha, 'pr_head_sha': ctx.head_sha},
            'paths': [], 'data_quality': {}, 'notes': [TEXTS['EXCLUDED'], *[REASON_TEXT.get(r, r) for r in reasons]]}


def build_history_evidence(db: Session, result_id: UUID, snapshot_id: int, *, as_of: datetime | None = None) -> AIEvidence:
    """리뷰 결과(`result_id`)와 분석 snapshot(`snapshot_id`)으로 이력 근거를 만들어 저장한다(같은 결과·snapshot은 한 번만, 불변).

    결과와 snapshot은 같은 저장소여야 한다(다르면 존재를 숨기고 RESOURCE_NOT_FOUND). 검증을 통과하지 못한 snapshot은
    이유와 함께 excluded로 기록한다. commit은 호출자가 한다."""
    ctx = _result_context(db, result_id)
    if ctx.kind != 'review':
        raise AIError(ErrorCode.INVALID_STATE, '이력 근거는 코드 리뷰 결과에만 붙일 수 있습니다.')
    snapshot = db.get(AnalysisSnapshot, snapshot_id)
    if snapshot is None or snapshot.repository_id != ctx.repository_id:
        raise AIError(ErrorCode.RESOURCE_NOT_FOUND, '분석 결과를 찾을 수 없습니다.')       # 다른 저장소의 snapshot이면 존재를 숨긴다
    existing = db.execute(select(AIEvidence).where(AIEvidence.result_id == result_id, AIEvidence.snapshot_id == snapshot_id,
                                                   AIEvidence.kind == 'history')).scalar_one_or_none()
    effective = ctx.created_at if as_of is None else min(ctx.created_at, as_of if as_of.tzinfo else as_of.replace(tzinfo=timezone.utc))
    reasons = snapshot_eligibility(db, snapshot, as_of=effective)
    if existing is not None:
        return _same_judgement(existing, reasons, effective)
    payload = _excluded_payload(snapshot, ctx, reasons) if reasons else _build_payload(db, ctx, snapshot, _finding_paths(db, result_id))
    row = AIEvidence(result_id=result_id, owner_user_id=ctx.owner_user_id, repository_id=ctx.repository_id, kind='history', snapshot_id=snapshot.id,
                     snapshot_sha256=snapshot.payload_sha256, status='excluded' if reasons else 'ok', exclusion_reasons=reasons, as_of=effective,
                     payload=payload, payload_hash=canonical_hash(payload))
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError:                                         # 동시에 같은 근거를 만든 경우: 먼저 저장된 것을 쓴다
        db.expire_all()
        winner = db.execute(select(AIEvidence).where(AIEvidence.result_id == result_id, AIEvidence.snapshot_id == snapshot_id,
                                                     AIEvidence.kind == 'history')).scalar_one()
        return _same_judgement(winner, reasons, effective)           # 먼저 저장된 것도 이 요청의 평가 시점으로 다시 비교한다
    return row


def _same_judgement(existing: AIEvidence, reasons: list[str], effective: datetime) -> AIEvidence:
    """근거는 불변이고 (결과, snapshot)당 하나다. 요청한 평가 시점에서도 같은 판정(상태·이유)일 때만 기존 근거를 돌려준다:
    더 이른 시점이라 미래 자료가 되거나 반대로 더 늦은 시점에서 풀리는 경우에 다른 시점의 판정을 조용히 돌려주지 않는다."""
    if sorted(reasons) != sorted(existing.exclusion_reasons or []):
        raise AIError(ErrorCode.VERSION_CONFLICT, '이미 다른 평가 시점으로 만든 이력 근거가 있어 이 시점의 판정과 다릅니다.',
                      details={'reason': 'HISTORY_AS_OF_CONFLICT', 'existing_as_of': existing.as_of.isoformat(), 'requested_as_of': effective.isoformat()})
    return existing


def attach_safely(db: Session, result_id: UUID, snapshot_id: int) -> UUID | None:
    """결과를 저장하는 transaction 안에서 이력 근거를 붙인다. 근거를 만들지 못해도 결과 저장은 절대 막지 않는다(savepoint로 격리)."""
    try:
        with db.begin_nested():
            return build_history_evidence(db, result_id, snapshot_id).id
    except Exception as exc:                                       # 근거는 부가 정보다: 실패해도 리뷰 결과는 그대로 저장한다
        logger.warning('history evidence skipped result=%s error=%s', result_id, type(exc).__name__)
        return None


def list_evidence(db: Session, result_id: UUID) -> list[AIEvidence]:
    return list(db.execute(select(AIEvidence).where(AIEvidence.result_id == result_id).order_by(AIEvidence.created_at, AIEvidence.id)).scalars())


def snapshot_options(db: Session, repository_id: int, *, as_of: datetime | None = None, limit: int = 20) -> list[dict]:
    """저장소의 최근 snapshot과 이력 근거 사용 가능 여부(이유 포함). 화면의 선택지다."""
    rows = db.execute(select(AnalysisSnapshot).where(AnalysisSnapshot.repository_id == repository_id)
                      .order_by(AnalysisSnapshot.id.desc()).limit(limit)).scalars().all()
    out = []
    for s in rows:
        reasons = snapshot_eligibility(db, s, as_of=as_of)
        out.append({'snapshot_id': s.id, 'target_sha': s.target_sha, 'analyzed_at': s.analyzed_at, 'window_start': s.window_start, 'window_end': s.window_end,
                    'formula_version': s.formula_version, 'eligible': not reasons, 'reasons': reasons,
                    'reason_texts': [REASON_TEXT.get(r, r) for r in reasons]})
    return out
