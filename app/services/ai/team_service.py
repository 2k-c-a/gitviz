"""팀(S13): 팀·구성원·연결 저장소·명시적 공유. 계약은 docs/AI_TEAM_ACCESS.md.

- 개인 자료는 자동으로 공유되지 않는다. 작성자가 팀을 지정해 공유한 것만 팀에 보이고, 보일 때마다 역할·저장소 접근·공유 유효성을 다시 확인한다.
- 팀 행을 잠그고 바꾸는 동작(구성원·역할·저장소·공유)은 서로, 그리고 보고서 생성과 직렬화된다. 구성원·역할·저장소·공유가 줄면 공개 범위 세대를 올린다.
- 서비스는 commit까지 한다. 저장소 접근 확인은 team_access.check_repo_access(GitHub 기준)로 하며 외부 호출 중에는 팀 행을 잡지 않는다.
"""
import json
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.schemas.ai import ErrorCode
from app.schemas.ai_team import InviteRequest, ShareCreate, TeamCreate
from app.services.ai import team_access
from app.services.ai.access import AIError, ensure_owner
from app.services.ai.team_access import bump_epoch, check_repo_access, require_role

MAX_TEAMS_PER_USER = 20
MAX_MEMBERS = 50
MAX_REPOSITORIES = 20
MAX_SHARES_PER_TEAM = 500
SHAREABLE_CONTENT_KEYS = ('title', 'category', 'priority', 'condition', 'impact', 'verification', 'suggestion', 'limitations', 'location')


def _audit(db: Session, user_id: UUID, action: str, object_id, request_id: str, details: dict, repository_id: int | None = None) -> None:
    db.execute(text("""INSERT INTO audit_events (actor_user_id, repository_id, action, object_type, object_id, request_id, details)
                       VALUES (:a, :r, :act, 'team', :o, :q, CAST(:d AS jsonb))"""),
               {'a': user_id, 'r': repository_id, 'act': action, 'o': str(object_id), 'q': request_id, 'd': json.dumps(details)})


def _conflict(db: Session, message: str, **details) -> AIError:
    db.rollback()
    return AIError(ErrorCode.VERSION_CONFLICT, message, details=details)


# ── 팀 ──────────────────────────────────────────────────────────────────

def _member_count(db: Session, team_id: UUID) -> int:
    return db.execute(text("SELECT count(*) FROM team_members WHERE team_id = :t AND status = 'active'"), {'t': team_id}).scalar()


def team_view(db: Session, team, member) -> dict:
    return {'team_id': team.id, 'name': team.name, 'role': member.role, 'status': member.status, 'visibility_epoch': team.visibility_epoch,
            'member_count': _member_count(db, team.id), 'created_at': team.created_at}


def create_team(db: Session, user_id: UUID, body: TeamCreate, *, request_id: str = '-') -> dict:
    name = ' '.join(body.name.split())
    if not name:
        raise AIError(ErrorCode.INVALID_SELECTION, '팀 이름을 입력해 주세요.')
    if db.execute(text("SELECT count(*) FROM team_members WHERE user_id = :u AND role = 'admin' AND status = 'active'"), {'u': user_id}).scalar() >= MAX_TEAMS_PER_USER:
        raise AIError(ErrorCode.USER_LIMIT, f'관리하는 팀은 {MAX_TEAMS_PER_USER}개까지 만들 수 있습니다.', retryable=False)
    team = db.execute(text('INSERT INTO teams (name, created_by) VALUES (:n, :u) RETURNING *'), {'n': name, 'u': user_id}).one()
    member = db.execute(text("INSERT INTO team_members (team_id, user_id, role, status) VALUES (:t, :u, 'admin', 'active') RETURNING *"), {'t': team.id, 'u': user_id}).one()
    _audit(db, user_id, 'team.created', team.id, request_id, {})
    db.commit()
    return team_view(db, team, member)


