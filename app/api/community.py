"""Project community HTTP API. Members only; every change needs CSRF (web) or the desktop bearer session."""
import os
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Request, Response
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from app.api.ai.dependencies import require_csrf
from app.api.auth import _get_session_user_id
from app.core.database import get_db
from app.schemas.community import (
    ChatReadRequest,
    CollabRelayRequest,
    CollabSeatRequest,
    CreateChatMessageRequest,
    CreateCollabSessionRequest,
    CreateCommentRequest,
    CreateEventRequest,
    CreateInviteRequest,
    EventResponseRequest,
    CreatePostRequest,
    InvitesSeenRequest,
    NotificationSettingsRequest,
    RedeemInviteRequest,
    TransferAdminRequest,
    UpdateCommentRequest,
    UpdateEventRequest,
    UpdateMemberRequest,
    UpdatePostRequest,
    VoiceSessionRequest,
    VoiceSignalRequest,
)
from app.services import community as service
from app.services import community_chat as chat
from app.services import community_collab as collab
from app.services import community_events as events
from app.services import community_notifications as notifications
from app.services import community_voice as voice

router = APIRouter(prefix='/api/community/v1', tags=['community'], dependencies=[Depends(require_csrf)])
NO_STORE = {'Cache-Control': 'private, no-store'}


def done(db, response: Response, value=None):
    db.commit()
    response.headers.update(NO_STORE)
    return value if value is not None else {'ok': True}


def invite_url(request: Request, token):
    # The token rides in the fragment, so it never reaches server or proxy access logs when the link is opened.
    origin = (os.getenv('DESKTOP_PUBLIC_ORIGIN') or str(request.base_url)).rstrip('/')
    return f'{origin}/pages/community.html#invite={token}'


@router.get('/communities')
def my_communities(request: Request, response: Response, db=Depends(get_db)):
    return done(db, response, service.my_communities(db, _get_session_user_id(request)))


@router.get('/repositories/{repository_id}')
def community(repository_id: int, request: Request, response: Response, db=Depends(get_db)):
    member = service.membership(db, repository_id, _get_session_user_id(request))
    return done(db, response, service.community_view(db, repository_id, member))


@router.get('/repositories/{repository_id}/members')
def members(repository_id: int, request: Request, response: Response, db=Depends(get_db)):
    return done(db, response, service.members(db, repository_id, _get_session_user_id(request)))


@router.patch('/repositories/{repository_id}/members/{user_id}')
def update_member(repository_id: int, user_id: UUID, body: UpdateMemberRequest, request: Request, response: Response,
                  db=Depends(get_db)):
    service.update_member(db, repository_id, _get_session_user_id(request), user_id, body.status)
    result = done(db, response)
    if body.status == 'removed':
        # Their open chat stream ends now instead of on the next reconnect.
        chat.publish(repository_id, 'removed', {'user_id': str(user_id)})
        voice.drop_user(db, repository_id, user_id)
        collab.drop_user(db, repository_id, user_id)
    return result


@router.post('/repositories/{repository_id}/admin')
def transfer_admin(repository_id: int, body: TransferAdminRequest, request: Request, response: Response,
                   db=Depends(get_db)):
    service.transfer_admin(db, repository_id, _get_session_user_id(request), body.user_id)
    return done(db, response)


@router.get('/repositories/{repository_id}/invites')
def invites(repository_id: int, request: Request, response: Response, db=Depends(get_db)):
    return done(db, response, service.invites(db, repository_id, _get_session_user_id(request)))


@router.post('/repositories/{repository_id}/invites', status_code=201)
def create_invite(repository_id: int, body: CreateInviteRequest, request: Request, response: Response,
                  db=Depends(get_db)):
    result = service.create_invite(db, repository_id, _get_session_user_id(request), body.expires_in_hours,
                                   body.max_uses)
    result['url'] = invite_url(request, result.pop('token'))
    return done(db, response, result)


@router.delete('/repositories/{repository_id}/invites/{invite_id}')
def revoke_invite(repository_id: int, invite_id: UUID, request: Request, response: Response, db=Depends(get_db)):
    service.revoke_invite(db, repository_id, _get_session_user_id(request), invite_id)
    return done(db, response)


@router.post('/invites/redeem')
def redeem_invite(body: RedeemInviteRequest, request: Request, response: Response, db=Depends(get_db)):
    return done(db, response, service.redeem_invite(db, _get_session_user_id(request), body.token))


@router.get('/repositories/{repository_id}/posts')
def posts(repository_id: int, request: Request, response: Response, before: int | None = Query(default=None, ge=1),
          limit: int = Query(default=20, ge=1, le=50), db=Depends(get_db)):
    return done(db, response, service.posts(db, repository_id, _get_session_user_id(request), before, limit))


@router.post('/repositories/{repository_id}/posts', status_code=201)
def create_post(repository_id: int, body: CreatePostRequest, request: Request, response: Response, db=Depends(get_db)):
    return done(db, response, service.create_post(db, repository_id, _get_session_user_id(request), body.title,
                                                  body.body))


