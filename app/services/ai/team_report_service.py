"""팀 보고서(S13). 결정적으로 정리하며 모델을 호출하지 않는다. 계약은 docs/AI_TEAM_ACCESS.md 4절.

- 생성: 팀에 연결된 저장소를 고르면, 지금 유효한 공유만 모아 원본 목록(source_manifest)과 공개 범위 세대(visibility_epoch)와 함께 저장한다. 수정할 수 없다.
- 조회: 구성원이면서 ① 세대가 팀의 현재 세대와 같고 ② 원본 공유가 모두 유효하며 ③ 조회자가 원본의 모든 저장소에 지금 접근할 수 있어야 한다.
  하나라도 어긋나면 REPORT_BLOCKED로 내용을 주지 않는다(일부 저장소만 볼 수 있는 사람에게 같은 전문을 보여 주지 않는다).
- 문제 집계는 사람이 valid로 판정한 지적만 센다. 검증하지 않은 지적·불확실은 따로 세고 오탐은 집계에서 제외한다. 지문이 같은 지적은 후보로만 묶는다.
"""
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.schemas.ai import ErrorCode, canonical_hash
from app.schemas.ai_team import ReportCreate
from app.services.ai import markdown_safety, team_access, team_service
from app.services.ai.access import AIError
from app.services.ai.markdown_safety import code_span, escape_inline
from app.services.ai.team_access import check_repo_access, repo_access_reason, require_role

TEMPLATE_VERSION = 'team-report-v1'
SCHEMA_VERSION = 'team-report-payload-v1'
TEXT_FILE = Path(__file__).parent / 'prompts' / 'team_report_v1.txt'
TEXT_DIGEST = '376088c0e77a05a8e8822f9caba590c2c38e543441e8e66e7fbfe8d49620465d'                                   # 테스트가 현재 파일과 대조한다(내용을 바꾸면 새 버전 파일을 만든다)
MAX_REPORTS_PER_TEAM = 100
MAX_LISTED_VALID = 30
MAX_REPEAT_CANDIDATES = 20
BLOCK_TEXT = {
    'visibility_changed': '구성원·역할·저장소 연결·공유 범위가 바뀐 뒤라 이 보고서는 차단됐습니다. 새 범위로 다시 생성해야 합니다.',
    'share_removed': '이 보고서의 원본 공유가 해제됐습니다.',
    'share_outdated': '공유한 뒤 원본이 바뀌었거나 보관 기간이 지났습니다.',
    'repository_unlinked': '보고서에 포함된 저장소가 팀에서 연결 해제됐습니다.',
    'repository_access_lost': '보고서에 포함된 저장소 중 접근할 수 없는 곳이 있어 볼 수 없습니다. 접근할 수 있는 저장소만으로 다시 생성해야 합니다.',
    'access_check_unavailable': '저장소 접근을 확인할 수 없어 보고서를 열 수 없습니다. 잠시 후 다시 시도해 주세요.',
}
STALE_TEXT = {'feedback_changed': '공유한 사람이 AI 지적의 판정을 바꿨습니다. 이 보고서는 이전 판정 기준입니다.'}


def load_texts() -> dict[str, str]:
    out = {}
    for line in TEXT_FILE.read_text(encoding='utf-8').splitlines():
        if line.startswith('#') or ':' not in line:
            continue
        key, _, value = line.partition(':')
        out[key.strip()] = value.strip()
    return out


def text_digest() -> str:
    return hashlib.sha256(TEXT_FILE.read_bytes().replace(b'\r\n', b'\n')).hexdigest()


T = load_texts()


def _audit(db: Session, user_id: UUID, action: str, team_id: UUID, request_id: str, details: dict) -> None:
    team_service._audit(db, user_id, action, team_id, request_id, details)


# ── 만들기 ──────────────────────────────────────────────────────────────

def _feedback_digest(findings: list[dict]) -> str:
    return canonical_hash([[f['fingerprint'], f['verdict'], f['feedback_version']] for f in findings])