def list_teams(db: Session, user_id: UUID) -> list[dict]:
    rows = db.execute(text("""SELECT t.*, m.role, m.status AS member_status, m.id AS member_id FROM team_members m JOIN teams t ON t.id = m.team_id
                              WHERE m.user_id = :u ORDER BY t.created_at, t.id"""), {'u': user_id}).all()
    out = []
    for r in rows:
        out.append({'team_id': r.id, 'name': r.name, 'role': r.role, 'status': r.member_status, 'visibility_epoch': r.visibility_epoch,
                    'member_count': _member_count(db, r.id) if r.member_status == 'active' else 0, 'created_at': r.created_at})
    return out


def get_team(db: Session, user_id: UUID, team_id: UUID) -> dict:
    team, member = require_role(db, user_id, team_id, 'view')
    return team_view(db, team, member)


def rename_team(db: Session, user_id: UUID, team_id: UUID, name: str, *, request_id: str = '-') -> dict:
    team, member = require_role(db, user_id, team_id, 'manage', lock=True)
    name = ' '.join(name.split())
    if not name:
        db.rollback()
        raise AIError(ErrorCode.INVALID_SELECTION, '팀 이름을 입력해 주세요.')
    team = db.execute(text('UPDATE teams SET name = :n, updated_at = now() WHERE id = :t RETURNING *'), {'n': name, 't': team_id}).one()
    _audit(db, user_id, 'team.renamed', team_id, request_id, {})
    db.commit()
    return team_view(db, team, member)


def delete_team(db: Session, user_id: UUID, team_id: UUID, *, request_id: str = '-') -> None:
    require_role(db, user_id, team_id, 'manage', lock=True)
    db.execute(text('DELETE FROM teams WHERE id = :t'), {'t': team_id})                      # 구성원·연결 저장소·공유·보고서가 함께 사라진다(개인 자료는 그대로)
    _audit(db, user_id, 'team.deleted', team_id, request_id, {})
    db.commit()


# ── 구성원 ──────────────────────────────────────────────────────────────

def _member_view(r, login: str | None = None, display_name: str | None = None) -> dict:
    return {'user_id': r.user_id, 'github_login': login if login is not None else r.github_login,
            'display_name': display_name if login is not None else r.display_name, 'role': r.role, 'status': r.status, 'created_at': r.created_at}


def list_members(db: Session, user_id: UUID, team_id: UUID) -> list[dict]:
    _, me = require_role(db, user_id, team_id, 'view')
    rows = db.execute(text("""SELECT m.*, u.github_login, u.display_name FROM team_members m JOIN users u ON u.id = m.user_id
                              WHERE m.team_id = :t AND (m.status = 'active' OR :admin) ORDER BY m.status, u.github_login"""),
                      {'t': team_id, 'admin': me.role == 'admin'}).all()                     # 초대 중인 사람은 관리자에게만 보인다
    return [_member_view(r) for r in rows]


def invite(db: Session, user_id: UUID, team_id: UUID, body: InviteRequest, *, request_id: str = '-') -> dict:
    require_role(db, user_id, team_id, 'manage')
    found = db.execute(text('SELECT id, github_login, display_name FROM users WHERE lower(github_login) = lower(:l)'), {'l': body.github_login.strip().lstrip('@')}).all()
    if len(found) != 1:                                                                       # 없음·모호함은 같은 응답이다(사용자 존재를 알려 주지 않는다)
        db.rollback()
        raise AIError(ErrorCode.RESOURCE_NOT_FOUND, '초대할 사용자를 찾을 수 없습니다. GitViz에 로그인한 적 있는 GitHub 사용자만 초대할 수 있습니다.')
    target = found[0]
    if target.id == user_id:
        db.rollback()
        raise AIError(ErrorCode.INVALID_SELECTION, '자기 자신은 초대할 수 없습니다.')
    require_role(db, user_id, team_id, 'manage', lock=True)                                   # 조회하는 동안 역할이 바뀌었을 수 있으니 잠그고 다시 확인한다
    existing = db.execute(text('SELECT * FROM team_members WHERE team_id = :t AND user_id = :u'), {'t': team_id, 'u': target.id}).one_or_none()
    if existing is not None and existing.status == 'active':
        raise _conflict(db, '이미 팀 구성원입니다.', reason='ALREADY_MEMBER')
    if existing is None and db.execute(text('SELECT count(*) FROM team_members WHERE team_id = :t'), {'t': team_id}).scalar() >= MAX_MEMBERS:
        db.rollback()
        raise AIError(ErrorCode.USER_LIMIT, f'구성원과 초대는 합쳐 {MAX_MEMBERS}명까지입니다.', retryable=False)
    if existing is None:
        row = db.execute(text("INSERT INTO team_members (team_id, user_id, role, status, invited_by) VALUES (:t, :u, :r, 'invited', :i) RETURNING *"),
                         {'t': team_id, 'u': target.id, 'r': body.role, 'i': user_id}).one()
    else:
        row = db.execute(text("UPDATE team_members SET role = :r, invited_by = :i, updated_at = now() WHERE id = :m RETURNING *"),
                         {'r': body.role, 'i': user_id, 'm': existing.id}).one()
    _audit(db, user_id, 'team.invited', team_id, request_id, {'role': body.role})
    db.commit()
    return _member_view(row, target.github_login, target.display_name)


