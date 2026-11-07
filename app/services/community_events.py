"""Community schedule rules. Callers own and commit the DB transaction.

Decisions (2026-11-07): any active member creates events; the author and the community admin edit or delete them;
participants answer for themselves. An event is visible to every member ('community') or only to its author,
assignee and participants ('participants'), and that narrowing also holds for the admin. Development links reuse
what the analysis already collected (pull requests with reviews, releases); issues are kept by number and link to
GitHub, because issues are not collected. Sprints and milestones are events that other events point at.

Recurring events are one row expanded per query, in the UTC offset the author entered them with, so a monthly
event on the 31st skips months without one and 'every day 09:00 KST' stays 09:00 KST. Edits apply to the series.
"""
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from fastapi import HTTPException
from sqlalchemy import func, or_, select

from app.models.community import CommunityEvent, CommunityEventParticipant, CommunityMember, CommunityPost
from app.models.pr_review import PrReview
from app.models.pull_request import PullRequest
from app.models.release import Release
from app.models.repository import Repository
from app.services.community import fail, membership, now, user_view, users_by_id

MAX_RANGE = timedelta(days=100)
MAX_OCCURRENCES = 1000
MAX_SEARCH = 50
EVENT_LIMIT = 20
EVENT_WINDOW = timedelta(minutes=1)
STEP = {'daily': timedelta(days=1), 'weekly': timedelta(days=7)}


def aware(value):
    # SQLite fixtures drop the offset that PostgreSQL TIMESTAMPTZ keeps.
    return value if value is None or value.tzinfo else value.replace(tzinfo=timezone.utc)


def iso(value):
    return aware(value).astimezone(timezone.utc).isoformat() if value else None


def check_range(start, end):
    if start is None or end is None:
        raise fail(422, 'INVALID_INPUT', '조회할 기간(start, end)을 지정하세요.')
    start, end = aware(start), aware(end)
    if end <= start or end - start > MAX_RANGE:
        raise fail(422, 'INVALID_INPUT', '조회 기간은 100일 이내여야 합니다.')
    return start, end


def add_months(value, months):
    month = value.month - 1 + months
    year, month = value.year + month // 12, month % 12 + 1
    try:
        return value.replace(year=year, month=month)
    except ValueError:
        return None  # e.g. the 31st in a 30-day month: that month has no occurrence