def _collect(db: Session, team_id: UUID, repository_ids: list[int]) -> tuple[list[dict], list[dict]]:
    """(쓸 수 있는 공유 내용, 제외한 공유). 공유한 사람이 지금도 공유할 수 있는 역할이어야 하고, 공유는 유효해야 한다."""
    rows = db.execute(text("""
        SELECT s.*, u.github_login FROM team_resource_shares s JOIN users u ON u.id = s.shared_by
        JOIN team_members m ON m.team_id = s.team_id AND m.user_id = s.shared_by AND m.status = 'active' AND m.role IN ('admin', 'member')
        WHERE s.team_id = :t AND s.repository_id = ANY(:repos) ORDER BY s.repository_id, s.created_at, s.id"""),
                      {'t': team_id, 'repos': repository_ids}).all()
    used, excluded = [], []
    for r in rows:
        status = team_service.share_status(db, r)
        if status != 'active':
            excluded.append({'share_id': str(r.id), 'type': r.resource_type, 'repository_id': r.repository_id, 'reason': status})
            continue
        source = None
        if r.resource_type == 'review_result':
            source = team_service.review_source(db, r.review_result_id, r.shared_by)
            if source is None:                                                                # 검사 뒤 작성자가 원본을 삭제했다: 오류로 끝내지 않고 제외하고 밝힌다
                excluded.append({'share_id': str(r.id), 'type': r.resource_type, 'repository_id': r.repository_id, 'reason': 'source_deleted'})
                continue
            content = source[1]
        else:
            content = team_service.handover_content(db, r.handover_target_id, r.pinned_target_version, r.pinned_preparation_version)   # 공유 당시 고정한 버전 그대로
            if content is None:                                                               # 검사와 읽기 사이에 작성자가 바꿨다: 공유하지 않은 새 내용은 쓰지 않는다
                excluded.append({'share_id': str(r.id), 'type': r.resource_type, 'repository_id': r.repository_id, 'reason': 'outdated'})
                continue
        meta = {'share_id': str(r.id), 'type': r.resource_type, 'repository_id': r.repository_id, 'shared_by': str(r.shared_by), 'shared_by_login': r.github_login,
                'created_at': r.created_at.isoformat()}
        if r.resource_type == 'review_result':
            meta.update(resource_id=str(r.review_result_id), payload_hash=source[0], feedback_digest=_feedback_digest(content['findings']))
        else:
            meta.update(resource_id=str(r.handover_target_id), pinned_target_version=r.pinned_target_version, pinned_preparation_version=r.pinned_preparation_version)
        used.append({'meta': meta, 'content': content})
    return used, excluded


