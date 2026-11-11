"""Community notifications (2026-11-11): unread chat per community and schedule invitations, for the app to announce.

Nothing is stored per message. A member keeps only the id of the last chat message they have seen (`chat_read_id`), so the
unread count is counted when asked. An invitation is a participant row the participant's app has not announced yet
(`notified_at` is empty). The desktop app polls `summary()`, shows Windows notifications and marks invitations seen; the
chat screen moves the read position. Muting a community silences its chat only; invitations are addressed to the person.
"""
from sqlalchemy import func, select, update

from app.models.community import CommunityChatMessage, CommunityEvent, CommunityEventParticipant, CommunityMember
from app.models.repository import Repository
from app.services.community import fail, membership, now, user_view, users_by_id

PREVIEW = 80
INVITE_LIMIT = 20


def iso(value):
    return value.isoformat() if value else None


def preview(body):
    text = ' '.join((body or '').split())
    return text if len(text) <= PREVIEW else text[:PREVIEW - 1] + '…'


def latest_message_id(db, repository_id):
    return db.scalar(select(func.max(CommunityChatMessage.id)).where(CommunityChatMessage.repository_id == repository_id)) or 0


def summary(db, user_id):
    if not user_id:
        raise fail(401, 'AUTH_REQUIRED', '커뮤니티 알림은 로그인한 사용자만 볼 수 있습니다.')
    members = list(db.scalars(select(CommunityMember).where(CommunityMember.user_id == user_id,
                                                            CommunityMember.status == 'active')))
    ids = [m.repository_id for m in members]
    repos = {r.id: r for r in db.scalars(select(Repository).where(Repository.id.in_(ids)))} if ids else {}
    members = sorted((m for m in members if m.repository_id in repos), key=lambda m: (repos[m.repository_id].name or '').lower())
    communities, authors = [], set()
    for member in members:
        if member.chat_read_id is None:
            # A new member starts at the newest message; the history they scroll through on joining is not "unread".
            member.chat_read_id = latest_message_id(db, member.repository_id)
        unread_filter = (CommunityChatMessage.repository_id == member.repository_id, CommunityChatMessage.deleted_at.is_(None),
                         CommunityChatMessage.author_id != user_id, CommunityChatMessage.id > member.chat_read_id)
        unread = db.scalar(select(func.count()).select_from(CommunityChatMessage).where(*unread_filter)) or 0
        latest = db.scalar(select(CommunityChatMessage).where(*unread_filter)
                           .order_by(CommunityChatMessage.id.desc()).limit(1)) if unread else None
        if latest:
            authors.add(latest.author_id)
        communities.append((member, unread, latest))
    rows = db.execute(
        select(CommunityEvent)
        .join(CommunityEventParticipant, CommunityEventParticipant.event_id == CommunityEvent.id)
        .where(CommunityEventParticipant.user_id == user_id, CommunityEventParticipant.notified_at.is_(None),
               CommunityEvent.deleted_at.is_(None), CommunityEvent.status != 'cancelled',
               CommunityEvent.author_id != user_id, CommunityEvent.repository_id.in_(list(repos) or [0]))
        .order_by(CommunityEvent.created_at, CommunityEvent.id).limit(INVITE_LIMIT)).scalars().all()
    authors |= {event.author_id for event in rows}
    people = users_by_id(db, list(authors))

    def repository(repository_id):
        repo = repos[repository_id]
        return {'id': repo.id, 'name': repo.name}

    chat = [{'repository': repository(member.repository_id), 'muted': bool(member.notifications_muted), 'unread_chat': unread,
             'latest': {'id': latest.id, 'author': user_view(people.get(latest.author_id)), 'preview': preview(latest.body),
                        'created_at': iso(latest.created_at)} if latest else None}
            for member, unread, latest in communities]
    invites = [{'event_id': event.id, 'repository': repository(event.repository_id), 'title': event.title,
                'starts_at': iso(event.starts_at), 'all_day': bool(event.all_day),
                'utc_offset': (event.details or {}).get('utc_offset', 0), 'author': user_view(people.get(event.author_id))}
               for event in rows]
    total = sum(item['unread_chat'] for item in chat if not item['muted']) + len(invites)
    return {'communities': chat, 'invites': invites, 'unread_total': total}


def mark_chat_read(db, repository_id, user_id, message_id):
    """Move the read position forward only, and never past the newest message of this community."""
    member = membership(db, repository_id, user_id)
    target = min(message_id, latest_message_id(db, repository_id))
    if target > (member.chat_read_id or 0):
        member.chat_read_id = target
    return {'chat_read_id': member.chat_read_id or 0}


def mark_invites_seen(db, user_id, event_ids):
    if not user_id:
        raise fail(401, 'AUTH_REQUIRED', '다시 로그인해 주세요.')
    result = db.execute(update(CommunityEventParticipant)
                        .where(CommunityEventParticipant.user_id == user_id, CommunityEventParticipant.notified_at.is_(None),
                               CommunityEventParticipant.event_id.in_(list(dict.fromkeys(event_ids))))
                        .values(notified_at=now()).execution_options(synchronize_session=False))
    return {'marked': result.rowcount or 0}


def set_muted(db, repository_id, user_id, muted):
    member = membership(db, repository_id, user_id)
    member.notifications_muted = bool(muted)
    return {'muted': member.notifications_muted}
