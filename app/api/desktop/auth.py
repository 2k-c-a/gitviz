"""System-browser approval. GET never authorizes a desktop session."""
import hmac
import html
import json
import secrets
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

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


class Exchange(BaseModel):
    model_config = ConfigDict(extra='forbid')
    flowId: UUID
    pollSecret: str = Field(pattern=r'^[A-Za-z0-9_-]{43,128}$')
    verifier: str = Field(pattern=r'^[A-Za-z0-9_-]{43,128}$')


def enabled():
    service.settings()


router.dependencies.append(Depends(enabled))


@router.post('/auth/transactions', status_code=201)
def transactions(body: CreateFlow, request: Request, db=Depends(get_db)):
    if request.headers.get('authorization') or request.headers.get('cookie'):
        raise service.unavailable()
    result = service.create_flow(db, body.challenge, body.deviceLabel, request.client.host if request.client else 'unknown')
    db.commit()
    return JSONResponse(result, 201, headers=NO_STORE)


@router.get('/auth/authorize')
def authorize_page(flow: UUID, request: Request, db=Depends(get_db)):
    if getattr(request.state, 'desktop_principal', None):
        raise service.unavailable()
    binding = request.session.get('desktop_binding') or secrets.token_urlsafe(32)
    request.session['desktop_binding'] = binding
    transaction = service.bind_browser(db, flow, binding)
    request.session['desktop_flow'] = str(flow)
    db.commit()
    if not request.session.get('user_id'):
        return RedirectResponse('/api/auth/github', status_code=303, headers=NO_STORE)
    csrf = secrets.token_urlsafe(32)
    request.session['desktop_csrf'] = csrf
    user = db.get(User, UUID(request.session['user_id']))
    if not user:
        raise service.unavailable()
    code = html.escape(transaction.display_code)
    page = (f'<!doctype html><html lang="ko"><meta charset="utf-8"><title>GitViz 로그인 승인</title>'
        f'<h1>GitViz Desktop 로그인</h1><p>앱: {html.escape(transaction.device_label)}</p>'
        f'<p>계정: {html.escape(user.github_login)}</p><p>앱에 표시된 코드와 비교하세요: <strong>{code}</strong></p>'
        f'<form method="post"><input type="hidden" name="flowId" value="{flow}">'
        f'<input type="hidden" name="csrf" value="{csrf}"><label>앱 확인 코드 <input name="displayCode" required maxlength="12"></label>'
        '<button name="approve" value="true">승인</button><button name="approve" value="false">거절</button></form></html>')
    # Under no-referrer, browsers send `Origin: null` on this form's same-origin POST, which the Origin check rejects.
    return HTMLResponse(page, headers={**NO_STORE, 'Referrer-Policy': 'same-origin', 'Content-Security-Policy':
        "default-src 'none'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"})


@router.post('/auth/authorize')
async def authorize_browser(request: Request, db=Depends(get_db)):
    # Parse a tiny standard browser form without adding multipart dependencies.
    from urllib.parse import parse_qs
    origin, _ = service.settings()
    if request.headers.get('origin') != origin or getattr(request.state, 'desktop_principal', None):
        raise service.unavailable()
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 2048:
            raise HTTPException(413, '요청이 너무 큽니다.')
    try:
        if request.headers.get('content-type', '').split(';')[0] == 'application/json':
            body = json.loads(raw)
        else:
            form = parse_qs(raw.decode('utf-8'), strict_parsing=True, max_num_fields=4)
            if any(len(v) != 1 for v in form.values()):
                raise ValueError()
            body = {k: v[0] for k, v in form.items()}
        if set(body) != {'flowId', 'displayCode', 'approve', 'csrf'}:
            raise ValueError()
        flow = UUID(body['flowId'])
        approve = body['approve']
        if approve not in ('true', 'false', True, False) or not isinstance(body['csrf'], str):
            raise ValueError()
        if (request.session.get('desktop_flow') != str(flow) or not request.session.get('user_id')
                or not hmac.compare_digest(request.session.get('desktop_csrf', ''), body['csrf'])):
            raise ValueError()
        def commit_approval():
            service.authorize(db, flow, request.session['desktop_binding'], UUID(request.session['user_id']),
                              body['displayCode'], approve in ('true', True))
            db.commit()
        await run_in_threadpool(commit_approval)
    except (ValueError, KeyError, TypeError):
        raise service.unavailable() from None
    for name in ('desktop_csrf', 'desktop_flow', 'desktop_binding'):
        request.session.pop(name, None)
    return HTMLResponse('<!doctype html><meta charset="utf-8"><p>처리했습니다. 앱으로 돌아가세요.</p>', headers=NO_STORE)


@router.post('/auth/exchange')
def exchange(body: Exchange, request: Request, db=Depends(get_db)):
    try:
        key = str(UUID(request.headers.get('idempotency-key', '')))
    except ValueError:
        raise HTTPException(422, '요청 형식이 올바르지 않습니다.') from None
    if request.headers.get('authorization') or request.headers.get('cookie'):
        raise service.unavailable()
    status, result = service.exchange(db, body.flowId, body.pollSecret, body.verifier, key)
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
