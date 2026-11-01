"""System-browser sign-in that returns to the app's loopback listener (RFC 8252). No typed code, no approve form."""
import secrets
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from app.core.database import get_db
from app.models.desktop_session import DesktopSession
from app.models.user import User
from app.services import desktop_auth as service

router = APIRouter(prefix='/api/desktop/v1', tags=['desktop'])
NO_STORE = {'Cache-Control': 'private, no-store', 'Referrer-Policy': 'no-referrer'}


class CreateFlow(BaseModel):
    model_config = ConfigDict(extra='forbid')
    challenge: str = Field(pattern=r'^[0-9a-f]{64}$')
    deviceLabel: str = Field(min_length=1, max_length=80, pattern=r'^[^\x00-\x1f\x7f]+$')
    redirectPort: int = Field(ge=1024, le=65535)


class Exchange(BaseModel):
    model_config = ConfigDict(extra='forbid')
    flowId: UUID
    pollSecret: str = Field(pattern=r'^[A-Za-z0-9_-]{43,128}$')
    verifier: str = Field(pattern=r'^[A-Za-z0-9_-]{43,128}$')
    callbackCode: str | None = Field(default=None, pattern=r'^[A-Za-z0-9_-]{43}$')


def enabled():
    service.settings()


router.dependencies.append(Depends(enabled))


@router.post('/auth/transactions', status_code=201)
def transactions(body: CreateFlow, request: Request, db=Depends(get_db)):
    if request.headers.get('authorization') or request.headers.get('cookie'):
        raise service.unavailable()
    result = service.create_flow(db, body.challenge, body.deviceLabel, request.client.host if request.client else 'unknown',
                                body.redirectPort)
    db.commit()
    return JSONResponse(result, 201, headers=NO_STORE)


@router.get('/auth/authorize')
def authorize_page(flow: UUID, request: Request, port: int | None = Query(default=None, ge=1024, le=65535),
                   db=Depends(get_db)):
    if getattr(request.state, 'desktop_principal', None):
        raise service.unavailable()
    # The GitHub callback returns here without the port; the signed session carries it across the round trip.
    if port is None:
        port = request.session.get('desktop_port') if request.session.get('desktop_flow') == str(flow) else None
    if not isinstance(port, int) or not 1024 <= port <= 65535:
        raise service.unavailable()
    binding = request.session.get('desktop_binding') or secrets.token_urlsafe(32)
    request.session['desktop_binding'] = binding
    service.bind_browser(db, flow, binding)
    request.session['desktop_flow'] = str(flow)
    request.session['desktop_port'] = port
    db.commit()
    # Every app sign-in goes through GitHub once, even with a browser session, so the stored GitHub token is fresh.
    if not request.session.get('user_id') or request.session.get('desktop_github_flow') != str(flow):
        return RedirectResponse('/api/auth/github', status_code=303, headers=NO_STORE)
    user = db.get(User, UUID(request.session['user_id']))
    if not user:
        raise service.unavailable()
    # Owner decision: signing in to GitHub is the approval. Authorizing alone issues nothing; the session
    # needs the code below, which only this browser carries, and only to its own machine's loopback address.
    service.authorize(db, flow, binding, user.id)
    db.commit()
    for name in ('desktop_flow', 'desktop_port', 'desktop_binding', 'desktop_github_flow'):
        request.session.pop(name, None)
    target = f'http://127.0.0.1:{port}/callback?flow={flow}&code={service.callback_code(flow)}'
    return RedirectResponse(target, status_code=303, headers=NO_STORE)


@router.post('/auth/exchange')
def exchange(body: Exchange, request: Request, db=Depends(get_db)):
    try:
        key = str(UUID(request.headers.get('idempotency-key', '')))
    except ValueError:
        raise HTTPException(422, '요청 형식이 올바르지 않습니다.') from None
    if request.headers.get('authorization') or request.headers.get('cookie'):
        raise service.unavailable()
    status, result = service.exchange(db, body.flowId, body.pollSecret, body.verifier, key, body.callbackCode)
    db.commit()
    return JSONResponse(result, status, headers={**NO_STORE, **({'Retry-After': '2'} if status == 202 else {})})


@router.get('/session')
def session(request: Request, db=Depends(get_db)):
    principal = getattr(request.state, 'desktop_principal', None)
    if not isinstance(principal, service.DesktopPrincipal):
        raise service.unavailable()
    stored = db.get(DesktopSession, principal.session_id)
    user = db.get(User, principal.user_id)
    return JSONResponse({'sessionId': str(stored.id), 'user': service.user_view(user),
        'capabilities': stored.capabilities, 'expiresAt': stored.expires_at.isoformat(),
        'apiCapabilities': service.API_CAPABILITIES}, headers=NO_STORE)


@router.delete('/session', status_code=204)
def logout(request: Request, db=Depends(get_db)):
    principal = getattr(request.state, 'desktop_principal', None)
    if not isinstance(principal, service.DesktopPrincipal):
        raise service.unavailable()
    stored = db.get(DesktopSession, principal.session_id)
    if not stored.revoked_at:
        stored.revoked_at = service.now()
    db.commit()
    return Response(status_code=204, headers=NO_STORE)