@router.get('/repositories/{repository_id}/posts/{post_id}')
def post(repository_id: int, post_id: int, request: Request, response: Response, db=Depends(get_db)):
    return done(db, response, service.post(db, repository_id, _get_session_user_id(request), post_id))


@router.patch('/repositories/{repository_id}/posts/{post_id}')
def update_post(repository_id: int, post_id: int, body: UpdatePostRequest, request: Request, response: Response,
                db=Depends(get_db)):
    service.update_post(db, repository_id, _get_session_user_id(request), post_id, body.title, body.body)
    return done(db, response)


@router.delete('/repositories/{repository_id}/posts/{post_id}')
def delete_post(repository_id: int, post_id: int, request: Request, response: Response, db=Depends(get_db)):
    service.delete_post(db, repository_id, _get_session_user_id(request), post_id)
    return done(db, response)


@router.post('/repositories/{repository_id}/posts/{post_id}/comments', status_code=201)
def create_comment(repository_id: int, post_id: int, body: CreateCommentRequest, request: Request, response: Response,
                   db=Depends(get_db)):
    return done(db, response, service.create_comment(db, repository_id, _get_session_user_id(request), post_id,
                                                     body.body))


@router.patch('/repositories/{repository_id}/comments/{comment_id}')
def update_comment(repository_id: int, comment_id: int, body: UpdateCommentRequest, request: Request,
                   response: Response, db=Depends(get_db)):
    service.update_comment(db, repository_id, _get_session_user_id(request), comment_id, body.body)
    return done(db, response)


@router.delete('/repositories/{repository_id}/comments/{comment_id}')
def delete_comment(repository_id: int, comment_id: int, request: Request, response: Response, db=Depends(get_db)):
    service.delete_comment(db, repository_id, _get_session_user_id(request), comment_id)
    return done(db, response)


@router.get('/repositories/{repository_id}/chat/messages')
def chat_messages(repository_id: int, request: Request, response: Response, before: int | None = Query(default=None, ge=1),
                  limit: int = Query(default=50, ge=1, le=100), db=Depends(get_db)):
    return done(db, response, chat.history(db, repository_id, _get_session_user_id(request), before, limit))


@router.post('/repositories/{repository_id}/chat/messages', status_code=201)
def send_chat_message(repository_id: int, body: CreateChatMessageRequest, request: Request, response: Response,
                      db=Depends(get_db)):
    message = chat.send(db, repository_id, _get_session_user_id(request), body.body)
    result = done(db, response, message)
    chat.publish(repository_id, 'message', message)
    return result


@router.delete('/repositories/{repository_id}/chat/messages/{message_id}')
def delete_chat_message(repository_id: int, message_id: int, request: Request, response: Response, db=Depends(get_db)):
    chat.delete(db, repository_id, _get_session_user_id(request), message_id)
    result = done(db, response)
    chat.publish(repository_id, 'deleted', {'id': message_id})
    return result


# Notifications (2026-11-11): the app polls this summary and announces new chat and schedule invitations.
@router.get('/notifications')
def notification_summary(request: Request, response: Response, db=Depends(get_db)):
    return done(db, response, notifications.summary(db, _get_session_user_id(request)))


@router.post('/notifications/invites/seen')
def notification_invites_seen(body: InvitesSeenRequest, request: Request, response: Response, db=Depends(get_db)):
    return done(db, response, notifications.mark_invites_seen(db, _get_session_user_id(request), body.event_ids))


@router.put('/repositories/{repository_id}/chat/read')
def chat_read(repository_id: int, body: ChatReadRequest, request: Request, response: Response, db=Depends(get_db)):
    return done(db, response, notifications.mark_chat_read(db, repository_id, _get_session_user_id(request), body.message_id))


@router.put('/repositories/{repository_id}/notifications')
def notification_settings(repository_id: int, body: NotificationSettingsRequest, request: Request, response: Response,
                          db=Depends(get_db)):
    return done(db, response, notifications.set_muted(db, repository_id, _get_session_user_id(request), body.muted))


@router.get('/repositories/{repository_id}/voice')
def voice_room(repository_id: int, request: Request, response: Response, db=Depends(get_db)):
    return done(db, response, voice.room(db, repository_id, _get_session_user_id(request)))


@router.post('/repositories/{repository_id}/voice/join')
def join_voice(repository_id: int, request: Request, response: Response, db=Depends(get_db)):
    return done(db, response, voice.join(db, repository_id, _get_session_user_id(request)))


@router.post('/repositories/{repository_id}/voice/heartbeat')
def voice_heartbeat(repository_id: int, body: VoiceSessionRequest, request: Request, response: Response, db=Depends(get_db)):
    return done(db, response, voice.heartbeat(db, repository_id, _get_session_user_id(request), body.session_id))


@router.post('/repositories/{repository_id}/voice/leave')
def leave_voice(repository_id: int, body: VoiceSessionRequest, request: Request, response: Response, db=Depends(get_db)):
    return done(db, response, voice.leave(db, repository_id, _get_session_user_id(request), body.session_id))