def _build_payload(db: Session, team, repository_ids: list[int], used: list[dict], excluded: list[dict], generated_at: str) -> dict:
    names = {r.id: (r.name or f'#{r.id}') for r in db.execute(text('SELECT id, name FROM repositories WHERE id = ANY(:r)'), {'r': repository_ids}).all()}
    repos = []
    totals = Counter()
    for repo_id in repository_ids:
        results, handovers = [], []
        problems, valid_rows = Counter(), []
        by_fingerprint = defaultdict(list)
        for item in (u for u in used if u['meta']['repository_id'] == repo_id):
            meta, content = item['meta'], item['content']
            if meta['type'] == 'review_result':
                counts = Counter(f['verdict'] for f in content['findings'])
                results.append({'share_id': meta['share_id'], 'shared_by_login': meta['shared_by_login'], 'created_at': meta['created_at'], 'total': len(content['findings']),
                                **{k: counts.get(k, 0) for k in ('valid', 'unverified', 'uncertain', 'false_positive')}})
                for f in content['findings']:
                    problems[f['verdict']] += 1
                    if f['verdict'] == 'valid':
                        valid_rows.append({'title': f.get('title'), 'category': f.get('category'), 'location': f.get('location'), 'share_id': meta['share_id']})
                    by_fingerprint[f['fingerprint']].append((meta['share_id'], f))
            else:
                counts = Counter(i['status'] for i in content['items'])
                handovers.append({'share_id': meta['share_id'], 'name': content['name'], 'shared_by_login': meta['shared_by_login'],
                                  **{k: counts.get(k, 0) for k in ('unknown', 'needed', 'ready')}})
        repeats = []
        for fp, rows in by_fingerprint.items():
            if len({sid for sid, _ in rows}) >= 2:                                           # 서로 다른 결과에서 지문이 같은 지적: 후보일 뿐 문제 수에 합치지 않는다
                repeats.append({'fingerprint': fp[:12], 'title': rows[0][1].get('title'), 'result_count': len({sid for sid, _ in rows}),
                                'verdicts': dict(Counter(f['verdict'] for _, f in rows))})
        repeats.sort(key=lambda r: (-r['result_count'], r['fingerprint']))
        valid_by_category = Counter(v['category'] for v in valid_rows)
        repos.append({'repository_id': repo_id, 'name': names.get(repo_id, f'#{repo_id}'), 'results': results, 'handovers': handovers,
                      'problems': {'confirmed': problems['valid'], 'pending_review': problems['unverified'], 'uncertain': problems['uncertain'],
                                   'excluded_false_positive': problems['false_positive']},
                      'confirmed_by_category': dict(sorted(valid_by_category.items())), 'confirmed_problems': valid_rows[:MAX_LISTED_VALID],
                      'repeat_candidates': repeats[:MAX_REPEAT_CANDIDATES]})
        totals.update(results=len(results), handovers=len(handovers), valid=problems['valid'], unverified=problems['unverified'], uncertain=problems['uncertain'],
                      false_positive=problems['false_positive'])
    return {'schema': SCHEMA_VERSION, 'template_version': TEMPLATE_VERSION, 'repositories': repos, 'totals': dict(totals), 'excluded_shares': excluded,
            'generated_at': generated_at, 'visibility_epoch': team.visibility_epoch}


def render(payload: dict, shares: int) -> str:
    totals = payload['totals']
    lines = [f"# {T['TITLE']}", '', T['INTRO'], '', f"- {T['CAVEAT_AI']}", f"- {T['CAVEAT_PROBLEMS']}", f"- {T['CAVEAT_REPEAT']}", f"- {T['CAVEAT_ACCESS']}", '',
             f"## {T['S_SUMMARY']}", '']
    lines.append(T['SUMMARY_LINE'].format(repos=len(payload['repositories']), results=totals.get('results', 0), handovers=totals.get('handovers', 0)))
    lines.append('')
    lines.append(T['SUMMARY_PROBLEMS'].format(valid=totals.get('valid', 0), unverified=totals.get('unverified', 0), uncertain=totals.get('uncertain', 0),
                                              false_positive=totals.get('false_positive', 0)))
    if not totals.get('results') and not totals.get('handovers'):
        lines += ['', T['NOTHING_SHARED']]
    lines += ['', f"## {T['S_REPOSITORIES']}"]
    for repo in payload['repositories']:
        lines += ['', f"### {T['REPO_HEAD'].format(name=escape_inline(repo['name'], limit=120))}", '', f"**{T['RESULTS_HEAD']}**", '']
        if not repo['results']:
            lines.append(T['RESULTS_NONE'])
        for r in repo['results']:
            lines.append('- ' + T['RESULT_LINE'].format(when=escape_inline(r['created_at'][:10]), login=escape_inline(r['shared_by_login'] or '-', limit=60), total=r['total'], valid=r['valid'],
                                                         unverified=r['unverified'], uncertain=r['uncertain'], false_positive=r['false_positive']))
        lines += ['', f"**{T['VALID_HEAD']}**", '']
        if not repo['confirmed_problems']:
            lines.append(T['VALID_NONE'])
        for v in repo['confirmed_problems']:
            loc = v.get('location') or {}
            where = f"{loc.get('path')}:{loc.get('start_line')}" if loc.get('path') else '-'
            lines.append(f"- {escape_inline(v.get('title') or '', limit=200)} — {escape_inline(v.get('category') or '')}, {code_span(where)}")
        if repo['repeat_candidates']:
            lines += ['', f"**{T['REPEAT_HEAD']}**", '']
            for c in repo['repeat_candidates']:
                verdicts = ', '.join(f'{k} {n}' for k, n in sorted(c['verdicts'].items()))
                lines.append('- ' + T['REPEAT_LINE'].format(title=escape_inline(c['title'] or '', limit=160), count=c['result_count'], verdicts=escape_inline(verdicts)))
        lines += ['', f"**{T['HANDOVER_HEAD']}**", '']
        if not repo['handovers']:
            lines.append(T['HANDOVER_NONE'])
        for h in repo['handovers']:
            lines.append('- ' + T['HANDOVER_LINE'].format(name=escape_inline(h['name'], limit=120), unknown=h['unknown'], needed=h['needed'], ready=h['ready']))
    lines += ['', f"## {T['S_EXCLUDED']}", '']
    if not payload['excluded_shares']:
        lines.append(T['EXCLUDED_NONE'])
    for e in payload['excluded_shares']:
        lines.append('- ' + T['EXCLUDED_LINE'].format(id=escape_inline(e['share_id'][:8]), reason=T.get(f"REASON_{e['reason']}", escape_inline(e['reason']))))
    lines += ['', f"## {T['S_BASIS']}", '', T['BASIS_LINE'].format(epoch=payload['visibility_epoch'], generated_at=escape_inline(payload['generated_at']), shares=shares)]
    return '\n'.join(lines).rstrip('\n') + '\n'