def accept_invitation(db: Session, user_id: UUID, team_id: UUID, *, request_id: str = '-') -> dict:
    team = team_access.load_team(db, team_id, lock=True)
    row = db.execute(text("SELECT * FROM team_members WHERE team_id = :t AND user_id = :u AND status = 'invited' FOR UPDATE"), {'t': team_id, 'u': user_id}).one_or_none() if team else None
    if row is None:
        db.rollback()
        raise AIError(ErrorCode.RESOURCE_NOT_FOUND, '초대를 찾을 수 없습니다.')
    row = db.execute(text("UPDATE team_members SET status = 'active', updated_at = now() WHERE id = :m RETURNING *"), {'m': row.id}).one()
    _audit(db, user_id, 'team.joined', team_id, request_id, {'role': row.role})
    db.commit()
    return team_view(db, team, row)


def _active_admin_count(db: Session, team_id: UUID) -> int:
    return db.execute(text("SELECT count(*) FROM team_members WHERE team_id = :t AND role = 'admin' AND status = 'active'"), {'t': team_id}).scalar()


def _drop_shares_of(db: Session, team_id: UUID, user_id: UUID) -> int:
    return db.execute(text('DELETE FROM team_resource_shares WHERE team_id = :t AND shared_by = :u'), {'t': team_id, 'u': user_id}).rowcount


def change_role(db: Session, user_id: UUID, team_id: UUID, target_id: UUID, role: str, *, request_id: str = '-') -> dict:
    require_role(db, user_id, team_id, 'manage', lock=True)
    row = db.execute(text('SELECT m.*, u.github_login, u.display_name FROM team_members m JOIN users u ON u.id = m.user_id WHERE m.team_id = :t AND m.user_id = :u'),
                     {'t': team_id, 'u': target_id}).one_or_none()
    if row is None:
        db.rollback()
        raise AIError(ErrorCode.RESOURCE_NOT_FOUND, '구성원을 찾을 수 없습니다.')
    if row.role == role:
        db.rollback()
        return _member_view(row)
    if row.status == 'active' and row.role == 'admin' and _active_admin_count(db, team_id) <= 1:
        raise _conflict(db, '팀에는 관리자가 한 명 이상 있어야 합니다.', reason='LAST_ADMIN')
    updated = db.execute(text('UPDATE team_members SET role = :r, updated_at = now() WHERE id = :m RETURNING *'), {'r': role, 'm': row.id}).one()
    dropped = 0
    if row.status == 'active':
        if role == 'viewer':
            dropped = _drop_shares_of(db, team_id, target_id)                                 # 조회자는 공유할 수 없으므로 그 사람의 공유는 모두 닫는다
        bump_epoch(db, team_id)
    _audit(db, user_id, 'team.role_changed', team_id, request_id, {'role': role, 'shares_removed': dropped})
    db.commit()
    return _member_view(updated, row.github_login, row.display_name)


