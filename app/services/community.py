"""Community membership and content rules. Callers own and commit the DB transaction.

Owner decisions (2026-11-01, AD-25): contributors of the analyzed repository join automatically, anyone else
only with a URL invite, the user who triggered the first analysis starts as admin, and the admin can hand the
role to another active member. Removed members stay removed: neither contributor matching nor an invite
brings them back; only the admin can restore them.
"""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import func, select

from app.models.analysis_job import AnalysisJob
from app.models.community import (
    CommunityComment,
    CommunityInvite,
    CommunityMember,
    CommunityPost,
)
from app.models.contributor import Contributor
from app.models.repository import Repository
from app.models.user import User

# Posts and comments together, per user, per community.
WRITE_LIMIT = 10
WRITE_WINDOW = timedelta(minutes=1)


def now():
    return datetime.now(timezone.utc)


def fail(status, code, message):
    return HTTPException(status, {'code': code, 'message': message})


def digest(token):
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def user_view(user):
    return {'id': str(user.id), 'github_login': user.github_login, 'display_name': user.display_name,
            'avatar_url': user.avatar_url} if user else None


def first_analyzer(db, repository_id):
    return db.scalar(select(AnalysisJob.triggered_by_user_id)
                     .where(AnalysisJob.repository_id == repository_id, AnalysisJob.triggered_by_user_id.is_not(None))
                     .order_by(AnalysisJob.created_at, AnalysisJob.id).limit(1))


def ensure_admin(db, repository_id):
    """The community exists as soon as anyone looks at it; its first admin is the first analyzer."""
    if db.scalar(select(CommunityMember).where(CommunityMember.repository_id == repository_id,
                                                CommunityMember.role == 'admin')):
        return
    analyzer = first_analyzer(db, repository_id)
    if not analyzer or not db.get(User, analyzer):
        return
    row = db.scalar(select(CommunityMember).where(CommunityMember.repository_id == repository_id,
                                                   CommunityMember.user_id == analyzer))
    if row:
        row.role = 'admin'
        row.status = 'active'
    else:
        db.add(CommunityMember(repository_id=repository_id, user_id=analyzer, role='admin', status='active',
                               joined_via='analyzer'))
    db.flush()


def is_contributor(db, repository_id, user):
    login = (user.github_login or '').lower()
    return bool(login) and db.scalar(select(func.count()).select_from(Contributor).where(
        Contributor.repository_id == repository_id, func.lower(Contributor.github_login) == login)) > 0


def membership(db, repository_id, user_id, *, join=True):
    """Return the caller's active membership, joining a contributor on the way. Everyone else gets 403."""
    if not user_id:
        raise fail(401, 'AUTH_REQUIRED', '커뮤니티는 로그인한 사용자만 볼 수 있습니다.')
    if not db.get(Repository, repository_id):
        raise fail(404, 'NOT_FOUND', '저장소를 찾을 수 없습니다.')
    user = db.get(User, user_id)
    if not user:
        raise fail(401, 'AUTH_REQUIRED', '다시 로그인해 주세요.')
    ensure_admin(db, repository_id)
    member = db.scalar(select(CommunityMember).where(CommunityMember.repository_id == repository_id,
                                                      CommunityMember.user_id == user_id))
    if member and member.status == 'removed':
        raise fail(403, 'MEMBER_REMOVED', '관리자가 이 커뮤니티에서 내보냈습니다.')
    if not member and join and is_contributor(db, repository_id, user):
        member = CommunityMember(repository_id=repository_id, user_id=user_id, role='member', status='active',
                                 joined_via='contributor')
        db.add(member)
        db.flush()
    if not member:
        raise fail(403, 'INVITE_REQUIRED', '이 저장소의 기여자가 아니면 초대 링크로만 참여할 수 있습니다.')
    return member


def require_admin(db, repository_id, user_id):
    member = membership(db, repository_id, user_id)
    if member.role != 'admin':
        raise fail(403, 'ADMIN_REQUIRED', '관리자만 할 수 있습니다.')
    return member