def report_item(row, *, visibility_current: bool) -> dict:
    return {'report_id': row.id, 'team_id': row.team_id, 'repository_ids': list(row.repository_ids), 'created_by': row.created_by,
            'visibility_epoch': row.visibility_epoch, 'visibility_current': visibility_current, 'created_at': row.created_at}


def generate(db: Session, user_id: UUID, team_id: UUID, body: ReportCreate, *, request_id: str = '-') -> dict:
    team, member = require_role(db, user_id, team_id, 'generate_report')
    repository_ids = sorted(set(body.repository_ids))
    linked = team_access.team_repository_ids(db, team_id)
    if not set(repository_ids) <= linked:
        db.rollback()
        raise AIError(ErrorCode.INVALID_SELECTION, '팀에 연결된 저장소만 보고서에 넣을 수 있습니다.')
    epoch0 = team.visibility_epoch
    db.rollback()                                                                             # 외부 확인 동안 팀 행을 잡지 않는다
    for repo_id in repository_ids:
        check_repo_access(db, user_id, repo_id)                                               # 생성하는 사람이 보고서의 모든 저장소에 접근할 수 있어야 한다
    team, member = require_role(db, user_id, team_id, 'generate_report', lock=True)           # 이후 구성원·공유 변경은 이 생성이 끝난 뒤에 처리된다
    if team.visibility_epoch != epoch0 or not set(repository_ids) <= team_access.team_repository_ids(db, team_id):
        db.rollback()
        raise AIError(ErrorCode.VERSION_CONFLICT, '생성하는 동안 팀의 구성원·저장소·공유 범위가 바뀌었습니다. 다시 시도해 주세요.', details={'reason': 'VISIBILITY_CHANGED'})
    if db.execute(text('SELECT count(*) FROM team_reports WHERE team_id = :t'), {'t': team_id}).scalar() >= MAX_REPORTS_PER_TEAM:
        db.rollback()
        raise AIError(ErrorCode.USER_LIMIT, f'팀의 보고서는 {MAX_REPORTS_PER_TEAM}개까지입니다. 오래된 보고서를 삭제해 주세요.', retryable=False)
    used, excluded = _collect(db, team_id, repository_ids)
    generated_at = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    payload = _build_payload(db, team, repository_ids, used, excluded, generated_at)
    markdown = render(payload, len(used))
    issues = markdown_safety.validate_markdown(markdown)
    if issues:                                                                                # 렌더러가 만든 문서는 항상 안전해야 한다. 아니면 저장하지 않는다.
        db.rollback()
        raise AIError(ErrorCode.INVALID_STATE, '보고서를 안전하게 만들 수 없습니다.', details={'issues': issues})
    manifest = {'team_id': str(team_id), 'visibility_epoch': team.visibility_epoch, 'repository_ids': repository_ids, 'shares': [u['meta'] for u in used],
                'excluded': excluded, 'generated_by': str(user_id)}
    row = db.execute(text("""INSERT INTO team_reports (team_id, created_by, repository_ids, source_manifest, payload, markdown, payload_hash, visibility_epoch, template_version)
                             VALUES (:t, :u, CAST(:r AS jsonb), CAST(:m AS jsonb), CAST(:p AS jsonb), :md, :h, :e, :v) RETURNING *"""),
                     {'t': team_id, 'u': user_id, 'r': json.dumps(repository_ids), 'm': json.dumps(manifest), 'p': json.dumps(payload), 'md': markdown,
                      'h': canonical_hash(payload), 'e': team.visibility_epoch, 'v': TEMPLATE_VERSION}).one()
    _audit(db, user_id, 'team.report_generated', team_id, request_id, {'report_id': str(row.id), 'repositories': len(repository_ids), 'shares': len(used), 'excluded': len(excluded)})
    db.commit()
    return report_view(row, [])