def remove_member(db: Session, user_id: UUID, team_id: UUID, target_id: UUID, *, request_id: str = '-') -> None:
    """관리자가 구성원을 제거하거나 본인이 탈퇴한다. 그 사람이 한 공유는 모두 닫히고 공개 범위 세대가 올라간다."""
    if target_id == user_id:
        team_access.load_team(db, team_id, lock=True)
        mine = db.execute(text('SELECT * FROM team_members WHERE team_id = :t AND user_id = :u'), {'t': team_id, 'u': user_id}).one_or_none()
        if mine is None:
            db.rollback()
            raise AIError(ErrorCode.RESOURCE_NOT_FOUND, '팀을 찾을 수 없습니다.')
    else:
        require_role(db, user_id, team_id, 'manage', lock=True)
    row = db.execute(text('SELECT * FROM team_members WHERE team_id = :t AND user_id = :u'), {'t': team_id, 'u': target_id}).one_or_none()
    if row is None:
        db.rollback()
        raise AIError(ErrorCode.RESOURCE_NOT_FOUND, '구성원을 찾을 수 없습니다.')
    if row.status == 'active' and row.role == 'admin' and _active_admin_count(db, team_id) <= 1:
        raise _conflict(db, '팀에는 관리자가 한 명 이상 있어야 합니다. 다른 관리자를 지정한 뒤 진행해 주세요.', reason='LAST_ADMIN')
    dropped = _drop_shares_of(db, team_id, target_id)
    db.execute(text('DELETE FROM team_members WHERE id = :m'), {'m': row.id})
    if row.status == 'active':
        bump_epoch(db, team_id)
    _audit(db, user_id, 'team.member_left' if target_id == user_id else 'team.member_removed', team_id, request_id, {'shares_removed': dropped})
    db.commit()


# ── 저장소 ──────────────────────────────────────────────────────────────

def list_repositories(db: Session, user_id: UUID, team_id: UUID) -> list[dict]:
    require_role(db, user_id, team_id, 'view')
    rows = db.execute(text("""SELECT tr.repository_id, r.name, r.url, tr.created_at FROM team_repositories tr JOIN repositories r ON r.id = tr.repository_id
                              WHERE tr.team_id = :t ORDER BY tr.created_at, tr.repository_id"""), {'t': team_id}).all()
    return [{'repository_id': r.repository_id, 'name': r.name, 'url': r.url, 'created_at': r.created_at} for r in rows]


def add_repository(db: Session, user_id: UUID, team_id: UUID, repository_id: int, *, request_id: str = '-') -> dict:
    require_role(db, user_id, team_id, 'manage')
    if not db.execute(text('SELECT 1 FROM repositories WHERE id = :r'), {'r': repository_id}).scalar():
        db.rollback()
        raise AIError(ErrorCode.RESOURCE_NOT_FOUND, '저장소를 찾을 수 없습니다.')
    check_repo_access(db, user_id, repository_id)                                            # 연결하는 관리자가 그 저장소에 접근할 수 있어야 한다
    require_role(db, user_id, team_id, 'manage', lock=True)
    if db.execute(text('SELECT count(*) FROM team_repositories WHERE team_id = :t'), {'t': team_id}).scalar() >= MAX_REPOSITORIES:
        db.rollback()
        raise AIError(ErrorCode.USER_LIMIT, f'팀에는 저장소를 {MAX_REPOSITORIES}개까지 연결할 수 있습니다.', retryable=False)
    db.execute(text('INSERT INTO team_repositories (team_id, repository_id, added_by) VALUES (:t, :r, :u) ON CONFLICT DO NOTHING'),
               {'t': team_id, 'r': repository_id, 'u': user_id})
    _audit(db, user_id, 'team.repository_added', team_id, request_id, {}, repository_id)
    db.commit()
    return next(r for r in list_repositories(db, user_id, team_id) if r['repository_id'] == repository_id)


def remove_repository(db: Session, user_id: UUID, team_id: UUID, repository_id: int, *, request_id: str = '-') -> None:
    require_role(db, user_id, team_id, 'manage', lock=True)
    removed = db.execute(text('DELETE FROM team_repositories WHERE team_id = :t AND repository_id = :r'), {'t': team_id, 'r': repository_id}).rowcount
    if not removed:
        db.rollback()
        raise AIError(ErrorCode.RESOURCE_NOT_FOUND, '연결된 저장소가 아닙니다.')
    bump_epoch(db, team_id)                                                                   # 그 저장소의 공유는 연결과 함께 사라지고 이전 보고서는 차단된다
    _audit(db, user_id, 'team.repository_removed', team_id, request_id, {}, repository_id)
    db.commit()


