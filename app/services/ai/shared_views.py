"""같은 저장소 구성원에게 여는 읽기 전용 조회: 다른 사람이 만든 AI 결과·지적과 그 사람들의 판단·메모.

- 읽기만 한다. 만들기·삭제·판단 저장은 기존 API가 그대로 작성자 본인에게만 허용한다.
- 이 모듈은 작성자 검사를 하지 않는다: 호출하는 API 계층이 매 요청마다 현재 저장소 접근 권한을 다시 확인한다.
  (권한을 잃으면 존재를 숨긴다. 결과가 속한 저장소로만 권한을 확인하므로 다른 저장소의 결과 id로는 열 수 없다.)
- 결과에는 입력 원문 접근이 포함되지 않는다: 근거 코드는 작성자 본인만 볼 수 있고, 여기서는 결과에 저장된 인용만 보인다.
"""
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.schemas.ai import (AuthorView, Coverage, ErrorCode, Finding, SharedFeedbackView, SharedFindingView,
                            SharedResultItem, SharedResultView, ResultView)
from app.services.ai.access import AIError
from app.services.ai.job_views import DEFAULT_PAGE, MAX_PAGE, decode_cursor, encode_cursor

_RESULT_SQL = """
SELECT r.*, j.kind, j.input_id, j.provider, j.model, u.github_login, u.display_name,
       (SELECT count(*) FROM ai_findings f WHERE f.result_id = r.id) AS finding_count
FROM ai_results r JOIN ai_jobs j ON j.id = r.job_id JOIN users u ON u.id = r.owner_user_id
WHERE j.deleted_at IS NULL"""


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(timezone.utc)


def _author(login: str, display_name: str | None) -> AuthorView:
    return AuthorView(login=login, display_name=display_name)


def load_shared_result_row(db: Session, result_id: UUID, now: datetime):
    """작성자와 무관하게 결과 행을 읽는다(없음·삭제·만료는 모두 오류). 저장소 권한은 호출자가 이 행의 repository_id로 확인한다."""
    row = db.execute(text(_RESULT_SQL + " AND r.id = :r"), {'r': result_id}).one_or_none()
    if row is None:
        raise AIError(ErrorCode.RESOURCE_NOT_FOUND, '결과를 찾을 수 없습니다.')
    if row.expires_at <= now:
        raise AIError(ErrorCode.RESULT_EXPIRED, '결과의 보관 기간이 지났습니다.')
    return row


def get_shared_result(db: Session, row, viewer_id: UUID) -> SharedResultView:
    result = ResultView(
        result_id=row.id, job_id=row.job_id, kind=row.kind, repository_id=row.repository_id, input_id=row.input_id,
        payload=row.payload, payload_hash=row.payload_hash.strip(), coverage=Coverage(**row.coverage),
        structure_validation=row.structure_validation, human_validation=row.human_validation, provider=row.provider,
        model=row.model, finding_count=row.finding_count, created_at=row.created_at, expires_at=row.expires_at)
    return SharedResultView(result=result, author=_author(row.github_login, row.display_name),
                            mine=row.owner_user_id == viewer_id)


def list_shared_results(db: Session, viewer_id: UUID, repository_id: int, *, limit: int = DEFAULT_PAGE,
                        cursor: str | None = None, now: datetime | None = None) -> tuple[list[SharedResultItem], str | None]:
    """저장소의 다른 구성원이 만든 결과를 최신순 keyset으로 돌려준다. 내 결과는 기존 작업 목록에서 본다."""
    now = _now(now)
    limit = max(1, min(limit, MAX_PAGE))
    sql = _RESULT_SQL + """ AND r.repository_id = :repo AND r.owner_user_id <> :v AND r.expires_at > :now"""
    params: dict = {'repo': repository_id, 'v': viewer_id, 'now': now, 'n': limit + 1}
    if cursor is not None:
        stamp, last_id = decode_cursor(cursor, 2)
        try:
            params['t'], params['c'] = datetime.fromisoformat(stamp), UUID(last_id)
        except (TypeError, ValueError):
            raise AIError(ErrorCode.INVALID_SELECTION, 'cursor가 올바르지 않습니다.') from None
        sql += " AND (r.created_at, r.id) < (:t, :c)"
    rows = db.execute(text(sql + " ORDER BY r.created_at DESC, r.id DESC LIMIT :n"), params).all()
    more = len(rows) > limit
    rows = rows[:limit]
    counts = {}
    if rows:
        counts = {r.result_id: r.n for r in db.execute(text("""
            SELECT f.result_id, count(*) AS n FROM finding_feedback b JOIN ai_findings f ON f.id = b.finding_id
            WHERE f.result_id = ANY(:ids) GROUP BY f.result_id"""), {'ids': [r.id for r in rows]}).all()}
    items = [SharedResultItem(
        result_id=r.id, job_id=r.job_id, kind=r.kind, repository_id=r.repository_id,
        author=_author(r.github_login, r.display_name), provider=r.provider, model=r.model,
        finding_count=r.finding_count, feedback_count=counts.get(r.id, 0), created_at=r.created_at, expires_at=r.expires_at)
        for r in rows]
    next_cursor = encode_cursor(rows[-1].created_at.isoformat(), str(rows[-1].id)) if more else None
    return items, next_cursor


def list_shared_findings(db: Session, row, viewer_id: UUID, *, limit: int = DEFAULT_PAGE,
                         cursor: str | None = None) -> tuple[list[SharedFindingView], str | None]:
    """ordinal 순 keyset 목록. 각 지적에 구성원들이 남긴 판단·메모가 모두 붙는다(최근에 고친 것이 먼저)."""
    limit = max(1, min(limit, MAX_PAGE))
    after = 0
    if cursor is not None:
        (after,) = decode_cursor(cursor, 1)
        if not isinstance(after, int) or isinstance(after, bool) or after < 0:
            raise AIError(ErrorCode.INVALID_SELECTION, 'cursor가 올바르지 않습니다.')
    rows = db.execute(text("""
        SELECT f.id, f.result_id, f.ordinal, f.detail FROM ai_findings f
        WHERE f.result_id = :r AND f.ordinal > :a ORDER BY f.ordinal LIMIT :n"""),
                      {'r': row.id, 'a': after, 'n': limit + 1}).all()
    more = len(rows) > limit
    rows = rows[:limit]
    by_finding: dict = {}
    if rows:
        for b in db.execute(text("""
            SELECT b.finding_id, b.author_user_id, b.verdict, b.note, b.version, b.updated_at, u.github_login, u.display_name
            FROM finding_feedback b JOIN users u ON u.id = b.author_user_id
            WHERE b.finding_id = ANY(:ids) ORDER BY b.updated_at DESC, b.author_user_id"""),
                           {'ids': [r.id for r in rows]}).all():
            by_finding.setdefault(b.finding_id, []).append(SharedFeedbackView(
                author=_author(b.github_login, b.display_name), verdict=b.verdict, note=b.note, version=b.version,
                updated_at=b.updated_at, mine=b.author_user_id == viewer_id))
    views = []
    for r in rows:
        detail = dict(r.detail)
        quote = detail.pop('quote', None)
        for extra in ('file_id', 'model_limitations'):
            detail.pop(extra, None)
        views.append(SharedFindingView(finding_id=r.id, result_id=r.result_id, finding=Finding(**detail), quote=quote,
                                       feedbacks=by_finding.get(r.id, [])))
    return views, (encode_cursor(rows[-1].ordinal) if more else None)