def report_view(row, stale: list[str]) -> dict:
    return {'report_id': row.id, 'team_id': row.team_id, 'repository_ids': list(row.repository_ids), 'created_by': row.created_by, 'visibility_epoch': row.visibility_epoch,
            'template_version': row.template_version, 'source_manifest': row.source_manifest, 'payload': row.payload, 'markdown': row.markdown, 'stale_reasons': stale,
            'created_at': row.created_at}


def list_reports(db: Session, user_id: UUID, team_id: UUID) -> list[dict]:
    team, _ = require_role(db, user_id, team_id, 'view')
    rows = db.execute(text('SELECT * FROM team_reports WHERE team_id = :t ORDER BY created_at DESC, id LIMIT 100'), {'t': team_id}).all()
    return [report_item(r, visibility_current=r.visibility_epoch == team.visibility_epoch) for r in rows]


def _access_reasons(db: Session, user_id: UUID, row) -> list[str]:
    """저장소 접근(외부 확인) 때문에 볼 수 없는 이유. 잠금 밖에서 한다."""
    reasons = []
    for repo_id in sorted({int(r) for r in row.repository_ids}):
        reason = repo_access_reason(db, user_id, repo_id)
        if reason:
            reasons.append(reason)
    return reasons


def _block_reasons(db: Session, team, row) -> list[str]:
    """DB 상태 때문에 볼 수 없는 이유(세대·저장소 연결·공유 존재와 유효성). 팀 행을 잡은 채 한 트랜잭션에서 판단한다."""
    reasons = []
    if row.visibility_epoch != team.visibility_epoch:
        reasons.append('visibility_changed')
    linked = team_access.team_repository_ids(db, team.id)
    if not {int(r) for r in row.repository_ids} <= linked:
        reasons.append('repository_unlinked')
    shares = {str(s.id): s for s in db.execute(text('SELECT * FROM team_resource_shares WHERE team_id = :t'), {'t': team.id}).all()}
    for src in (row.source_manifest or {}).get('shares', []):
        share = shares.get(src['share_id'])
        if share is None:
            reasons.append('share_removed')
        elif team_service.share_status(db, share) != 'active':
            reasons.append('share_outdated')
        elif src['type'] == 'handover_target' and (share.pinned_target_version, share.pinned_preparation_version) != (src['pinned_target_version'], src['pinned_preparation_version']):
            reasons.append('share_outdated')                                                  # 다시 공유되어 고정 버전이 바뀐 경우
    return sorted(set(reasons))