def throttle(db, repository_id, user_id):
    since = now() - WRITE_WINDOW
    posts = db.scalar(select(func.count()).select_from(CommunityPost).where(
        CommunityPost.repository_id == repository_id, CommunityPost.author_id == user_id, CommunityPost.created_at > since))
    comments = db.scalar(select(func.count()).select_from(CommunityComment).join(CommunityPost).where(
        CommunityPost.repository_id == repository_id, CommunityComment.author_id == user_id,
        CommunityComment.created_at > since))
    if posts + comments >= WRITE_LIMIT:
        raise HTTPException(429, {'code': 'RATE_LIMITED', 'message': '잠시 후 다시 작성해 주세요.'},
                            headers={'Retry-After': '60'})


def users_by_id(db, ids):
    ids = {i for i in ids if i}
    return {u.id: u for u in db.scalars(select(User).where(User.id.in_(ids)))} if ids else {}


def community_view(db, repository_id, member):
    repo = db.get(Repository, repository_id)
    admin = db.scalar(select(CommunityMember).where(CommunityMember.repository_id == repository_id,
                                                     CommunityMember.role == 'admin'))
    count = db.scalar(select(func.count()).select_from(CommunityMember).where(
        CommunityMember.repository_id == repository_id, CommunityMember.status == 'active'))
    return {'repository': {'id': repo.id, 'name': repo.name, 'url': repo.url},
            'me': {'role': member.role, 'joined_via': member.joined_via, 'chat_muted': bool(member.notifications_muted)},
            'admin': user_view(db.get(User, admin.user_id)) if admin else None, 'member_count': count}


def my_communities(db, user_id):
    """Joined communities plus the ones a contributor could open (and would join by opening)."""
    if not user_id:
        raise fail(401, 'AUTH_REQUIRED', '커뮤니티는 로그인한 사용자만 볼 수 있습니다.')
    user = db.get(User, user_id)
    if not user:
        raise fail(401, 'AUTH_REQUIRED', '다시 로그인해 주세요.')
    joined = {m.repository_id: m for m in db.scalars(select(CommunityMember).where(CommunityMember.user_id == user_id))}
    login = (user.github_login or '').lower()
    contributed = set(db.scalars(select(Contributor.repository_id).where(func.lower(Contributor.github_login) == login))) if login else set()
    analyzed = set(db.scalars(select(AnalysisJob.repository_id).where(AnalysisJob.triggered_by_user_id == user_id)))
    candidates = (set(joined) | contributed | {r for r in analyzed if first_analyzer(db, r) == user_id})
    result = []
    for repo in db.scalars(select(Repository).where(Repository.id.in_(candidates)).order_by(Repository.name)) if candidates else []:
        member = joined.get(repo.id)
        if member and member.status == 'removed':
            continue
        result.append({'repository': {'id': repo.id, 'name': repo.name, 'url': repo.url},
                       'role': member.role if member else ('admin' if repo.id in analyzed else 'member'),
                       'joined': bool(member)})
    return {'communities': result}


def members(db, repository_id, user_id):
    me = membership(db, repository_id, user_id)
    rows = list(db.scalars(select(CommunityMember).where(CommunityMember.repository_id == repository_id)
                           .order_by(CommunityMember.role, CommunityMember.created_at, CommunityMember.id)))
    # Removed members are visible to the admin only, so they can be restored.
    rows = [r for r in rows if r.status == 'active' or me.role == 'admin']
    people = users_by_id(db, [r.user_id for r in rows])
    return {'members': [{'user': user_view(people.get(r.user_id)), 'role': r.role, 'status': r.status,
                         'joined_via': r.joined_via, 'joined_at': r.created_at.isoformat() if r.created_at else None}
                        for r in rows]}


def update_member(db, repository_id, user_id, target_id, status):
    admin = require_admin(db, repository_id, user_id)
    target = db.scalar(select(CommunityMember).where(CommunityMember.repository_id == repository_id,
                                                      CommunityMember.user_id == target_id))
    if not target:
        raise fail(404, 'NOT_FOUND', '멤버를 찾을 수 없습니다.')
    if target.id == admin.id:
        raise fail(409, 'INVALID_STATE', '관리자는 자신을 내보낼 수 없습니다. 먼저 관리자를 넘겨주세요.')
    target.status = status


def transfer_admin(db, repository_id, user_id, target_id):
    admin = require_admin(db, repository_id, user_id)
    target = db.scalar(select(CommunityMember).where(CommunityMember.repository_id == repository_id,
                                                      CommunityMember.user_id == target_id))
    if not target or target.status != 'active':
        raise fail(404, 'NOT_FOUND', '활동 중인 멤버에게만 넘겨줄 수 있습니다.')
    if target.id == admin.id:
        raise fail(409, 'INVALID_STATE', '이미 관리자입니다.')
    # Demote first so the one-admin index never sees two admins.
    admin.role = 'member'
    db.flush()
    target.role = 'admin'
    db.flush()