# ── 공유 ────────────────────────────────────────────────────────────────

def _latest_prep_version(db: Session, target_id: UUID) -> int:
    return db.execute(text('SELECT coalesce(max(version), 0) FROM handover_preparation_versions WHERE target_id = :t'), {'t': target_id}).scalar()


def _own_resource(db: Session, user_id: UUID, body: ShareCreate):
    """공유하려는 자료가 내 것이고 공유할 수 있는 상태인지 확인한다. (저장소 id, 고정할 버전들)."""
    if body.resource_type == 'review_result':
        row = db.execute(text("""SELECT r.id, r.owner_user_id, r.repository_id, r.expires_at, j.kind FROM ai_results r
                                 JOIN ai_jobs j ON j.id = r.job_id AND j.deleted_at IS NULL WHERE r.id = :r"""), {'r': body.resource_id}).one_or_none()
        if row is None:
            raise AIError(ErrorCode.RESOURCE_NOT_FOUND, '결과를 찾을 수 없습니다.')
        ensure_owner(row.owner_user_id, user_id)                                              # 남의 자료는 존재를 숨긴다
        if row.kind != 'review':
            raise AIError(ErrorCode.INVALID_SELECTION, '코드 리뷰 결과만 공유할 수 있습니다.')
        from datetime import datetime, timezone
        if row.expires_at <= datetime.now(timezone.utc):
            raise AIError(ErrorCode.RESULT_EXPIRED, '결과의 보관 기간이 지났습니다.')
        return row.repository_id, None, None
    row = db.execute(text('SELECT id, owner_user_id, repository_id, version FROM handover_targets WHERE id = :t'), {'t': body.resource_id}).one_or_none()
    if row is None:
        raise AIError(ErrorCode.RESOURCE_NOT_FOUND, '인계 대상을 찾을 수 없습니다.')
    ensure_owner(row.owner_user_id, user_id)
    return row.repository_id, row.version, _latest_prep_version(db, row.id)


def share_status(db: Session, row) -> str:
    from datetime import datetime, timezone
    if row.resource_type == 'review_result':
        expires = db.execute(text('SELECT expires_at FROM ai_results WHERE id = :r'), {'r': row.review_result_id}).scalar()
        return 'active' if expires and expires > datetime.now(timezone.utc) else 'expired'
    t = db.execute(text('SELECT version FROM handover_targets WHERE id = :t'), {'t': row.handover_target_id}).one_or_none()
    if t is None:
        return 'outdated'
    return 'active' if (t.version == row.pinned_target_version and _latest_prep_version(db, row.handover_target_id) == row.pinned_preparation_version) else 'outdated'


def _share_view(db: Session, row, login: str | None) -> dict:
    return {'share_id': row.id, 'team_id': row.team_id, 'repository_id': row.repository_id, 'resource_type': row.resource_type,
            'resource_id': row.review_result_id or row.handover_target_id, 'shared_by': row.shared_by, 'shared_by_login': login,
            'status': share_status(db, row), 'created_at': row.created_at}


