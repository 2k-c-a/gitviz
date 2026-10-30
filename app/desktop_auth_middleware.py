"""Only this middleware can install a verified desktop principal."""
import os

from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse
from uuid import uuid4

from app.core.database import SessionLocal
from app.services.desktop_auth import authenticate


class DesktopAuthMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        if scope['method'] == 'POST' and scope['path'] in {
                '/api/desktop/v1/auth/transactions', '/api/desktop/v1/auth/exchange'}:
            body = bytearray()
            while True:
                message = await receive()
                if message['type'] == 'http.disconnect':
                    return
                body.extend(message.get('body', b''))
                if len(body) > 4096:
                    return await JSONResponse({'detail': '요청이 너무 큽니다.'}, 413)(scope, receive, send)
                if not message.get('more_body'):
                    break
            original_receive = receive
            consumed = False
            async def bounded_receive():
                nonlocal consumed
                if consumed:
                    return await original_receive()
                consumed = True
                return {'type': 'http.request', 'body': bytes(body), 'more_body': False}
            receive = bounded_receive
        headers = list(scope.get('headers', []))
        authorizations = [v.decode('latin1') for k, v in headers if k.lower() == b'authorization']
        if os.getenv('DESKTOP_AUTH_ENABLED', '0') != '1' or not any(
                value.startswith('Bearer ') for value in authorizations):
            return await self.app(scope, receive, send)
        cookie = ';'.join(v.decode('latin1') for k, v in headers if k.lower() == b'cookie')
        mixed = any(part.strip().split('=', 1)[0] == 'gitviz_session' for part in cookie.split(';'))
        try:
            if len(authorizations) != 1 or mixed or not authorizations[0].startswith('Bearer '):
                raise HTTPException(401)
            token = authorizations[0][7:]
            def check():
                with SessionLocal() as db:
                    principal = authenticate(db, token, allow_revoked=(scope['method'] == 'DELETE'
                        and scope['path'] == '/api/desktop/v1/session'))
                    db.commit()
                    return principal
            principal = await run_in_threadpool(check)
        except HTTPException:
            path = scope['path']
            body = ({'error': {'code': 'AUTH_REQUIRED', 'message': '로그인이 필요합니다.',
                     'retryable': False, 'details': {}, 'request_id': str(uuid4())}}
                    if path.startswith('/api/ai/') else {'detail': '로그인이 필요합니다.'})
            return await JSONResponse(body, 401, headers={'Cache-Control': 'private, no-store'})(scope, receive, send)
        scope.setdefault('state', {})['desktop_principal'] = principal
        await self.app(scope, receive, send)