def create_invite(db, repository_id, user_id, hours, max_uses):
    require_admin(db, repository_id, user_id)
    token = secrets.token_urlsafe(32)
    invite = CommunityInvite(repository_id=repository_id, token_hash=digest(token), created_by=user_id,
                             created_at=now(), expires_at=now() + timedelta(hours=hours), max_uses=max_uses, use_count=0)
    db.add(invite)
    db.flush()
    # The token exists only in this response; the server keeps its hash.
    return {'invite': invite_view(invite), 'token': token}


def invite_view(invite):
    return {'id': str(invite.id), 'expires_at': invite.expires_at.isoformat(), 'max_uses': invite.max_uses,
            'use_count': invite.use_count, 'revoked': invite.revoked_at is not None}


def invites(db, repository_id, user_id):
    require_admin(db, repository_id, user_id)
    rows = db.scalars(select(CommunityInvite).where(CommunityInvite.repository_id == repository_id,
                                                    CommunityInvite.revoked_at.is_(None), CommunityInvite.expires_at > now())
                      .order_by(CommunityInvite.created_at.desc()))
    return {'invites': [invite_view(r) for r in rows if r.max_uses is None or r.use_count < r.max_uses]}


def revoke_invite(db, repository_id, user_id, invite_id):
    require_admin(db, repository_id, user_id)
    invite = db.get(CommunityInvite, invite_id)
    if not invite or invite.repository_id != repository_id:
        raise fail(404, 'NOT_FOUND', '초대 링크를 찾을 수 없습니다.')
    invite.revoked_at = invite.revoked_at or now()


def redeem_invite(db, user_id, token):
    if not user_id or not db.get(User, user_id):
        raise fail(401, 'AUTH_REQUIRED', '로그인한 뒤 초대 링크를 여세요.')
    invite = db.scalar(select(CommunityInvite).where(CommunityInvite.token_hash == digest(token)).with_for_update())
    if (not invite or invite.revoked_at or invite.expires_at <= now()
            or (invite.max_uses is not None and invite.use_count >= invite.max_uses)):
        raise fail(410, 'INVITE_INVALID', '만료됐거나 취소된 초대 링크입니다.')
    ensure_admin(db, invite.repository_id)
    member = db.scalar(select(CommunityMember).where(CommunityMember.repository_id == invite.repository_id,
                                                      CommunityMember.user_id == user_id))
    if member and member.status == 'removed':
        raise fail(403, 'MEMBER_REMOVED', '관리자가 이 커뮤니티에서 내보냈습니다.')
    if not member:
        db.add(CommunityMember(repository_id=invite.repository_id, user_id=user_id, role='member', status='active',
                               joined_via='invite'))
        # Only a new member uses up the invite; reopening it as a member is free.
        invite.use_count += 1
        db.flush()
    return {'repository_id': invite.repository_id}


def post_view(post, people, comments=None):
    deleted = post.deleted_at is not None
    view = {'id': post.id, 'author': None if deleted else user_view(people.get(post.author_id)),
            'author_id': None if deleted else str(post.author_id),
            'title': '삭제된 글' if deleted else post.title, 'body': '' if deleted else post.body,
            'created_at': post.created_at.isoformat() if post.created_at else None,
            'updated_at': post.updated_at.isoformat() if post.updated_at else None, 'deleted': deleted}
    if comments is not None:
        view['comments'] = [{'id': c.id, 'author': None if c.deleted_at else user_view(people.get(c.author_id)),
                             'author_id': None if c.deleted_at else str(c.author_id),
                             'body': '' if c.deleted_at else c.body, 'deleted': c.deleted_at is not None,
                             'created_at': c.created_at.isoformat() if c.created_at else None,
                             'updated_at': c.updated_at.isoformat() if c.updated_at else None} for c in comments]
    return view


