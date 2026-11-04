"""S5 입력 준비 테스트 공용 helper: DB 정리, git fixture 기반 리비전, prepare handler 실행.

GITVIZ_TEST_DATABASE_URL(gitviz_test…)이 필요하다. 이 모듈을 import하기 전에 test 파일이
support.require_test_database()를 호출해야 한다.
"""
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import requests
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

import ai_test_support as support
from ai_git_fixture import Fixture

URL = os.environ['GITVIZ_TEST_DATABASE_URL']
T0 = datetime(2036, 10, 7, 12, 0, 0, tzinfo=timezone.utc)   # 실제 시각보다 항상 미래: DB now() 기본값과 섞여도 시험이 시간에 따라 깨지지 않게 한다
engine = create_engine(URL, pool_size=20, max_overflow=0)
Session = sessionmaker(engine, expire_on_commit=False)
TRUNCATE = ('usage_reservations, usage_budgets, audit_events, request_idempotency, ai_attempts, '
            'finding_feedback, ai_findings, ai_results, ai_input_files, ai_jobs, ai_inputs, pr_revisions')
PR_PATHS = ['main.py', 'src/b.py', 'src/new_mod.py', 'src/old_mod.py']
SECRET_MARKERS = ['line two', 'bracket = 1', 'API_KEY', 'abcdefghijklmnop12345', 'def hello']


def migrate():
    command.upgrade(Config(str(support.ROOT / 'alembic.ini')), 'head')


def ai_env(**over):
    base = {'AI_ALLOWED_ORIGINS': 'http://localhost:8000', 'SESSION_SECRET_KEY': 'input-test-secret',
            'AI_MAX_FILES': '50', 'AI_MAX_INPUT_BYTES': '2000000'}
    base.update({k: str(v) for k, v in over.items()})
    return patch.dict(os.environ, base)


class InputCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix='gitviz-ai-input-')
        cls.fx = Fixture(cls.tmp)
        cls._env = ai_env()
        cls._env.start()

    @classmethod
    def tearDownClass(cls):
        cls._env.stop()
        from app.collectors.git import ai_diff_collector
        ai_diff_collector._remove_tree(cls.tmp)

    def setUp(self):
        self.db = Session()
        self.users, self.repos = [], []
        self.clock = T0                  # input_store가 '지금'으로 쓰는 시각(테스트가 고정한다)
        self._clock = patch('app.services.ai.input_store._now', side_effect=lambda now=None: now or self.clock)
        self._clock.start()
        # API가 접수한 작업은 API 프로세스 스레드에서 바로 실행된다(AD-33). 테스트는 실행 시점을 직접 정하므로
        # 접수(submit)만 기록하고, 실행은 run_prepare·run_generation으로 한다. 기록에는 실행에 넘긴 key가 있다.
        self.submitted = []
        self._submit = patch('app.services.ai.job_runner.submit',
                             side_effect=lambda job_id, api_key=None, **kw: self.submitted.append((job_id, api_key)))
        self._submit.start()
        # 코드 원문은 저장하지 않고 필요할 때 고정 SHA에서 다시 가져온다(AD-33): 모든 시험에서 local git fixture를 원격으로 쓴다.
        self._code = self.with_code()

    def tearDown(self):
        self._code.close()
        self._submit.stop()
        self._clock.stop()
        self.db.rollback()
        self.db.close()
        with Session() as db:
            db.execute(text(f'TRUNCATE {TRUNCATE} CASCADE'))
            for uid in self.users:
                db.execute(text('DELETE FROM users WHERE id = :i'), {'i': uid})
            for rid in self.repos:
                db.execute(text('DELETE FROM repositories WHERE id = :i'), {'i': rid})
            db.commit()

    # ── 데이터 ──
    def make_user(self):
        uid = uuid4()
        self.db.execute(text("INSERT INTO users(id, github_id, github_login) VALUES(:i, :g, 'u')"),
                        {'i': uid, 'g': uuid4().int % 10**12})
        self.db.commit()
        self.users.append(uid)
        return uid

    def make_repo(self, private=False):
        rid = self.db.execute(text("""INSERT INTO repositories(url, name, github_id, is_private)
                                      VALUES('https://github.com/acme/widgets', 'widgets', :g, :p) RETURNING id"""),
                              {'g': uuid4().int % 10**12, 'p': private}).scalar()
        self.db.commit()
        self.repos.append(rid)
        return rid

    def captured(self, repo_id, *, title='Add feature', body='Adds the feature', changed_files=None, pr_number=7):
        from app.collectors.github.ai_pr_collector import CapturedRevision
        from app.schemas.ai import canonical_hash
        return CapturedRevision(
            repository_id=repo_id, pr_number=pr_number, github_pr_id=9000 + pr_number, base_sha=self.fx.m1,
            head_sha=self.fx.f1, merge_base_sha=self.fx.c0, state_at_capture='open',
            metadata={'title': title, 'body': body, 'body_truncated': False, 'changed_files': changed_files},
            revision_fingerprint=canonical_hash({'repo': repo_id, 'pr': pr_number, 'head': self.fx.f1,
                                                 'base': self.fx.m1, 'mb': self.fx.c0}))

    def submit(self, owner, repo, paths=None, *, key=None, now=T0, **meta):
        from app.services.ai import idempotency, input_store
        paths = list(paths if paths is not None else PR_PATHS)
        return input_store.create_input_and_job(
            self.db, owner_id=owner, repository_id=repo, captured=self.captured(repo, **meta), selected_paths=paths,
            route='POST /api/ai/v1/inputs', idempotency_key=key or uuid4().hex,
            body_hash=idempotency.request_hash({'paths': sorted(paths), 'meta': meta, 'k': key or str(uuid4())}), now=now)

    def pinned(self, owner, repo, input_id, **kw):
        import ai_credential_support
        return ai_credential_support.pinned_for(self.db, owner, repo, input_id, **kw)

    def code_patches(self, token=None):
        """고정 코드를 local git fixture에서 가져오게 한다(준비·생성·파일 보기 공통). GitHub·HTTP는 쓰지 않는다."""
        from app.services.ai import input_prepare
        return [
            patch('app.services.ai.input_prepare.user_github_token', return_value=token),
            patch.object(input_prepare, 'PROTOCOLS', 'file'),
            patch('app.services.ai.input_builder.remote_url_for', return_value=self.fx.path.as_uri()),
            patch('requests.sessions.Session.request', side_effect=AssertionError('네트워크 호출 금지')),
        ]

    def with_code(self, token=None):
        """code_patches를 한꺼번에 켜는 context manager."""
        from contextlib import ExitStack
        stack = ExitStack()
        for p in self.code_patches(token):
            stack.enter_context(p)
        return stack

    def run_prepare(self, job_id, *, authorize=None, verify=None, token=None):
        """prepare handler를 실제 DB·실제 git(local file remote)로 실행한다. GitHub API와 HTTP는 쓰지 않는다."""
        from app.services.ai import input_prepare, job_runner
        job_runner.register_handler('prepare', input_prepare.prepare_handler)
        patches = [
            patch('app.services.ai.input_prepare.authorize_ai', side_effect=authorize or (lambda *a, **k: ('repo', {}))),
            patch('app.services.ai.input_prepare.verify_unchanged', side_effect=verify),
            *self.code_patches(token),
        ]
        for p in patches:
            p.start()
        try:
            return job_runner.run_job(job_id, session_factory=Session)
        finally:
            for p in reversed(patches):
                p.stop()

    def input_row(self, input_id):
        self.db.rollback()
        return self.db.execute(text('SELECT * FROM ai_inputs WHERE id = :i'), {'i': input_id}).mappings().one()

    def job_row(self, job_id):
        self.db.rollback()
        return self.db.execute(text('SELECT * FROM ai_jobs WHERE id = :i'), {'i': job_id}).mappings().one()

    def scalar(self, sql, **params):
        self.db.rollback()
        return self.db.execute(text(sql), params).scalar()

    def prepared(self, owner=None, repo=None, paths=None, **kw):
        """준비를 끝낸 입력을 만든다. (owner, repo, input_id, job_id)"""
        owner = owner or self.make_user()
        repo = repo or self.make_repo()
        self.clock = kw.get('now', T0)
        result = self.submit(owner, repo, paths, **kw)
        outcome = self.run_prepare(result.job.id)
        assert outcome == 'succeeded', outcome
        return owner, repo, result.job.input_id, result.job.id