def create_share(db: Session, user_id: UUID, team_id: UUID, body: ShareCreate, *, request_id: str = '-') -> tuple[dict, bool]:
    """(공유 보기, 새로 만들었는지). 인계 대상은 다시 공유하면 현재 버전으로 고정을 갱신한다."""
    require_role(db, user_id, team_id, 'share')
    try:
        repository_id, tv, pv = _own_resource(db, user_id, body)
    except AIError:
        db.rollback()
        raise
    if repository_id not in team_access.team_repository_ids(db, team_id):
        db.rollback()
        raise AIError(ErrorCode.INVALID_SELECTION, '이 저장소는 팀에 연결돼 있지 않습니다. 관리자가 저장소를 먼저 연결해야 합니다.')
    check_repo_access(db, user_id, repository_id)                                            # 공유하는 사람이 지금 그 저장소에 접근할 수 있어야 한다
    require_role(db, user_id, team_id, 'share', lock=True)                                   # 외부 확인 뒤 다시 잠그고 역할·연결을 재확인한다
    if repository_id not in team_access.team_repository_ids(db, team_id):
        db.rollback()
        raise AIError(ErrorCode.INVALID_SELECTION, '이 저장소는 팀에 연결돼 있지 않습니다.')
    column = 'review_result_id' if body.resource_type == 'review_result' else 'handover_target_id'
    existing = db.execute(text(f'SELECT * FROM team_resource_shares WHERE team_id = :t AND {column} = :r'), {'t': team_id, 'r': body.resource_id}).one_or_none()
    created = existing is None
    if existing is None:
        if db.execute(text('SELECT count(*) FROM team_resource_shares WHERE team_id = :t'), {'t': team_id}).scalar() >= MAX_SHARES_PER_TEAM:
            db.rollback()
            raise AIError(ErrorCode.USER_LIMIT, f'팀의 공유는 {MAX_SHARES_PER_TEAM}개까지입니다.', retryable=False)
        row = db.execute(text(f"""INSERT INTO team_resource_shares (team_id, repository_id, resource_type, {column}, shared_by, pinned_target_version, pinned_preparation_version)
                                  VALUES (:t, :repo, :type, :r, :u, :tv, :pv) RETURNING *"""),
                         {'t': team_id, 'repo': repository_id, 'type': body.resource_type, 'r': body.resource_id, 'u': user_id, 'tv': tv, 'pv': pv}).one()
    elif body.resource_type == 'handover_target':
        row = db.execute(text('UPDATE team_resource_shares SET pinned_target_version = :tv, pinned_preparation_version = :pv WHERE id = :s RETURNING *'),
                         {'tv': tv, 'pv': pv, 's': existing.id}).one()
    else:
        row = existing
    _audit(db, user_id, 'team.shared' if created else 'team.share_refreshed', team_id, request_id, {'type': body.resource_type}, repository_id)
    db.commit()
    login = db.execute(text('SELECT github_login FROM users WHERE id = :u'), {'u': row.shared_by}).scalar()
    return _share_view(db, row, login), created


def list_shares(db: Session, user_id: UUID, team_id: UUID) -> list[dict]:
    require_role(db, user_id, team_id, 'view')
    rows = db.execute(text("""SELECT s.*, u.github_login FROM team_resource_shares s JOIN users u ON u.id = s.shared_by
                              WHERE s.team_id = :t ORDER BY s.created_at DESC, s.id LIMIT 200"""), {'t': team_id}).all()
    return [_share_view(db, r, r.github_login) for r in rows]


def delete_share(db: Session, user_id: UUID, team_id: UUID, share_id: UUID, *, request_id: str = '-') -> None:
    _, me = require_role(db, user_id, team_id, 'view', lock=True)
    row = db.execute(text('SELECT * FROM team_resource_shares WHERE id = :s AND team_id = :t'), {'s': share_id, 't': team_id}).one_or_none()
    if row is None:
        db.rollback()
        raise AIError(ErrorCode.RESOURCE_NOT_FOUND, '공유를 찾을 수 없습니다.')
    if row.shared_by != user_id and me.role != 'admin':
        db.rollback()
        raise AIError(ErrorCode.PERMISSION_DENIED, '공유를 해제할 수 있는 사람은 공유한 사람과 관리자입니다.', details={'role': me.role})
    db.execute(text('DELETE FROM team_resource_shares WHERE id = :s'), {'s': share_id})
    bump_epoch(db, team_id)                                                                   # 이 자료가 들어간 보고서는 차단된다
    _audit(db, user_id, 'team.unshared', team_id, request_id, {'type': row.resource_type, 'by_owner': row.shared_by == user_id}, row.repository_id)
    db.commit()


# ── 공유된 자료 보기 ────────────────────────────────────────────────────