def posts(db, repository_id, user_id, before=None, limit=20):
    membership(db, repository_id, user_id)
    query = select(CommunityPost).where(CommunityPost.repository_id == repository_id, CommunityPost.deleted_at.is_(None))
    if before:
        query = query.where(CommunityPost.id < before)
    rows = list(db.scalars(query.order_by(CommunityPost.id.desc()).limit(limit + 1)))
    page, more = rows[:limit], len(rows) > limit
    counts = dict(db.execute(select(CommunityComment.post_id, func.count()).where(
        CommunityComment.post_id.in_([p.id for p in page]), CommunityComment.deleted_at.is_(None))
        .group_by(CommunityComment.post_id)).all()) if page else {}
    people = users_by_id(db, [p.author_id for p in page])
    items = []
    for p in page:
        view = post_view(p, people)
        view['body'] = view['body'][:280]
        view['comment_count'] = counts.get(p.id, 0)
        items.append(view)
    return {'posts': items, 'next_before': page[-1].id if more else None}


def find_post(db, repository_id, post_id):
    post = db.get(CommunityPost, post_id)
    if not post or post.repository_id != repository_id:
        raise fail(404, 'NOT_FOUND', '글을 찾을 수 없습니다.')
    return post


def post(db, repository_id, user_id, post_id):
    membership(db, repository_id, user_id)
    found = find_post(db, repository_id, post_id)
    if found.deleted_at:
        raise fail(404, 'NOT_FOUND', '삭제된 글입니다.')
    comments = list(db.scalars(select(CommunityComment).where(CommunityComment.post_id == post_id)
                               .order_by(CommunityComment.id)))
    people = users_by_id(db, [found.author_id, *[c.author_id for c in comments]])
    return post_view(found, people, comments)


def create_post(db, repository_id, user_id, title, body):
    membership(db, repository_id, user_id)
    throttle(db, repository_id, user_id)
    row = CommunityPost(repository_id=repository_id, author_id=user_id, title=title.strip(), body=body, created_at=now())
    if not row.title:
        raise fail(422, 'INVALID_INPUT', '제목을 입력하세요.')
    db.add(row)
    db.flush()
    return {'id': row.id}


def update_post(db, repository_id, user_id, post_id, title, body):
    membership(db, repository_id, user_id)
    row = find_post(db, repository_id, post_id)
    if row.deleted_at or row.author_id != user_id:
        raise fail(403, 'PERMISSION_DENIED', '작성자만 고칠 수 있습니다.')
    if not title.strip():
        raise fail(422, 'INVALID_INPUT', '제목을 입력하세요.')
    row.title, row.body, row.updated_at = title.strip(), body, now()


def delete_post(db, repository_id, user_id, post_id):
    me = membership(db, repository_id, user_id)
    row = find_post(db, repository_id, post_id)
    if row.author_id != user_id and me.role != 'admin':
        raise fail(403, 'PERMISSION_DENIED', '작성자나 관리자만 지울 수 있습니다.')
    row.deleted_at = row.deleted_at or now()


def create_comment(db, repository_id, user_id, post_id, body):
    membership(db, repository_id, user_id)
    parent = find_post(db, repository_id, post_id)
    if parent.deleted_at:
        raise fail(404, 'NOT_FOUND', '삭제된 글에는 댓글을 달 수 없습니다.')
    throttle(db, repository_id, user_id)
    row = CommunityComment(post_id=post_id, author_id=user_id, body=body, created_at=now())
    db.add(row)
    db.flush()
    return {'id': row.id}


def find_comment(db, repository_id, comment_id):
    row = db.get(CommunityComment, comment_id)
    if not row or db.get(CommunityPost, row.post_id).repository_id != repository_id:
        raise fail(404, 'NOT_FOUND', '댓글을 찾을 수 없습니다.')
    return row


def update_comment(db, repository_id, user_id, comment_id, body):
    membership(db, repository_id, user_id)
    row = find_comment(db, repository_id, comment_id)
    if row.deleted_at or row.author_id != user_id:
        raise fail(403, 'PERMISSION_DENIED', '작성자만 고칠 수 있습니다.')
    row.body, row.updated_at = body, now()


def delete_comment(db, repository_id, user_id, comment_id):
    me = membership(db, repository_id, user_id)
    row = find_comment(db, repository_id, comment_id)
    if row.author_id != user_id and me.role != 'admin':
        raise fail(403, 'PERMISSION_DENIED', '작성자나 관리자만 지울 수 있습니다.')
    row.deleted_at = row.deleted_at or now()
