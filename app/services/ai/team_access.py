"""팀 권한 판단(S13). 계약은 docs/AI_TEAM_ACCESS.md.

보이는 범위 = 팀 역할 ∩ 현재 저장소 접근 ∩ 현재 유효한 공유. 어느 것도 저장된 허가를 믿지 않고 요청마다 다시 확인한다.
구성원이 아니면 팀의 존재를 숨기고(404), 구성원이지만 역할이 부족하면 PERMISSION_DENIED(403)다.
"""
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.schemas.ai import ErrorCode
from app.services.ai.access import AIAction, AIError, authorize_ai
from app.services.ai.content_policy import POLICY_VERSION

ROLE_RANK = {'viewer': 1, 'member': 2, 'admin': 3}
# 동작별 필요한 최소 역할(docs/AI_TEAM_ACCESS.md 2절의 표)
ACTION_ROLE = {'view': 'viewer', 'share': 'member', 'generate_report': 'member', 'manage': 'admin'}
ROLE_TEXT = {'admin': '관리자', 'member': '멤버', 'viewer': '조회자'}


def _hide(db: Session) -> AIError:
    db.rollback()
    return AIError(ErrorCode.RESOURCE_NOT_FOUND, '팀을 찾을 수 없습니다.')


def load_team(db: Session, team_id: UUID, *, lock: bool | str = False):
    """lock=True는 FOR UPDATE(구성원·공유·세대를 바꾸는 쪽), lock='share'는 FOR SHARE(읽는 쪽)다. 읽는 쪽끼리는 서로 막지 않고,
    바꾸는 쪽은 읽는 쪽이 끝나기를 기다리며 그 반대도 같다."""
    clause = {True: ' FOR UPDATE', 'share': ' FOR SHARE'}.get(lock, '')
    return db.execute(text('SELECT * FROM teams WHERE id = :t' + clause), {'t': team_id}).one_or_none()


def active_member(db: Session, user_id: UUID, team_id: UUID):
    return db.execute(text("SELECT * FROM team_members WHERE team_id = :t AND user_id = :u AND status = 'active'"), {'t': team_id, 'u': user_id}).one_or_none()


def require_role(db: Session, user_id: UUID, team_id: UUID, action: str, *, lock: bool | str = False):
    """(팀 행, 구성원 행). 팀을 잠그면(lock) 구성원·공유·세대 변경과 직렬화된다. 구성원이 아니면 404, 역할이 부족하면 403."""
    team = load_team(db, team_id, lock=lock)
    member = active_member(db, user_id, team_id) if team is not None else None
    if team is None or member is None:
        raise _hide(db)
    if ROLE_RANK[member.role] < ROLE_RANK[ACTION_ROLE[action]]:
        db.rollback()
        raise AIError(ErrorCode.PERMISSION_DENIED, f'{ROLE_TEXT[ACTION_ROLE[action]]} 이상만 할 수 있습니다.', details={'required_role': ACTION_ROLE[action], 'role': member.role})
    return team, member


def bump_epoch(db: Session, team_id: UUID) -> int:
    """구성원·역할·저장소 연결·공유 범위가 줄어들 때 올린다. 이 세대보다 오래된 보고서는 차단된다."""
    return db.execute(text('UPDATE teams SET visibility_epoch = visibility_epoch + 1, updated_at = now() WHERE id = :t RETURNING visibility_epoch'), {'t': team_id}).scalar()


def team_repository_ids(db: Session, team_id: UUID) -> set[int]:
    return {r.repository_id for r in db.execute(text('SELECT repository_id FROM team_repositories WHERE team_id = :t'), {'t': team_id}).all()}


def check_repo_access(db: Session, user_id: UUID, repository_id: int) -> None:
    """이 사용자의 현재 저장소 접근(GitHub 기준)을 다시 확인한다. 권한이 없거나 확인할 수 없으면 AIError."""
    authorize_ai(db, user_id, repository_id, AIAction.read, POLICY_VERSION)


def repo_access_reason(db: Session, user_id: UUID, repository_id: int) -> str | None:
    """접근할 수 없는 이유 코드(없으면 None). 확인 장애도 접근 가능으로 보지 않는다(닫힘)."""
    try:
        check_repo_access(db, user_id, repository_id)
    except AIError as e:
        return 'access_check_unavailable' if e.code in (ErrorCode.ACCESS_CHECK_UNAVAILABLE, ErrorCode.UPSTREAM_UNAVAILABLE, ErrorCode.UPSTREAM_RATE_LIMIT) else 'repository_access_lost'
    return None