def review_content(db: Session, result_id: UUID, owner_id: UUID) -> dict:
    """공유된 리뷰 결과: 지적(코드 원문·인용·작성자 메모 제외)과 작성자의 검증 판정, 결과에 붙은 이력 근거의 수치."""
    rows = db.execute(text("""
        SELECT f.ordinal, f.fingerprint, f.title, f.category, f.priority, f.detail, b.verdict, b.version AS fb_version FROM ai_findings f
        LEFT JOIN finding_feedback b ON b.finding_id = f.id AND b.author_user_id = :o WHERE f.result_id = :r ORDER BY f.ordinal"""),
                      {'o': owner_id, 'r': result_id}).all()
    findings = []
    for r in rows:
        detail = {k: r.detail.get(k) for k in SHAREABLE_CONTENT_KEYS if k in r.detail}
        findings.append({**detail, 'title': r.title, 'category': r.category, 'priority': r.priority, 'ordinal': r.ordinal, 'fingerprint': r.fingerprint.strip(), 'verdict': r.verdict or 'unverified', 'feedback_version': r.fb_version or 0})
    evidence = []
    for e in db.execute(text("SELECT snapshot_id, payload FROM ai_evidence WHERE result_id = :r AND status = 'ok' ORDER BY created_at"), {'r': result_id}).all():
        paths = []
        for p in (e.payload or {}).get('paths') or []:
            o, w = p.get('observations') or {}, p.get('ownership') or {}
            paths.append({'path': p.get('path'), 'status': p.get('status'), 'change_freq': o.get('change_freq'), 'bugfix_count': o.get('bugfix_count'),
                          'contributor_count': w.get('contributor_count'), 'observation_count': w.get('observation_count')})
        evidence.append({'snapshot_id': e.snapshot_id, 'paths': paths})
    return {'findings': findings, 'history': evidence,
            'note': '지적은 AI가 만든 것이며 판정(verdict)은 작성자의 판단입니다. GitViz가 확인한 것이 아닙니다.'}


def handover_content(db: Session, target_id: UUID) -> dict:
    """공유된 인계 대상: 대상 이름·파일과 항목별 준비 상태만(메모·링크·문서 본문·인계자/인수자 메모는 제외)."""
    from app.services.ai import handover_draft
    t = db.execute(text('SELECT name, selected_paths, snapshot_id, version FROM handover_targets WHERE id = :t'), {'t': target_id}).one()
    prep = db.execute(text('SELECT version, items FROM handover_preparation_versions WHERE target_id = :t ORDER BY version DESC LIMIT 1'), {'t': target_id}).one_or_none()
    items = []
    for key in handover_draft.ITEM_KEYS:
        status = ((prep.items or {}).get(key) or {}).get('status', 'unknown') if prep else 'unknown'
        items.append({'key': key, 'label': handover_draft.label_of(key), 'status': status, 'status_label': handover_draft.status_label(status)})
    return {'name': t.name, 'selected_paths': list(t.selected_paths), 'target_version': t.version, 'preparation_version': prep.version if prep else 0, 'items': items,
            'note': '"준비 완료"는 작성자가 기록한 상태이며 GitViz가 확인한 것이 아닙니다. 입력하지 않은 항목은 미확인입니다.'}


def share_content(db: Session, user_id: UUID, team_id: UUID, share_id: UUID) -> dict:
    """공유 자료의 내용. 팀 구성원이면서 이 저장소에 지금 접근할 수 있고 공유가 유효할 때만 준다."""
    require_role(db, user_id, team_id, 'view')
    row = db.execute(text('SELECT * FROM team_resource_shares WHERE id = :s AND team_id = :t'), {'s': share_id, 't': team_id}).one_or_none()
    if row is None:
        db.rollback()
        raise AIError(ErrorCode.RESOURCE_NOT_FOUND, '공유를 찾을 수 없습니다.')
    db.rollback()                                                                             # 외부 확인 동안 트랜잭션을 열어 두지 않는다
    check_repo_access(db, user_id, row.repository_id)
    status = share_status(db, row)
    if status != 'active':
        raise AIError(ErrorCode.INVALID_STATE, '공유한 뒤 원본이 바뀌었거나 보관 기간이 지나 내용을 볼 수 없습니다. 작성자가 다시 공유해야 합니다.', details={'reason': f'share_{status}'})
    content = review_content(db, row.review_result_id, row.shared_by) if row.resource_type == 'review_result' else handover_content(db, row.handover_target_id)
    return {'share_id': row.id, 'resource_type': row.resource_type, 'repository_id': row.repository_id, 'content': content}
