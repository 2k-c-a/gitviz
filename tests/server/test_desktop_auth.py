"""P03 HTTP/state tests. Default SQLite is a fixture, never a PG acceptance claim.

--postgres uses only an explicitly named gitviz_test database and a fresh schema.
Real migration execution remains a separately approved command.
"""

import os
os.environ['PYTHON_DOTENV_DISABLED'] = '1'
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
POSTGRES = "--postgres" in sys.argv
if POSTGRES:
    sys.argv.remove("--postgres")
    sys.path.insert(0, str(ROOT / "tests" / "server"))
    from ai_test_support import require_test_database

    URL = require_test_database()
else:
    URL = "sqlite://"
    os.environ["DATABASE_URL"] = "postgresql://gitviz_test:unused@localhost/gitviz_test"
    os.environ["DATABASE_URL_ASYNC"] = ""
os.environ["DESKTOP_AUTH_ENABLED"] = "1"
os.environ["DESKTOP_PUBLIC_ORIGIN"] = "https://desktop.test"
os.environ["DESKTOP_AUTH_RESPONSE_KEY"] = "a1" * 32
os.environ["SESSION_COOKIE_SECURE"] = "1"

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy import create_engine, event, text, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.dialects.postgresql import JSONB
from app.api.desktop.auth import router
from app.core.database import get_db
from app.desktop_auth_middleware import DesktopAuthMiddleware
from app.models.user import User
from app.models.desktop_session import DesktopAuthTransaction, DesktopSession
from app.services import desktop_auth as service
from app.api.auth import _get_session_user_id
from app.api.ai.dependencies import require_csrf
from app.api.auth import router as web_auth_router
from app.database import get_db as get_async_db


@compiles(JSONB, "sqlite")
def sqlite_json(type_, compiler, **kw):
    return "JSON"


class FixtureSession(Session):
    def execute(self, statement, *args, **kwargs):
        if not POSTGRES and str(statement).startswith("SELECT pg_advisory_xact_lock"):
            return None  # Fixture only: PG advisory/row locks require the PG run.
        return super().execute(statement, *args, **kwargs)


@event.listens_for(FixtureSession, "loaded_as_persistent")
def sqlite_timezone(session, obj):
    if not POSTGRES:
        for column in obj.__table__.columns:
            value = getattr(obj, column.key)
            if isinstance(value, datetime) and value.tzinfo is None:
                setattr(obj, column.key, value.replace(tzinfo=timezone.utc))


class DesktopAuthTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            URL, **({} if POSTGRES else {"poolclass": StaticPool, "connect_args": {"check_same_thread": False}})
        )
        self.schema = None
        if POSTGRES:
            self.schema = "gitviz_p03_" + uuid4().hex
            with self.engine.begin() as conn:
                conn.execute(text(f"CREATE SCHEMA {self.schema}"))

            @event.listens_for(self.engine, "connect")
            def search_path(connection, record):
                cursor = connection.cursor()
                cursor.execute(f"SET search_path TO {self.schema}")
                cursor.close()

            self.engine.dispose()
        self.db_factory = sessionmaker(self.engine, class_=FixtureSession)
        for table in (User.__table__, DesktopAuthTransaction.__table__, DesktopSession.__table__):
            table.create(self.engine)
        self.user_id = uuid4()
        with self.db_factory.begin() as db:
            db.add(User(id=self.user_id, github_id=123, github_login="tester", display_name="Fixture"))
        app = FastAPI()
        app.add_middleware(DesktopAuthMiddleware)
        app.add_middleware(
            SessionMiddleware, secret_key="p03-http-fixture", https_only=True, session_cookie="gitviz_session"
        )

        def database():
            with self.db_factory() as db:
                yield db

        app.dependency_overrides[get_db] = database
        app.dependency_overrides[get_async_db] = lambda: None
        app.include_router(router)
        app.include_router(web_auth_router)

        @app.get("/fixture/login")
        def login(request: Request):
            request.session["user_id"] = str(self.user_id)
            return {"ok": True}

        @app.get("/fixture/principal")
        def principal(request: Request):
            return {"user": str(_get_session_user_id(request))}

        @app.post("/fixture/mutate")
        def mutate(request: Request):
            require_csrf(request)
            return {"ok": True}

        self.patcher = patch("app.desktop_auth_middleware.SessionLocal", self.db_factory)
        self.patcher.start()
        self.client = TestClient(app, base_url="https://desktop.test", raise_server_exceptions=True)
        self.browser = TestClient(app, base_url="https://desktop.test", follow_redirects=False)
        self.verifier = "v" * 43

    def tearDown(self):
        self.client.close()
        self.browser.close()
        self.patcher.stop()
        if self.schema:
            with self.engine.begin() as conn:
                conn.execute(text(f"DROP SCHEMA {self.schema} CASCADE"))
        self.engine.dispose()

    def create(self):
        response = self.client.post(
            "/api/desktop/v1/auth/transactions",
            json={"challenge": service.digest(self.verifier), "deviceLabel": "Fixture", "redirectPort": 49152},
        )
        self.assertEqual(response.status_code, 201)
        return response.json()

    def approve(self, flow, browser=None):
        """Sign in and follow the server back to the app's loopback address; returns the callback code."""
        browser = browser or self.browser
        browser.get("/fixture/login")
        response = browser.get(flow["browserUrl"].removeprefix("https://desktop.test"))
        self.assertEqual(response.status_code, 303)
        from urllib.parse import parse_qs, urlsplit

        target = urlsplit(response.headers["location"])
        self.assertEqual((target.scheme, target.netloc, target.path), ("http", "127.0.0.1:49152", "/callback"))
        query = parse_qs(target.query)
        self.assertEqual(query["flow"], [flow["flowId"]])
        return query["code"][0]

    def ready(self, flow):
        with self.db_factory.begin() as db:
            row = db.get(DesktopAuthTransaction, __import__("uuid").UUID(flow["flowId"]))
            row.next_poll_at = service.now() - timedelta(seconds=1)

    def exchange(self, flow, key=None, **over):
        body = {"flowId": flow["flowId"], "pollSecret": flow["pollSecret"], "verifier": self.verifier, **over}
        return self.client.post(
            "/api/desktop/v1/auth/exchange", json=body, headers={"Idempotency-Key": key or str(uuid4())}
        )

    def test_flow_requires_browser_approval_and_secret_and_single_key(self):
        flow = self.create()
        self.assertNotIn(flow["pollSecret"], flow["browserUrl"])
        self.assertEqual(self.exchange(flow, pollSecret="x" * 43).status_code, 401)
        self.assertEqual(self.exchange(flow, verifier="x" * 43).status_code, 401)
        self.assertEqual(self.exchange(flow).status_code, 202)
        self.assertEqual(self.exchange(flow).status_code, 429)
        self.assertTrue(flow["browserUrl"].endswith("&port=49152"))
        code = self.approve(flow)
        self.ready(flow)
        # Authorized in the browser, but without the loopback code it is still pending.
        self.assertEqual(self.exchange(flow).status_code, 202)
        self.ready(flow)
        self.assertEqual(self.exchange(flow, callbackCode="x" * 43).status_code, 202)
        self.ready(flow)
        key = str(uuid4())
        result = self.exchange(flow, key, callbackCode=code)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(self.exchange(flow, key).json(), result.json())
        self.assertEqual(self.exchange(flow).status_code, 409)
        with self.db_factory() as db:
            stored = db.scalar(select(DesktopSession))
            self.assertNotEqual(stored.token_hash, result.json()["sessionToken"])
            transaction = db.get(DesktopAuthTransaction, __import__("uuid").UUID(flow["flowId"]))
            self.assertNotIn(result.json()["sessionToken"].encode(), transaction.issuance_response_ciphertext)

    def test_bearer_principal_csrf_mixed_cookie_revocation(self):
        flow = self.create()
        issued = self.exchange(flow, callbackCode=self.approve(flow)).json()
        headers = {"Authorization": "Bearer " + issued["sessionToken"]}
        self.assertEqual(self.client.get("/fixture/principal", headers=headers).json()["user"], str(self.user_id))
        self.assertEqual(self.client.post("/fixture/mutate", headers=headers).status_code, 200)
        self.assertEqual(self.client.get("/api/desktop/v1/session", headers=headers).status_code, 200)
        self.assertEqual(self.browser.get("/fixture/principal", headers=headers).status_code, 401)
        self.assertEqual(
            self.client.post("/fixture/mutate", headers={"Authorization": "Bearer forged"}).status_code, 401
        )
        self.assertEqual(self.client.delete("/api/desktop/v1/session", headers=headers).status_code, 204)
        self.assertEqual(self.client.delete("/api/desktop/v1/session", headers=headers).status_code, 204)
        self.assertEqual(self.client.get("/fixture/principal", headers=headers).status_code, 401)

    def test_binding_csrf_denial_expiry_and_size(self):
        self.assertEqual(self.client.post("/api/desktop/v1/auth/transactions", content=b"x" * 4097).status_code, 413)
        self.assertEqual(
            self.client.post(
                "/api/desktop/v1/auth/transactions", json={"challenge": "a" * 64, "deviceLabel": "x", "extra": True}
            ).status_code,
            422,
        )
        flow = self.create()
        # The approve form is gone: nothing but the GET sign-in can authorize a flow.
        self.assertEqual(
            self.browser.post("/api/desktop/v1/auth/authorize", json={"flowId": flow["flowId"]}).status_code, 405
        )
        # Without a loopback port there is nowhere safe to deliver the code.
        self.assertEqual(
            self.browser.get("/api/desktop/v1/auth/authorize", params={"flow": flow["flowId"]}).status_code, 401
        )
        self.assertEqual(self.client.post("/api/desktop/v1/auth/transactions",
            json={"challenge": "a" * 64, "deviceLabel": "x", "redirectPort": 80}).status_code, 422)
        self.browser.get(flow["browserUrl"].removeprefix("https://desktop.test"))
        with self.db_factory.begin() as db:
            row = db.get(DesktopAuthTransaction, __import__("uuid").UUID(flow["flowId"]))
            row.expires_at = service.now() - timedelta(seconds=1)
        self.assertEqual(self.exchange(flow).status_code, 401)

    def test_forwarded_link_signs_in_nobody_but_the_app_that_opened_it(self):
        flow = self.create()
        victim = TestClient(self.client.app, base_url="https://desktop.test", follow_redirects=False)
        try:
            # Someone else's browser completes the link; the code goes to that person's own 127.0.0.1.
            code = self.approve(flow, victim)
            self.ready(flow)
            self.assertEqual(self.exchange(flow).status_code, 202)
            # A second browser cannot take over a flow already bound to the first.
            self.browser.get("/fixture/login")
            self.assertEqual(
                self.browser.get(flow["browserUrl"].removeprefix("https://desktop.test")).status_code, 401
            )
            self.assertEqual(len(code), 43)
        finally:
            victim.close()

    def test_disabled_authorization_preserves_public_responses(self):
        for path in ('/', '/pages/search.html'):
            self.client.app.add_api_route(path, lambda: {'public': True}, methods=['GET'])
        with patch.dict(os.environ, DESKTOP_AUTH_ENABLED='0'):
            for path in ('/', '/pages/search.html', '/api/auth/csrf'):
                baseline = self.client.get(path)
                self.assertEqual(baseline.status_code, 200)
                for header in ('Bearer invalid', 'Basic dXNlcjpwYXNz'):
                    result = self.client.get(path, headers={'Authorization': header})
                    self.assertEqual(result.status_code, baseline.status_code)
                    if path != '/api/auth/csrf':
                        self.assertEqual(result.content, baseline.content)
            self.assertEqual(self.client.get('/api/desktop/v1/session',
                headers={'Authorization': 'Bearer invalid'}).status_code, 404)

    def test_non_bearer_preserves_web_session_and_csrf(self):
        from app.services.ai.access import AIError
        self.browser.get('/fixture/login')
        for header in ('Basic dXNlcjpwYXNz', 'bearer invalid', 'Bearer'):
            with self.subTest(header=header):
                with patch('app.desktop_auth_middleware.authenticate') as authenticate:
                    result = self.browser.get('/fixture/principal', headers={'Authorization': header})
                    self.assertEqual(result.json()['user'], str(self.user_id))
                    with self.assertRaises(AIError) as rejected:
                        self.browser.post('/fixture/mutate', headers={'Authorization': header})
                    self.assertEqual(rejected.exception.code, 'CSRF_FAILED')
                    authenticate.assert_not_called()

    def test_duplicate_bearer_is_rejected(self):
        self.assertEqual(self.client.get('/api/auth/csrf', headers=[
            ('Authorization', 'Bearer invalid'), ('Authorization', 'Bearer invalid')]).status_code, 401)

    def test_issue_rate_limit(self):
        for _ in range(10):
            self.create()
        result = self.client.post(
            "/api/desktop/v1/auth/transactions", json={"challenge": "a" * 64, "deviceLabel": "x", "redirectPort": 49152}
        )
        self.assertEqual(result.status_code, 429)

    def test_web_oauth_state_is_bound_to_desktop_flow_without_external_calls(self):
        from unittest.mock import AsyncMock
        from types import SimpleNamespace

        flow = self.create()
        response = self.browser.get(flow["browserUrl"].removeprefix("https://desktop.test"))
        self.assertEqual(response.status_code, 303)
        response = self.browser.get(response.headers["location"])
        from urllib.parse import urlsplit, parse_qs

        state = parse_qs(urlsplit(response.headers["location"]).query)["state"][0]
        self.assertEqual(
            self.browser.get("/api/auth/callback", params={"code": "fixture", "state": "wrong"}).status_code, 400
        )
        # A failed state consumes the original state. Restart the browser login explicitly.
        response = self.browser.get("/api/auth/github")
        state = parse_qs(urlsplit(response.headers["location"]).query)["state"][0]
        with (
            patch("app.api.auth.exchange_code_for_token", AsyncMock(return_value="fixture-github-token")),
            patch("app.api.auth.fetch_github_user", AsyncMock(return_value={})),
            patch("app.api.auth.upsert_user", AsyncMock(return_value=SimpleNamespace(id=self.user_id))),
        ):
            result = self.browser.get("/api/auth/callback", params={"code": "fixture", "state": state})
        self.assertEqual(result.status_code, 302)
        self.assertEqual(result.headers["location"], "/api/desktop/v1/auth/authorize?flow=" + flow["flowId"])
        # The GitHub round trip dropped the port from the URL; the signed session restores it.
        back = self.browser.get(result.headers["location"])
        self.assertEqual(back.status_code, 303)
        self.assertTrue(back.headers["location"].startswith("http://127.0.0.1:49152/callback?flow=" + flow["flowId"]))
        # Without the loopback code the app is still pending.
        self.assertEqual(self.exchange(flow).status_code, 202)

    @unittest.skipUnless(POSTGRES, "PostgreSQL row locking is required")
    def test_concurrent_exchange_issues_once_and_replays_same_response(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from uuid import UUID

        flow = self.create()
        code = self.approve(flow)
        barrier = Barrier(2)
        key = str(uuid4())

        def exchange_in_transaction():
            with self.db_factory.begin() as db:
                barrier.wait(timeout=5)
                return service.exchange(db, UUID(flow["flowId"]), flow["pollSecret"], self.verifier, key, code)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: exchange_in_transaction(), range(2)))
        self.assertEqual(results[0], results[1])
        with self.db_factory() as db:
            self.assertEqual(len(db.scalars(select(DesktopSession)).all()), 1)


if __name__ == "__main__":
    print(
        "Validation backend:", "isolated PostgreSQL" if POSTGRES else "SQLite fixture (PG locks/migration not tested)"
    )
    unittest.main(verbosity=2)
