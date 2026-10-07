"""지적(finding)에 대한 사람의 피드백. 원본 결과·지적은 바꾸지 않고 finding_feedback 행에만 기록한다.

결과 작성자만 남길 수 있고, `expected_version`(처음이면 0)이 맞을 때만 쓴다. 지적 행을 잠가 같은 지적에 대한 동시
요청을 직렬화하므로 두 요청이 같은 version을 근거로 둘 다 성공하는 일이 없다.
"""
import json
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.schemas.ai import ErrorCode, FeedbackView, Verdict
from app.services.ai.access import AIError, ensure_owner


def load_finding_owner(db: Session, finding_id: UUID, owner_id: UUID, now: datetime):
    """지적과 그 결과의 작성자·저장소·만료를 확인한다. 남의 자료는 존재를 숨긴다."""
    row = db.execute(text("""
        SELECT f.id, f.result_id, r.owner_user_id, r.repository_id, r.expires_at
        FROM ai_findings f JOIN ai_results r ON r.id = f.result_id JOIN ai_jobs j ON j.id = r.job_id
        WHERE f.id = :f AND j.deleted_at IS NULL"""), {'f': finding_id}).one_or_none()
    if row is None:
        raise AIError(ErrorCode.RESOURCE_NOT_FOUND, '지적을 찾을 수 없습니다.')
    ensure_owner(row.owner_user_id, owner_id)
    if row.expires_at <= now:
        raise AIError(ErrorCode.RESULT_EXPIRED, '결과의 보관 기간이 지났습니다.')
    return row


def put_feedback(db: Session, finding_id: UUID, owner_id: UUID, verdict: Verdict, note: str | None,
                 expected_version: int, *, request_id: str = '-', now: datetime | None = None) -> FeedbackView:
    now = now or datetime.now(timezone.utc)
    info = load_finding_owner(db, finding_id, owner_id, now)
    db.rollback()
    db.execute(text("SELECT 1 FROM ai_findings WHERE id = :f FOR UPDATE"), {'f': finding_id})     # 같은 지적의 동시 요청 직렬화
    current = db.execute(text("SELECT version FROM finding_feedback WHERE finding_id = :f AND author_user_id = :a"),
                         {'f': finding_id, 'a': owner_id}).scalar()
    if (current or 0) != expected_version:
        db.rollback()
        raise AIError(ErrorCode.VERSION_CONFLICT, '다른 곳에서 피드백이 바뀌었습니다. 최신 내용을 확인해 주세요.',
                      details={'current_version': current or 0})
    if current is None:
        row = db.execute(text("""INSERT INTO finding_feedback (finding_id, author_user_id, verdict, note, version)
                                 VALUES (:f, :a, :v, :n, 1) RETURNING verdict, note, version, updated_at"""),
                         {'f': finding_id, 'a': owner_id, 'v': verdict.value, 'n': note}).one()
    else:
        row = db.execute(text("""UPDATE finding_feedback SET verdict = :v, note = :n, version = version + 1, updated_at = :now
                                 WHERE finding_id = :f AND author_user_id = :a
                                 RETURNING verdict, note, version, updated_at"""),
                         {'f': finding_id, 'a': owner_id, 'v': verdict.value, 'n': note, 'now': now}).one()
    db.execute(text("""INSERT INTO audit_events (actor_user_id, repository_id, action, object_type, object_id, request_id, details)
                       VALUES (:a, :r, 'finding.feedback', 'finding', :f, :q, CAST(:d AS jsonb))"""),
               {'a': owner_id, 'r': info.repository_id, 'f': str(finding_id), 'q': request_id,
                'd': json.dumps({'verdict': verdict.value, 'version': row.version})})       # 메모 본문은 감사 기록에 넣지 않는다
    db.commit()
    return FeedbackView(verdict=row.verdict, note=row.note, version=row.version, updated_at=row.updated_at)