@router.post('/repositories/{repository_id}/voice/signal')
def voice_signal(repository_id: int, body: VoiceSignalRequest, request: Request, response: Response, db=Depends(get_db)):
    return done(db, response, voice.signal(db, repository_id, _get_session_user_id(request), body))


# Schedule (2026-11-07). A calendar range is at most 100 days; `q` searches titles and descriptions instead.
@router.get('/events')
def my_events(request: Request, response: Response, start: datetime | None = Query(default=None),
              end: datetime | None = Query(default=None), db=Depends(get_db)):
    return done(db, response, events.my_calendar(db, _get_session_user_id(request), start, end))


@router.get('/repositories/{repository_id}/events')
def community_events(repository_id: int, request: Request, response: Response, start: datetime | None = Query(default=None),
                     end: datetime | None = Query(default=None), q: str | None = Query(default=None, max_length=100),
                     db=Depends(get_db)):
    return done(db, response, events.events(db, repository_id, _get_session_user_id(request), start, end, q))


@router.get('/repositories/{repository_id}/event-links')
def event_links(repository_id: int, request: Request, response: Response, db=Depends(get_db)):
    return done(db, response, events.link_options(db, repository_id, _get_session_user_id(request)))


@router.post('/repositories/{repository_id}/events', status_code=201)
def create_event(repository_id: int, body: CreateEventRequest, request: Request, response: Response, db=Depends(get_db)):
    return done(db, response, events.create_event(db, repository_id, _get_session_user_id(request), body))


@router.get('/repositories/{repository_id}/events/{event_id}')
def community_event(repository_id: int, event_id: int, request: Request, response: Response, db=Depends(get_db)):
    return done(db, response, events.event(db, repository_id, _get_session_user_id(request), event_id))


@router.patch('/repositories/{repository_id}/events/{event_id}')
def update_event(repository_id: int, event_id: int, body: UpdateEventRequest, request: Request, response: Response,
                 db=Depends(get_db)):
    events.update_event(db, repository_id, _get_session_user_id(request), event_id, body)
    return done(db, response)


@router.delete('/repositories/{repository_id}/events/{event_id}')
def delete_event(repository_id: int, event_id: int, request: Request, response: Response, db=Depends(get_db)):
    events.delete_event(db, repository_id, _get_session_user_id(request), event_id)
    return done(db, response)


@router.put('/repositories/{repository_id}/events/{event_id}/response')
def respond_event(repository_id: int, event_id: int, body: EventResponseRequest, request: Request, response: Response,
                  db=Depends(get_db)):
    events.respond(db, repository_id, _get_session_user_id(request), event_id, body.response)
    return done(db, response)


SEAT = Path(pattern=r'^[A-Za-z0-9_-]{22}$')


@router.get('/repositories/{repository_id}/collab')
def collab_sessions(repository_id: int, request: Request, response: Response, db=Depends(get_db)):
    return done(db, response, collab.listing(db, repository_id, _get_session_user_id(request)))


@router.post('/repositories/{repository_id}/collab', status_code=201)
def create_collab(repository_id: int, body: CreateCollabSessionRequest, request: Request, response: Response, db=Depends(get_db)):
    return done(db, response, collab.create(db, repository_id, _get_session_user_id(request), body))


@router.post('/repositories/{repository_id}/collab/{session_id}/join')
def join_collab(repository_id: int, request: Request, response: Response, session_id: str = SEAT, db=Depends(get_db)):
    return done(db, response, collab.join(db, repository_id, _get_session_user_id(request), session_id))


@router.post('/repositories/{repository_id}/collab/{session_id}/heartbeat')
def collab_heartbeat(repository_id: int, body: CollabSeatRequest, request: Request, response: Response, session_id: str = SEAT,
                     db=Depends(get_db)):
    return done(db, response, collab.heartbeat(db, repository_id, _get_session_user_id(request), session_id, body.seat_id))


@router.post('/repositories/{repository_id}/collab/{session_id}/leave')
def leave_collab(repository_id: int, body: CollabSeatRequest, request: Request, response: Response, session_id: str = SEAT,
                 db=Depends(get_db)):
    return done(db, response, collab.leave(db, repository_id, _get_session_user_id(request), session_id, body.seat_id))


@router.post('/repositories/{repository_id}/collab/{session_id}/relay')
def relay_collab(repository_id: int, body: CollabRelayRequest, request: Request, response: Response, session_id: str = SEAT,
                 db=Depends(get_db)):
    return done(db, response, collab.relay(db, repository_id, _get_session_user_id(request), session_id, body))


@router.get('/repositories/{repository_id}/chat/stream')
async def chat_stream(repository_id: int, request: Request):
    """Server-sent events: ready, message, deleted, presence, voice, voice-signal (own seat only), collab-sessions, collab (own seats only), removed. Membership is checked before the first byte."""
    user_id = _get_session_user_id(request)
    await run_in_threadpool(chat.check_member, repository_id, user_id)
    return StreamingResponse(chat.stream(repository_id, user_id, request.is_disconnected), media_type='text/event-stream',
                             headers={'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'})