def _stale(db: Session, row) -> list[str]:
    for src in (row.source_manifest or {}).get('shares', []):
        if src['type'] != 'review_result':
            continue
        share = db.execute(text('SELECT shared_by, review_result_id FROM team_resource_shares WHERE id = :s'), {'s': src['share_id']}).one_or_none()
        if share is not None and _feedback_digest(team_service.review_content(db, share.review_result_id, share.shared_by)['findings']) != src['feedback_digest']:
            return ['feedback_changed']
    return []


def _report_row(db: Session, team_id: UUID, report_id: UUID):
    return db.execute(text('SELECT * FROM team_reports WHERE id = :r AND team_id = :t'), {'r': report_id, 't': team_id}).one_or_none()


def get_report(db: Session, user_id: UUID, team_id: UUID, report_id: UUID) -> dict:
    """보고서 조회. 저장소 접근 확인(외부 호출)을 잠금 밖에서 먼저 하고, 그 뒤 팀 행을 FOR SHARE로 잡은 채 구성원·세대·공유 상태를 다시 판단하고
    같은 잠금 안에서 내용을 만든다. 외부 확인을 기다리는 동안 공유가 해제되거나 구성원이 빠져도 처음 읽은 정보로 내용을 주지 않는다."""
    require_role(db, user_id, team_id, 'view')
    row = _report_row(db, team_id, report_id)
    if row is None:
        db.rollback()
        raise AIError(ErrorCode.RESOURCE_NOT_FOUND, '보고서를 찾을 수 없습니다.')
    db.rollback()                                                                             # 외부 확인 동안 트랜잭션을 열어 두지 않는다
    access_reasons = _access_reasons(db, user_id, row)
    team, _ = require_role(db, user_id, team_id, 'view', lock='share')                        # 이후의 구성원·공유·세대 변경은 이 조회가 끝난 뒤에 처리된다
    row = _report_row(db, team_id, report_id)
    if row is None:
        db.rollback()
        raise AIError(ErrorCode.RESOURCE_NOT_FOUND, '보고서를 찾을 수 없습니다.')
    reasons = sorted(set(access_reasons) | set(_block_reasons(db, team, row)))
    if reasons:
        db.rollback()
        raise AIError(ErrorCode.REPORT_BLOCKED, '이 보고서는 지금 볼 수 없습니다. ' + ' '.join(BLOCK_TEXT[r] for r in reasons),
                      details={'reasons': reasons})
    view = report_view(row, _stale(db, row))
    db.rollback()                                                                             # 읽기를 마치면 잠금을 푼다
    return view


def delete_report(db: Session, user_id: UUID, team_id: UUID, report_id: UUID, *, request_id: str = '-') -> None:
    _, me = require_role(db, user_id, team_id, 'view', lock=True)
    row = db.execute(text('SELECT id, created_by FROM team_reports WHERE id = :r AND team_id = :t'), {'r': report_id, 't': team_id}).one_or_none()
    if row is None:
        db.rollback()
        raise AIError(ErrorCode.RESOURCE_NOT_FOUND, '보고서를 찾을 수 없습니다.')
    if me.role != 'admin' and not (me.role == 'member' and row.created_by == user_id):
        db.rollback()
        raise AIError(ErrorCode.PERMISSION_DENIED, '보고서를 삭제할 수 있는 사람은 관리자와 만든 멤버입니다.', details={'role': me.role})
    db.execute(text('DELETE FROM team_reports WHERE id = :r'), {'r': report_id})
    _audit(db, user_id, 'team.report_deleted', team_id, request_id, {'report_id': str(report_id)})
    db.commit()