def occurrences(event, start, end):
    """(start, end) pairs of the event that overlap [start, end), in time order."""
    first, last = aware(event.starts_at), aware(event.ends_at)
    duration = last - first

    def overlaps(begin):
        finish = begin + duration
        return begin < end and (finish > start or (finish == begin and begin >= start))

    if event.recurrence == 'none':
        return [(first, last)] if overlaps(first) else []
    offset = timedelta(minutes=(event.details or {}).get('utc_offset', 0))
    local = (first + offset).replace(tzinfo=None)
    until = aware(event.recurrence_until)
    found = []
    if event.recurrence in STEP:
        step = STEP[event.recurrence]
        index = max(0, (start - duration - first) // step)
        while len(found) < MAX_OCCURRENCES:
            begin = first + step * index
            if begin >= end or (until and begin > until):
                break
            if overlaps(begin):
                found.append((begin, begin + duration))
            index += 1
        return found
    months = max(0, (start.year - first.year) * 12 + start.month - first.month - 1 - duration.days // 28)
    for index in range(months, months + 200):
        moved = add_months(local, index)
        if moved is None:
            continue
        begin = (moved - offset).replace(tzinfo=timezone.utc)
        if begin >= end or (until and begin > until) or len(found) >= MAX_OCCURRENCES:
            break
        if overlaps(begin):
            found.append((begin, begin + duration))
    return found


def participant_rows(db, event_ids):
    rows = {}
    if event_ids:
        for row in db.scalars(select(CommunityEventParticipant).where(CommunityEventParticipant.event_id.in_(event_ids))):
            rows.setdefault(row.event_id, []).append(row)
    return rows


def can_see(event, user_id, participants):
    return (event.visibility == 'community' or event.author_id == user_id or event.assignee_id == user_id
            or any(p.user_id == user_id for p in participants))


def summary(event, begin, finish, people, participants, user_id, repository):
    details = event.details or {}
    mine = next((p.response for p in participants if p.user_id == user_id), None)
    return {'id': event.id, 'repository': {'id': repository.id, 'name': repository.name},
            'title': event.title, 'event_type': event.event_type, 'status': event.status,
            'starts_at': iso(begin), 'ends_at': iso(finish), 'all_day': event.all_day,
            'series_starts_at': iso(event.starts_at), 'recurrence': event.recurrence, 'visibility': event.visibility,
            'assignee': user_view(people.get(event.assignee_id)),
            'participants': [user_view(people.get(p.user_id)) for p in participants if people.get(p.user_id)],
            'my_response': mine, 'author_id': str(event.author_id), 'location': event.location,
            'has_meeting_url': bool(event.meeting_url), 'reminder_minutes': event.reminder_minutes,
            'sprint_id': event.sprint_id, 'milestone_id': event.milestone_id,
            'pull_count': len(details.get('pulls', [])), 'issue_count': len(details.get('issues', [])),
            'release_tag': details.get('release_tag')}


def visible_events(db, user_id, query):
    rows = list(db.scalars(query))
    participants = participant_rows(db, [e.id for e in rows])
    rows = [e for e in rows if can_see(e, user_id, participants.get(e.id, []))]
    people = users_by_id(db, [u for e in rows for u in (e.assignee_id, *[p.user_id for p in participants.get(e.id, [])])])
    return rows, participants, people


def calendar(db, repository_ids, user_id, start, end):
    start, end = check_range(start, end)
    repositories = {r.id: r for r in db.scalars(select(Repository).where(Repository.id.in_(repository_ids)))} if repository_ids else {}
    if not repositories:
        return {'events': [], 'start': iso(start), 'end': iso(end)}
    query = select(CommunityEvent).where(CommunityEvent.repository_id.in_(list(repositories)),
                                         CommunityEvent.deleted_at.is_(None), CommunityEvent.starts_at < end,
                                         or_(CommunityEvent.ends_at >= start, CommunityEvent.recurrence != 'none'))
    rows, participants, people = visible_events(db, user_id, query)
    items = []
    for event in rows:
        for begin, finish in occurrences(event, start, end):
            items.append(summary(event, begin, finish, people, participants.get(event.id, []), user_id,
                                 repositories[event.repository_id]))
    items.sort(key=lambda item: (item['starts_at'], item['title'], item['id']))
    return {'events': items[:MAX_OCCURRENCES], 'start': iso(start), 'end': iso(end),
            'truncated': len(items) > MAX_OCCURRENCES}


def events(db, repository_id, user_id, start=None, end=None, q=None):
    membership(db, repository_id, user_id)
    if q is None:
        return calendar(db, [repository_id], user_id, start, end)
    text = q.strip()
    if not text:
        raise fail(422, 'INVALID_INPUT', '검색어를 입력하세요.')
    repository = db.get(Repository, repository_id)
    query = (select(CommunityEvent).where(CommunityEvent.repository_id == repository_id, CommunityEvent.deleted_at.is_(None),
                                          or_(func.lower(CommunityEvent.title).contains(text.lower(), autoescape=True),
                                              func.lower(CommunityEvent.description).contains(text.lower(), autoescape=True)))
             .order_by(CommunityEvent.starts_at.desc(), CommunityEvent.id.desc()).limit(MAX_SEARCH * 4))
    rows, participants, people = visible_events(db, user_id, query)
    return {'events': [summary(e, aware(e.starts_at), aware(e.ends_at), people, participants.get(e.id, []), user_id, repository)
                       for e in rows[:MAX_SEARCH]], 'query': text}


def my_calendar(db, user_id, start, end):
    """Every community the caller is an active member of, in one calendar."""
    if not user_id:
        raise fail(401, 'AUTH_REQUIRED', '커뮤니티는 로그인한 사용자만 볼 수 있습니다.')
    ids = list(db.scalars(select(CommunityMember.repository_id).where(CommunityMember.user_id == user_id,
                                                                      CommunityMember.status == 'active')))
    return calendar(db, ids, user_id, start, end)


def find_event(db, repository_id, event_id, user_id):
    event = db.get(CommunityEvent, event_id)
    if not event or event.repository_id != repository_id or event.deleted_at:
        raise fail(404, 'NOT_FOUND', '일정을 찾을 수 없습니다.')
    participants = participant_rows(db, [event.id]).get(event.id, [])
    if not can_see(event, user_id, participants):
        # Same answer as a missing event, so a private event's existence does not leak.
        raise fail(404, 'NOT_FOUND', '일정을 찾을 수 없습니다.')
    return event, participants


def linked_event(db, event_id, user_id):
    if not event_id:
        return None
    linked = db.get(CommunityEvent, event_id)
    if not linked or linked.deleted_at:
        return None
    if not can_see(linked, user_id, participant_rows(db, [linked.id]).get(linked.id, [])):
        return {'id': linked.id, 'hidden': True}
    return {'id': linked.id, 'title': linked.title, 'event_type': linked.event_type, 'status': linked.status,
            'starts_at': iso(linked.starts_at), 'ends_at': iso(linked.ends_at), 'all_day': linked.all_day,
            'goal': (linked.details or {}).get('goal'), 'hidden': False}


def event(db, repository_id, user_id, event_id):
    me = membership(db, repository_id, user_id)
    found, participants = find_event(db, repository_id, event_id, user_id)
    repository = db.get(Repository, repository_id)
    details = found.details or {}
    people = users_by_id(db, [found.author_id, found.assignee_id, *[p.user_id for p in participants]])
    base = (repository.url or '').rstrip('/')
    pulls = []
    numbers = details.get('pulls', [])
    rows = {r.pr_number: r for r in db.scalars(select(PullRequest).where(PullRequest.repository_id == repository_id,
                                                                         PullRequest.pr_number.in_(numbers)))} if numbers else {}
    reviews = {}
    if rows:
        for review in db.execute(select(PrReview.pull_request_id, PrReview.reviewer_login)
                                 .where(PrReview.pull_request_id.in_([r.id for r in rows.values()]))):
            if review.reviewer_login:
                reviews.setdefault(review.pull_request_id, set()).add(review.reviewer_login)
    for number in numbers:
        row = rows.get(number)
        pulls.append({'number': number, 'url': f'{base}/pull/{number}', 'found': bool(row),
                      **({'title': row.title, 'state': row.state, 'author_login': row.author_login,
                          'opened_at': iso(row.opened_at), 'merged_at': iso(row.merged_at),
                          'reviewers': sorted(reviews.get(row.id, set()), key=str.lower)} if row else {})})
    release = None
    if details.get('release_tag'):
        tag = details['release_tag']
        row = db.scalar(select(Release).where(Release.repository_id == repository_id, Release.tag_name == tag).limit(1))
        release = {'tag': tag, 'url': f'{base}/releases/tag/{quote(tag, safe="")}', 'found': bool(row),
                   **({'name': row.name, 'published_at': iso(row.published_at), 'prerelease': bool(row.is_prerelease)}
                      if row else {})}
    post = db.get(CommunityPost, found.post_id) if found.post_id else None
    view = summary(found, aware(found.starts_at), aware(found.ends_at), people, participants, user_id, repository)
    view.update({
        'description': found.description, 'meeting_url': found.meeting_url, 'recurrence_until': iso(found.recurrence_until),
        'author': user_view(people.get(found.author_id)), 'created_at': iso(found.created_at),
        'updated_at': iso(found.updated_at),
        'responses': [{'user': user_view(people.get(p.user_id)), 'response': p.response, 'responded_at': iso(p.responded_at)}
                      for p in participants if people.get(p.user_id)],
        'goal': details.get('goal'), 'version': details.get('version'), 'environment': details.get('environment'),
        'issues': [{'number': n, 'url': f'{base}/issues/{n}'} for n in details.get('issues', [])],
        'pulls': pulls, 'release': release, 'attachments': details.get('attachments', []),
        'sprint': linked_event(db, found.sprint_id, user_id), 'milestone': linked_event(db, found.milestone_id, user_id),
        'post': {'id': post.id, 'title': post.title} if post and not post.deleted_at else None,
        'repository_url': base, 'can_edit': found.author_id == user_id or me.role == 'admin',
        'can_respond': any(p.user_id == user_id for p in participants)})
    return view


def link_options(db, repository_id, user_id):
    """What the event form can link to: collected pull requests and releases, and this community's sprints and milestones."""
    membership(db, repository_id, user_id)
    pulls = db.scalars(select(PullRequest).where(PullRequest.repository_id == repository_id)
                       .order_by(PullRequest.opened_at.desc().nullslast(), PullRequest.pr_number.desc()).limit(100))
    releases = db.scalars(select(Release).where(Release.repository_id == repository_id)
                          .order_by(Release.published_at.desc().nullslast(), Release.id.desc()).limit(50))
    query = (select(CommunityEvent).where(CommunityEvent.repository_id == repository_id, CommunityEvent.deleted_at.is_(None),
                                          CommunityEvent.event_type.in_(('sprint', 'milestone')))
             .order_by(CommunityEvent.starts_at.desc()).limit(100))
    rows, _, _ = visible_events(db, user_id, query)
    members = db.scalars(select(CommunityMember).where(CommunityMember.repository_id == repository_id,
                                                       CommunityMember.status == 'active'))
    people = users_by_id(db, [m.user_id for m in members])
    return {'pulls': [{'number': p.pr_number, 'title': p.title, 'state': p.state} for p in pulls],
            'releases': [{'tag': r.tag_name, 'name': r.name, 'published_at': iso(r.published_at)} for r in releases],
            'sprints': [{'id': e.id, 'title': e.title, 'starts_at': iso(e.starts_at), 'ends_at': iso(e.ends_at)}
                        for e in rows if e.event_type == 'sprint'],
            'milestones': [{'id': e.id, 'title': e.title, 'starts_at': iso(e.starts_at), 'ends_at': iso(e.ends_at)}
                           for e in rows if e.event_type == 'milestone'],
            'members': sorted((user_view(u) for u in people.values()), key=lambda u: (u['github_login'] or '').lower())}


def check_links(db, repository_id, body, event_id=None):
    people = {body.assignee_id, *body.participant_ids} - {None}
    if people:
        active = set(db.scalars(select(CommunityMember.user_id).where(CommunityMember.repository_id == repository_id,
                                                                      CommunityMember.status == 'active',
                                                                      CommunityMember.user_id.in_(people))))
        if people - active:
            raise fail(422, 'INVALID_INPUT', '담당자와 참여자는 이 커뮤니티의 멤버여야 합니다.')
    for key, kind, label in (('sprint_id', 'sprint', '스프린트'), ('milestone_id', 'milestone', '마일스톤')):
        linked_id = getattr(body, key)
        if linked_id is None:
            continue
        linked = db.get(CommunityEvent, linked_id)
        if (not linked or linked.repository_id != repository_id or linked.deleted_at or linked.event_type != kind
                or linked.id == event_id):
            raise fail(422, 'INVALID_INPUT', f'연결할 {label}를 이 커뮤니티 일정에서 고르세요.')
    if body.post_id is not None:
        post = db.get(CommunityPost, body.post_id)
        if not post or post.repository_id != repository_id or post.deleted_at:
            raise fail(422, 'INVALID_INPUT', '연결할 글을 찾을 수 없습니다.')


def details_of(body):
    offset = body.starts_at.utcoffset()
    values = {'utc_offset': int(offset.total_seconds() // 60) if offset else 0,
              'goal': body.goal, 'version': body.version, 'environment': body.environment,
              'release_tag': body.release_tag,
              'issues': sorted(set(body.issue_numbers)), 'pulls': sorted(set(body.pull_numbers)),
              'attachments': [a.model_dump() for a in body.attachments]}
    return {k: v for k, v in values.items() if v not in (None, [], '')} | {'utc_offset': values['utc_offset']}


def apply(row, body):
    row.title, row.description = body.title.strip(), body.description
    row.event_type, row.status = body.event_type, body.status
    utc = timezone.utc
    row.starts_at, row.ends_at, row.all_day = body.starts_at.astimezone(utc), body.ends_at.astimezone(utc), body.all_day
    row.assignee_id, row.location, row.meeting_url = body.assignee_id, body.location, body.meeting_url
    row.visibility, row.reminder_minutes = body.visibility, body.reminder_minutes
    row.recurrence = body.recurrence
    row.recurrence_until = body.recurrence_until.astimezone(utc) if body.recurrence_until else None
    row.sprint_id, row.milestone_id, row.post_id = body.sprint_id, body.milestone_id, body.post_id
    row.details = details_of(body)
    if not row.title:
        raise fail(422, 'INVALID_INPUT', '제목을 입력하세요.')


def set_participants(db, event_id, user_ids):
    """Keep the answers of people who stay; new people start as pending."""
    wanted = list(dict.fromkeys(user_ids))
    current = {p.user_id: p for p in db.scalars(select(CommunityEventParticipant).where(
        CommunityEventParticipant.event_id == event_id))}
    for user_id, row in current.items():
        if user_id not in wanted:
            db.delete(row)
    for user_id in wanted:
        if user_id not in current:
            db.add(CommunityEventParticipant(event_id=event_id, user_id=user_id, response='pending'))


def throttle(db, repository_id, user_id):
    count = db.scalar(select(func.count()).select_from(CommunityEvent).where(
        CommunityEvent.repository_id == repository_id, CommunityEvent.author_id == user_id,
        CommunityEvent.created_at > now() - EVENT_WINDOW))
    if count >= EVENT_LIMIT:
        raise HTTPException(429, {'code': 'RATE_LIMITED', 'message': '잠시 후 다시 등록해 주세요.'}, headers={'Retry-After': '60'})


def create_event(db, repository_id, user_id, body):
    membership(db, repository_id, user_id)
    throttle(db, repository_id, user_id)
    check_links(db, repository_id, body)
    row = CommunityEvent(repository_id=repository_id, author_id=user_id, created_at=now())
    apply(row, body)
    db.add(row)
    db.flush()
    set_participants(db, row.id, body.participant_ids)
    db.flush()
    return {'id': row.id}


def editable(db, repository_id, user_id, event_id):
    me = membership(db, repository_id, user_id)
    row, _ = find_event(db, repository_id, event_id, user_id)
    if row.author_id != user_id and me.role != 'admin':
        raise fail(403, 'PERMISSION_DENIED', '작성자나 관리자만 고치거나 지울 수 있습니다.')
    return row


def update_event(db, repository_id, user_id, event_id, body):
    row = editable(db, repository_id, user_id, event_id)
    check_links(db, repository_id, body, event_id)
    apply(row, body)
    row.updated_at = now()
    set_participants(db, row.id, body.participant_ids)
    db.flush()


def delete_event(db, repository_id, user_id, event_id):
    row = editable(db, repository_id, user_id, event_id)
    row.deleted_at = row.deleted_at or now()


def respond(db, repository_id, user_id, event_id, response):
    membership(db, repository_id, user_id)
    _, participants = find_event(db, repository_id, event_id, user_id)
    mine = next((p for p in participants if p.user_id == user_id), None)
    if not mine:
        raise fail(403, 'PERMISSION_DENIED', '참여자로 지정된 사람만 참석 여부를 답할 수 있습니다.')
    mine.response, mine.responded_at = response, now()
