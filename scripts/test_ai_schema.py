"""S2 AI 스키마 테스트. GITVIZ_TEST_DATABASE_URL(gitviz_test로 시작하는 PostgreSQL)이 필요하다.

테스트마다 임시 schema를 만들어 그 안에서 migration을 실행하므로 다른 schema의 데이터는 건드리지 않는다.
"""
import json
import os
import sys
import unittest
from uuid import uuid4

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ai_test_support as support  # noqa: E402

URL = support.require_test_database()   # app import 전에 호출
sys.path.insert(0, str(support.ROOT))

from alembic import command  # noqa: E402
from alembic.autogenerate import compare_metadata  # noqa: E402
from alembic.config import Config  # noqa: E402
from alembic.migration import MigrationContext  # noqa: E402
from alembic.script import ScriptDirectory  # noqa: E402
from sqlalchemy import create_engine, inspect, text  # noqa: E402
from sqlalchemy.exc import DBAPIError  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core.database import Base  # noqa: E402
import app.models  # noqa: E402,F401
from app.models import AIInput, AIJob, PRRevision  # noqa: E402

HEAD = 'a7c1e5b2d903'
PREVIOUS = 'a1b2c3d4e5f6'
AI_TABLES = ['pr_revisions', 'ai_inputs', 'ai_input_files', 'ai_jobs', 'ai_attempts', 'ai_results', 'ai_findings',
             'finding_feedback', 'request_idempotency', 'task_outbox', 'usage_budgets', 'usage_reservations',
             'audit_events']
H = lambda c: c * 64  # noqa: E731
SHA = 'a' * 40
CONFIG = Config(str(support.ROOT / 'alembic.ini'))
engine = create_engine(URL)


class SchemaCase(unittest.TestCase):
    """클래스마다 임시 schema를 만들고 setup_schema()에서 준비한다."""

    @classmethod
    def setUpClass(cls):
        cls.schema = 'gitviz_s2_' + uuid4().hex[:12]
        cls.conn = engine.connect()
        cls.conn.execute(text(f'CREATE SCHEMA {cls.schema}'))
        cls.conn.execute(text(f'SET search_path TO {cls.schema}'))
        cls.conn.commit()
        CONFIG.attributes['connection'] = cls.conn
        cls.setup_schema()
        cls.conn.commit()

    @classmethod
    def tearDownClass(cls):
        CONFIG.attributes.pop('connection', None)
        cls.conn.rollback()
        cls.conn.execute(text('RESET search_path'))
        cls.conn.execute(text(f'DROP SCHEMA {cls.schema} CASCADE'))
        cls.conn.commit()
        cls.conn.close()

    @classmethod
    def setup_schema(cls):
        command.upgrade(CONFIG, 'head')

    def setUp(self):
        self.tx = self.conn.begin()

    def tearDown(self):
        self.tx.rollback()

    # ── helpers ──
    def insert(self, table, **cols):
        names, values, params = [], [], {}
        for i, (key, value) in enumerate(cols.items()):
            names.append(key)
            if isinstance(value, (dict, list)):
                values.append(f'CAST(:p{i} AS jsonb)')
                params[f'p{i}'] = json.dumps(value)
            else:
                values.append(f':p{i}')
                params[f'p{i}'] = value
        sql = f'INSERT INTO {table} ({",".join(names)}) VALUES ({",".join(values)}) RETURNING id'
        return self.conn.execute(text(sql), params).scalar()

    def execute(self, sql, **params):
        return self.conn.execute(text(sql), params)

    def rejected(self, fn, *args, **kw):
        """제약·trigger 위반(DBAPIError)이 나야 한다. 실패한 문장은 savepoint로 격리한다."""
        with self.assertRaises(DBAPIError) as ctx:
            with self.conn.begin_nested():
                fn(*args, **kw)
        return str(ctx.exception.orig)

    def user(self, n=None):
        return self.insert('users', id=str(uuid4()), github_id=n or uuid4().int % 10**12,
                           github_login='u' + uuid4().hex[:6])

    def repo(self):
        rid = self.execute("INSERT INTO repositories(url,name) VALUES(:u,'r') RETURNING id",
                           u='https://github.com/t/' + uuid4().hex[:8]).scalar()
        return rid

    def revision(self, owner, repo, fp=None, **kw):
        cols = dict(owner_user_id=owner, repository_id=repo, pr_number=1, github_pr_id=100, base_sha=SHA,
                    head_sha='b' * 40, merge_base_sha='c' * 40, revision_fingerprint=fp or uuid4().hex * 2,
                    state_at_capture='open', metadata={})
        cols.update(kw)
        return self.insert('pr_revisions', **cols)

    def input(self, owner, repo, rev, status='building', **kw):
        cols = dict(owner_user_id=owner, repository_id=repo, revision_id=rev, status=status, coverage={},
                    expires_at='2099-01-01T00:00:00Z', policy_version='code-transfer-v1')
        if status == 'ready':
            cols.update(manifest={'files': []}, manifest_hash=H('a'), input_hash=H('b'),
                        finalized_at='2026-10-07T00:00:00Z', valid_until='2099-01-01T00:00:00Z')
        cols.update(kw)
        return self.insert('ai_inputs', **cols)

    def file(self, input_id, path='a.py', **kw):
        cols = dict(input_id=input_id, path=path, side_availability='BOTH', content_state='read',
                    content_hash=H('c'), encrypted_payload=b'cipher', key_version='v1', line_map={}, byte_count=6)
        cols.update(kw)
        return self.insert('ai_input_files', **cols)

    def job(self, owner, repo, input_id, kind='review', fingerprint=None, **kw):
        cols = dict(owner_user_id=owner, repository_id=repo, input_id=input_id, kind=kind,
                    fingerprint=fingerprint or uuid4().hex * 2)
        if kind != 'prepare':
            cols.update(provider_profile='p1', prompt_version='v1', schema_version='v1')
        cols.update(kw)
        return self.insert('ai_jobs', **cols)

    def chain(self):
        """user/repo → revision → ready input → file → job. 같은 owner/repo."""
        owner, repo = self.user(), self.repo()
        rev = self.revision(owner, repo)
        inp = self.input(owner, repo, rev)
        file_id = self.file(inp)
        self.execute("UPDATE ai_inputs SET status='ready',manifest='{}',manifest_hash=:h,input_hash=:h,"
                     "finalized_at=now(),valid_until=now()+interval '1 day' WHERE id=:i", h=H('a'), i=inp)
        job = self.job(owner, repo, inp)
        return dict(owner=owner, repo=repo, rev=rev, inp=inp, file=file_id, job=job)


class Migration(SchemaCase):
    def test_single_head(self):
        self.assertEqual(ScriptDirectory.from_config(CONFIG).get_heads(), [HEAD])
        self.assertEqual(self.execute('SELECT version_num FROM alembic_version').scalar(), HEAD)

    def test_all_tables_created_and_users_fk_is_real(self):
        tables = set(inspect(self.conn).get_table_names(schema=self.schema))
        self.assertTrue(set(AI_TABLES) <= tables)
        fk_targets = {(t, fk['referred_table']) for t in AI_TABLES
                      for fk in inspect(self.conn).get_foreign_keys(t, schema=self.schema)}
        self.assertIn(('pr_revisions', 'users'), fk_targets)
        self.assertIn(('finding_feedback', 'users'), fk_targets)
        self.assertIn(('request_idempotency', 'users'), fk_targets)

    def test_models_match_database(self):
        insp = inspect(self.conn)
        for name in AI_TABLES:
            table = Base.metadata.tables[name]
            actual = {c['name']: c for c in insp.get_columns(name, schema=self.schema)}
            self.assertEqual(set(table.columns.keys()), set(actual), name)
            for col in table.columns:
                self.assertEqual(col.nullable, actual[col.name]['nullable'], f'{name}.{col.name}')
            db_unique = {u['name'] for u in insp.get_unique_constraints(name, schema=self.schema)}
            self.assertEqual({c.name for c in table.constraints if c.__class__.__name__ == 'UniqueConstraint'},
                             db_unique, name)
            db_checks = {c['name'] for c in insp.get_check_constraints(name, schema=self.schema)}
            self.assertEqual({c.name for c in table.constraints if c.__class__.__name__ == 'CheckConstraint'},
                             db_checks, name)
            db_fks = {f['name'] for f in insp.get_foreign_keys(name, schema=self.schema)}
            model_fks = {c.name for c in table.constraints if c.__class__.__name__ == 'ForeignKeyConstraint' and c.name}
            self.assertTrue(model_fks <= db_fks, name)  # 이름이 없는 단일 컬럼 FK는 DB 이름과 비교하지 않는다
            self.assertEqual(len(table.foreign_key_constraints), len(db_fks), name)
            db_indexes = {i['name'] for i in insp.get_indexes(name, schema=self.schema)
                          if not i.get('duplicates_constraint')}
            self.assertEqual({i.name for i in table.indexes}, db_indexes, name)

    def test_alembic_autogenerate_sees_no_ai_drift_and_ignores_users(self):
        ctx = MigrationContext.configure(self.conn, opts={'compare_type': False})

        def include(obj, name, type_, reflected, compare_to):
            return not (type_ == 'table' and name == 'users')
        ctx = MigrationContext.configure(self.conn, opts={'include_object': include})
        diffs = repr(compare_metadata(ctx, Base.metadata))
        for name in AI_TABLES:
            self.assertNotIn(f"'{name}'", diffs, f'{name} drift: {diffs[:300]}')
        self.assertNotIn("'users'", diffs)

    def test_orm_roundtrip(self):
        owner, repo = self.user(), self.repo()
        session = Session(bind=self.conn, join_transaction_mode='create_savepoint')
        rev = PRRevision(owner_user_id=owner, repository_id=repo, pr_number=3, github_pr_id=9, base_sha=SHA,
                         head_sha='b' * 40, revision_fingerprint=H('d'), state_at_capture='open',
                         pr_metadata={'title': 't'})
        session.add(rev)
        session.flush()
        inp = AIInput(owner_user_id=owner, repository_id=repo, revision_id=rev.id, coverage={},
                      expires_at='2099-01-01T00:00:00Z', policy_version='code-transfer-v1')
        session.add(inp)
        session.flush()
        session.add(AIJob(owner_user_id=owner, repository_id=repo, input_id=inp.id, kind='prepare',
                          fingerprint=H('e')))
        session.flush()
        self.assertEqual(session.get(PRRevision, rev.id).pr_metadata, {'title': 't'})
        self.assertEqual(session.get(AIInput, inp.id).status, 'building')
        session.close()


class Constraints(SchemaCase):
    def test_composite_fk_blocks_cross_owner_and_cross_repo(self):
        a = self.chain()
        other_user, other_repo = self.user(), self.repo()
        # revision은 a의 것인데 owner나 repo를 다르게 지정
        self.rejected(self.input, other_user, a['repo'], a['rev'])
        self.rejected(self.input, a['owner'], other_repo, a['rev'])
        # input은 a의 것인데 job의 owner/repo 불일치
        self.rejected(self.job, other_user, a['repo'], a['inp'])
        self.rejected(self.job, a['owner'], other_repo, a['inp'])
        # result는 job과 owner/repo가 일치해야 한다
        payload = dict(job_id=a['job'], payload={}, payload_hash=H('a'), coverage={}, structure_validation='passed',
                       expires_at='2099-01-01T00:00:00Z')
        self.rejected(self.insert, 'ai_results', owner_user_id=other_user, repository_id=a['repo'], **payload)
        self.rejected(self.insert, 'ai_results', owner_user_id=a['owner'], repository_id=other_repo, **payload)
        self.insert('ai_results', owner_user_id=a['owner'], repository_id=a['repo'], **payload)

    def test_nonexistent_user_or_repo_rejected(self):
        self.rejected(self.revision, str(uuid4()), self.repo())
        self.rejected(self.revision, self.user(), 999999999)
        self.rejected(self.insert, 'finding_feedback', finding_id=str(uuid4()), author_user_id=self.user(),
                      verdict='valid')

    def test_unique_constraints(self):
        a = self.chain()
        fp = H('1')
        self.revision(a['owner'], a['repo'], fp=fp)
        self.rejected(self.revision, a['owner'], a['repo'], fp=fp)
        self.revision(self.user(), a['repo'], fp=fp)     # 다른 owner는 같은 fingerprint 가능

        j = self.job(a['owner'], a['repo'], a['inp'], fingerprint=fp)
        self.rejected(self.job, a['owner'], a['repo'], a['inp'], fingerprint=fp)
        self.job(a['owner'], a['repo'], a['inp'], fingerprint=fp, generation=1)  # 새 generation
        self.job(a['owner'], a['repo'], a['inp'], kind='summary', fingerprint=fp)

        self.insert('ai_attempts', job_id=j, attempt_no=1, lease_token=str(uuid4()))
        self.rejected(self.insert, 'ai_attempts', job_id=j, attempt_no=1, lease_token=str(uuid4()))

        self.insert('task_outbox', job_id=j, event_type='generate')
        self.rejected(self.insert, 'task_outbox', job_id=j, event_type='generate')
        self.insert('task_outbox', job_id=j, event_type='generate', dispatch_generation=1)

        self.insert('request_idempotency', owner_user_id=a['owner'], route='POST /jobs', key='k1',
                    request_hash=H('a'), expires_at='2099-01-01T00:00:00Z')
        self.rejected(self.insert, 'request_idempotency', owner_user_id=a['owner'], route='POST /jobs', key='k1',
                      request_hash=H('b'), expires_at='2099-01-01T00:00:00Z')

        self.insert('usage_budgets', owner_user_id=None, period_start='2026-10-01', currency='USD', amount_limit=1)
        self.rejected(self.insert, 'usage_budgets', owner_user_id=None, period_start='2026-10-01',
                      currency='USD', amount_limit=2)   # 서버 예산도 중복 불가(NULL owner)
        self.insert('usage_budgets', owner_user_id=a['owner'], period_start='2026-10-01', currency='USD',
                    amount_limit=1)
        self.rejected(self.insert, 'usage_budgets', owner_user_id=a['owner'], period_start='2026-10-01',
                      currency='USD', amount_limit=1)

        self.insert('ai_input_files', input_id=self.input(a['owner'], a['repo'], a['rev']), path='ok.py',
                    side_availability='BOTH', content_state='excluded', reason='binary')

    def test_input_file_path_unique_per_input(self):
        a = self.chain()
        building = self.input(a['owner'], a['repo'], a['rev'])
        self.file(building, 'x.py')
        self.rejected(self.file, building, 'x.py')

    def test_result_and_finding_and_feedback_uniques(self):
        a = self.chain()
        result = self.insert('ai_results', job_id=a['job'], owner_user_id=a['owner'], repository_id=a['repo'],
                             payload={}, payload_hash=H('a'), coverage={}, structure_validation='passed',
                             expires_at='2099-01-01T00:00:00Z')
        self.rejected(self.insert, 'ai_results', job_id=a['job'], owner_user_id=a['owner'],
                      repository_id=a['repo'], payload={}, payload_hash=H('a'), coverage={},
                      structure_validation='passed', expires_at='2099-01-01T00:00:00Z')
        finding = self.finding(result, a['file'])
        self.rejected(self.finding, result, a['file'])   # 같은 ordinal
        self.insert('finding_feedback', finding_id=finding, author_user_id=a['owner'], verdict='valid')
        self.rejected(self.insert, 'finding_feedback', finding_id=finding, author_user_id=a['owner'],
                      verdict='uncertain')

    def finding(self, result, file_id, ordinal=1, **kw):
        cols = dict(result_id=result, ordinal=ordinal, fingerprint=H('f'), category='correctness', priority=1,
                    title='t', detail={}, source_file_id=file_id, side='RIGHT', start_line=1, end_line=2)
        cols.update(kw)
        return self.insert('ai_findings', **cols)

    def test_check_constraints(self):
        a = self.chain()
        result = self.insert('ai_results', job_id=a['job'], owner_user_id=a['owner'], repository_id=a['repo'],
                             payload={}, payload_hash=H('a'), coverage={}, structure_validation='passed',
                             expires_at='2099-01-01T00:00:00Z')
        j = a['job']
        bad = {
            'pr_number 0': lambda: self.revision(a['owner'], a['repo'], pr_number=0),
            'bad sha': lambda: self.revision(a['owner'], a['repo'], head_sha='XYZ'),
            'bad fingerprint': lambda: self.revision(a['owner'], a['repo'], fp='short'),
            'bad state': lambda: self.revision(a['owner'], a['repo'], state_at_capture='draft'),
            'metadata array': lambda: self.revision(a['owner'], a['repo'], metadata=[]),
            'input status': lambda: self.input(a['owner'], a['repo'], a['rev'], status='done'),
            'ready without hash': lambda: self.input(a['owner'], a['repo'], a['rev'], status='ready', input_hash=None),
            'file bytes<0': lambda: self.file(self.input(a['owner'], a['repo'], a['rev']), 'n.py', byte_count=-1),
            'file side': lambda: self.file(self.input(a['owner'], a['repo'], a['rev']), 'n.py', side_availability='X'),
            'read with reason': lambda: self.file(self.input(a['owner'], a['repo'], a['rev']), 'n.py', reason='binary'),
            'excluded without reason': lambda: self.file(self.input(a['owner'], a['repo'], a['rev']), 'n.py',
                                                         content_state='excluded', content_hash=None),
            'payload without key': lambda: self.file(self.input(a['owner'], a['repo'], a['rev']), 'n.py',
                                                     key_version=None),
            'job kind': lambda: self.job(a['owner'], a['repo'], a['inp'], kind='report', fingerprint=H('2')),
            'job status': lambda: self.job(a['owner'], a['repo'], a['inp'], fingerprint=H('3'), status='paused'),
            'generation<0': lambda: self.job(a['owner'], a['repo'], a['inp'], fingerprint=H('4'), generation=-1),
            'review without profile': lambda: self.job(a['owner'], a['repo'], a['inp'], fingerprint=H('5'),
                                                       provider_profile=None),
            'running without lease': lambda: self.job(a['owner'], a['repo'], a['inp'], fingerprint=H('6'),
                                                      status='running'),
            'failed without code': lambda: self.job(a['owner'], a['repo'], a['inp'], fingerprint=H('7'),
                                                    status='failed', finished_at='2026-10-07T00:00:00Z'),
            'terminal without finished_at': lambda: self.job(a['owner'], a['repo'], a['inp'], fingerprint=H('8'),
                                                             status='cancelled'),
            'attempt no 0': lambda: self.insert('ai_attempts', job_id=j, attempt_no=0, lease_token=str(uuid4())),
            'negative tokens': lambda: self.insert('ai_attempts', job_id=j, attempt_no=1, lease_token=str(uuid4()),
                                                   input_tokens=-1),
            'negative provider cost': lambda: self.insert('ai_attempts', job_id=j, attempt_no=1,
                                                          lease_token=str(uuid4()), provider_reported_cost=-0.01),
            'negative estimated cost': lambda: self.insert('ai_attempts', job_id=j, attempt_no=1,
                                                           lease_token=str(uuid4()), estimated_cost=-1),
            'uncertain flag mismatch': lambda: self.insert('ai_attempts', job_id=j, attempt_no=1,
                                                           lease_token=str(uuid4()), status='uncertain'),
            'result structure': lambda: self.insert('ai_results', job_id=self.job(a['owner'], a['repo'], a['inp'],
                                                    fingerprint=H('9')), owner_user_id=a['owner'],
                                                    repository_id=a['repo'], payload={}, payload_hash=H('a'),
                                                    coverage={}, structure_validation='ok',
                                                    expires_at='2099-01-01T00:00:00Z'),
            'finding start 0': lambda: self.finding(result, a['file'], 2, start_line=0),
            'finding reversed': lambda: self.finding(result, a['file'], 3, start_line=5, end_line=2),
            'finding negative': lambda: self.finding(result, a['file'], 4, start_line=-2, end_line=1),
            'finding side': lambda: self.finding(result, a['file'], 5, side='BOTH'),
            'finding ordinal 0': lambda: self.finding(result, a['file'], 0),
            'verdict': lambda: self.insert('finding_feedback', finding_id=self.finding(result, a['file'], 6),
                                           author_user_id=a['owner'], verdict='maybe'),
            'version 0': lambda: self.insert('finding_feedback', finding_id=self.finding(result, a['file'], 7),
                                             author_user_id=a['owner'], verdict='valid', version=0),
            'idempotency status': lambda: self.insert('request_idempotency', owner_user_id=a['owner'], route='r',
                                                      key='k', request_hash=H('a'), status='x',
                                                      expires_at='2099-01-01T00:00:00Z'),
            'idempotency empty key': lambda: self.insert('request_idempotency', owner_user_id=a['owner'], route='r',
                                                         key='', request_hash=H('a'),
                                                         expires_at='2099-01-01T00:00:00Z'),
            'outbox retry<0': lambda: self.insert('task_outbox', job_id=j, event_type='e', retry_count=-1),
            'budget negative limit': lambda: self.insert('usage_budgets', owner_user_id=a['owner'],
                                                         period_start='2026-10-01', currency='USD', amount_limit=-1),
            'budget negative reserved': lambda: self.insert('usage_budgets', owner_user_id=a['owner'],
                                                            period_start='2026-10-01', currency='USD',
                                                            amount_limit=1, reserved_amount=-1),
            'budget negative spent': lambda: self.insert('usage_budgets', owner_user_id=a['owner'],
                                                         period_start='2026-10-01', currency='USD',
                                                         amount_limit=1, spent_amount=-1),
            'budget currency': lambda: self.insert('usage_budgets', owner_user_id=a['owner'],
                                                   period_start='2026-10-01', currency='usd', amount_limit=1),
            'audit secret key': lambda: self.insert('audit_events', action='a', object_type='job', request_id='r',
                                                    details={'token': 'x'}),
            'audit non-object': lambda: self.insert('audit_events', action='a', object_type='job', request_id='r',
                                                    details=[]),
        }
        budget = self.insert('usage_budgets', owner_user_id=a['owner'], period_start='2026-09-01', currency='USD',
                             amount_limit=5)
        bad.update({
            'reservation negative': lambda: self.insert('usage_reservations', job_id=j, attempt_no=1, budget_id=budget,
                                                        reserved_amount=-1),
            'reservation settled without amount': lambda: self.insert('usage_reservations', job_id=j, attempt_no=1,
                                                                      budget_id=budget, reserved_amount=1,
                                                                      status='settled'),
            'reservation status': lambda: self.insert('usage_reservations', job_id=j, attempt_no=1, budget_id=budget,
                                                      reserved_amount=1, status='lost'),
        })
        for label, fn in bad.items():
            with self.subTest(label):
                self.rejected(fn)

    def test_valid_rows_accepted(self):
        a = self.chain()
        self.job(a['owner'], a['repo'], a['inp'], kind='prepare', fingerprint=H('1'))
        self.job(a['owner'], a['repo'], a['inp'], fingerprint=H('2'), status='running',
                 lease_token=str(uuid4()), lease_until='2099-01-01T00:00:00Z')
        self.job(a['owner'], a['repo'], a['inp'], fingerprint=H('3'), status='failed', error_code='X',
                 finished_at='2026-10-07T00:00:00Z')
        self.insert('ai_attempts', job_id=a['job'], attempt_no=1, lease_token=str(uuid4()), status='uncertain',
                    outcome_uncertain=True, estimated_cost=0.5)
        self.insert('audit_events', action='job.create', object_type='job', request_id='r', details={'kind': 'review'})


class Immutability(SchemaCase):
    def test_revision_immutable(self):
        a = self.chain()
        msg = self.rejected(self.execute, "UPDATE pr_revisions SET head_sha=:s WHERE id=:i", s='d' * 40, i=a['rev'])
        self.assertIn('immutable', msg)

    def test_input_lifecycle(self):
        owner, repo = self.user(), self.repo()
        rev = self.revision(owner, repo)
        inp = self.input(owner, repo, rev)
        # building: 내용 수정과 확정이 가능
        self.execute("UPDATE ai_inputs SET coverage=CAST(:c AS jsonb) WHERE id=:i", c='{"x":1}', i=inp)
        self.rejected(self.execute, "UPDATE ai_inputs SET status='expired' WHERE id=:i", i=inp)   # building→expired 불가
        self.rejected(self.execute, "UPDATE ai_inputs SET owner_user_id=:o WHERE id=:i", o=self.user(), i=inp)
        self.rejected(self.execute, "UPDATE ai_inputs SET status='ready' WHERE id=:i", i=inp)       # 해시 없이 확정 불가
        self.execute("UPDATE ai_inputs SET status='ready',manifest='{}',manifest_hash=:h,input_hash=:h,"
                     "finalized_at=now(),valid_until=now() WHERE id=:i", h=H('a'), i=inp)
        # ready: 내용·hash·정책 변경 불가, 만료 전환만 가능
        for sql in ("UPDATE ai_inputs SET manifest_hash=:h WHERE id=:i", "UPDATE ai_inputs SET input_hash=:h WHERE id=:i",
                    "UPDATE ai_inputs SET coverage='{}' WHERE id=:i", "UPDATE ai_inputs SET policy_version='x' WHERE id=:i",
                    "UPDATE ai_inputs SET status='building' WHERE id=:i", "UPDATE ai_inputs SET status='failed' WHERE id=:i"):
            with self.subTest(sql):
                self.rejected(self.execute, sql, h=H('f'), i=inp)
        self.execute("UPDATE ai_inputs SET status='expired' WHERE id=:i", i=inp)
        self.rejected(self.execute, "UPDATE ai_inputs SET status='ready' WHERE id=:i", i=inp)       # 종료 상태

    def test_failed_input_is_final(self):
        owner, repo = self.user(), self.repo()
        inp = self.input(owner, repo, self.revision(owner, repo))
        self.execute("UPDATE ai_inputs SET status='failed' WHERE id=:i", i=inp)
        self.rejected(self.execute, "UPDATE ai_inputs SET status='building' WHERE id=:i", i=inp)

    def test_files_locked_after_finalize_but_raw_text_can_be_purged(self):
        a = self.chain()    # 입력은 ready
        self.rejected(self.file, a['inp'], 'late.py')      # 확정 후 코드 추가 불가
        self.rejected(self.execute, "UPDATE ai_input_files SET path='z.py' WHERE id=:i", i=a['file'])
        self.rejected(self.execute, "UPDATE ai_input_files SET content_hash=:h WHERE id=:i", h=H('9'), i=a['file'])
        self.rejected(self.execute, "UPDATE ai_input_files SET encrypted_payload=:p WHERE id=:i", p=b'new', i=a['file'])
        self.execute("UPDATE ai_input_files SET encrypted_payload=NULL,key_version=NULL WHERE id=:i", i=a['file'])
        row = self.execute("SELECT encrypted_payload, key_version, content_hash FROM ai_input_files WHERE id=:i",
                           i=a['file']).one()
        self.assertEqual((row[0], row[1], row[2].strip()), (None, None, H('c')))

    def test_result_original_immutable_but_human_validation_updates(self):
        a = self.chain()
        result = self.insert('ai_results', job_id=a['job'], owner_user_id=a['owner'], repository_id=a['repo'],
                             payload={'k': 1}, payload_hash=H('a'), coverage={}, structure_validation='passed',
                             expires_at='2099-01-01T00:00:00Z')
        for sql in ("UPDATE ai_results SET payload='{}' WHERE id=:i", "UPDATE ai_results SET payload_hash=:h WHERE id=:i",
                    "UPDATE ai_results SET coverage=CAST('[1]' AS jsonb) WHERE id=:i",
                    "UPDATE ai_results SET structure_validation='failed' WHERE id=:i"):
            with self.subTest(sql):
                self.rejected(self.execute, sql, h=H('b'), i=result)
        self.execute("UPDATE ai_results SET human_validation='valid' WHERE id=:i", i=result)
        finding = self.insert('ai_findings', result_id=result, ordinal=1, fingerprint=H('f'), category='c', priority=1,
                              title='t', detail={}, source_file_id=a['file'], side='RIGHT', start_line=1, end_line=1)
        self.rejected(self.execute, "UPDATE ai_findings SET title='changed' WHERE id=:i", i=finding)

    def test_terminal_job_is_final_and_identity_fixed(self):
        a = self.chain()
        self.rejected(self.execute, "UPDATE ai_jobs SET kind='summary' WHERE id=:i", i=a['job'])
        self.rejected(self.execute, "UPDATE ai_jobs SET fingerprint=:f WHERE id=:i", f=H('9'), i=a['job'])
        self.execute("UPDATE ai_jobs SET status='succeeded',finished_at=now() WHERE id=:i", i=a['job'])
        for sql in ("UPDATE ai_jobs SET status='queued' WHERE id=:i", "UPDATE ai_jobs SET status='failed',error_code='x' WHERE id=:i",
                    "UPDATE ai_jobs SET attempt_count=5 WHERE id=:i"):
            with self.subTest(sql):
                self.rejected(self.execute, sql, i=a['job'])
        self.execute("UPDATE ai_jobs SET deleted_at=now() WHERE id=:i", i=a['job'])   # 소프트 삭제는 허용


class CascadeAndRetention(SchemaCase):
    def test_deleting_user_removes_ai_data_but_keeps_budget_ledger_rows_detached(self):
        a = self.chain()
        budget = self.insert('usage_budgets', owner_user_id=None, period_start='2026-10-01', currency='USD',
                             amount_limit=10)
        res = self.insert('usage_reservations', job_id=a['job'], attempt_no=1, budget_id=budget, reserved_amount=1)
        self.insert('ai_attempts', job_id=a['job'], attempt_no=1, lease_token=str(uuid4()))
        self.insert('task_outbox', job_id=a['job'], event_type='generate')
        self.insert('audit_events', actor_user_id=a['owner'], repository_id=a['repo'], action='x', object_type='job',
                    request_id='r')
        self.execute('DELETE FROM users WHERE id=:i', i=a['owner'])
        for table in ['pr_revisions', 'ai_inputs', 'ai_input_files', 'ai_jobs', 'ai_attempts', 'task_outbox']:
            self.assertEqual(self.execute(f'SELECT count(*) FROM {table}').scalar(), 0, table)
        # 예약 기록은 남고 job만 분리된다. 감사 기록은 actor/repository만 분리된다.
        self.assertIsNone(self.execute('SELECT job_id FROM usage_reservations WHERE id=:i', i=res).scalar())
        self.assertEqual(self.execute('SELECT actor_user_id FROM audit_events').scalar(), None)

    def test_deleting_input_cascades_files_jobs_results_findings(self):
        a = self.chain()
        result = self.insert('ai_results', job_id=a['job'], owner_user_id=a['owner'], repository_id=a['repo'],
                             payload={}, payload_hash=H('a'), coverage={}, structure_validation='passed',
                             expires_at='2099-01-01T00:00:00Z')
        finding = self.insert('ai_findings', result_id=result, ordinal=1, fingerprint=H('f'), category='c',
                              priority=1, title='t', detail={}, source_file_id=a['file'], side='RIGHT',
                              start_line=1, end_line=1)
        self.insert('finding_feedback', finding_id=finding, author_user_id=a['owner'], verdict='valid')
        self.execute('DELETE FROM ai_inputs WHERE id=:i', i=a['inp'])
        for table in ['ai_input_files', 'ai_jobs', 'ai_results', 'ai_findings', 'finding_feedback']:
            self.assertEqual(self.execute(f'SELECT count(*) FROM {table}').scalar(), 0, table)
        self.assertEqual(self.execute('SELECT count(*) FROM pr_revisions').scalar(), 1)


class UpgradeFromPreviousHead(unittest.TestCase):
    """기존 head(AI 이전)에서 업그레이드해도 기존 사용자·분석 snapshot·저장소가 보존된다."""

    @classmethod
    def setUpClass(cls):
        cls.schema = 'gitviz_s2_up_' + uuid4().hex[:10]
        cls.conn = engine.connect()
        cls.conn.execute(text(f'CREATE SCHEMA {cls.schema}'))
        cls.conn.execute(text(f'SET search_path TO {cls.schema}'))
        cls.conn.commit()
        CONFIG.attributes['connection'] = cls.conn

    @classmethod
    def tearDownClass(cls):
        CONFIG.attributes.pop('connection', None)
        cls.conn.rollback()
        cls.conn.execute(text('RESET search_path'))
        cls.conn.execute(text(f'DROP SCHEMA {cls.schema} CASCADE'))
        cls.conn.commit()
        cls.conn.close()

    def ai_tables(self):
        return set(inspect(self.conn).get_table_names(schema=self.schema)) & set(AI_TABLES)

    def test_upgrade_preserves_data_and_downgrade_removes_only_ai(self):
        command.upgrade(CONFIG, PREVIOUS)
        self.assertEqual(self.ai_tables(), set())
        uid = str(uuid4())
        self.conn.execute(text("INSERT INTO users(id,github_id,github_login) VALUES(:i,424242,'keep-me')"), {'i': uid})
        rid = self.conn.execute(text("INSERT INTO repositories(url,name,github_id,is_private) "
                                     "VALUES('https://github.com/t/keep','keep',777,false) RETURNING id")).scalar()
        self.conn.execute(text(
            "INSERT INTO analysis_snapshots(repository_id,target_sha,analyzed_at,window_end,formula_version,"
            "parameters,payload,payload_sha256) VALUES(:r,:s,now(),now(),'v1','{}',CAST(:p AS jsonb),:h)"),
            {'r': rid, 's': SHA, 'h': H('a'), 'p': '{"a": 1}'})
        self.conn.commit()

        command.upgrade(CONFIG, 'head')
        self.assertEqual(self.ai_tables(), set(AI_TABLES))
        self.assertEqual(self.conn.execute(text('SELECT github_login FROM users')).scalar(), 'keep-me')
        self.assertEqual(self.conn.execute(text('SELECT count(*) FROM analysis_snapshots')).scalar(), 1)
        self.assertEqual(self.conn.execute(text('SELECT github_id FROM repositories')).scalar(), 777)
        # 기존 snapshot의 불변 trigger도 그대로다.
        with self.assertRaises(DBAPIError):
            with self.conn.begin_nested():
                self.conn.execute(text("UPDATE analysis_snapshots SET target_sha=:s"), {'s': 'b' * 40})
        self.conn.commit()

        command.downgrade(CONFIG, PREVIOUS)    # 테스트 schema에서만 수행
        self.assertEqual(self.ai_tables(), set())
        self.assertEqual(self.conn.execute(text('SELECT github_login FROM users')).scalar(), 'keep-me')
        self.assertEqual(self.conn.execute(text('SELECT count(*) FROM analysis_snapshots')).scalar(), 1)
        leftovers = self.conn.execute(text(
            "SELECT proname FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
            "WHERE n.nspname=:s AND proname LIKE 'gitviz_ai_%'"), {'s': self.schema}).all()
        self.assertEqual(leftovers, [])
        command.upgrade(CONFIG, 'head')        # 다시 올릴 수 있다
        self.assertEqual(self.ai_tables(), set(AI_TABLES))
        self.conn.commit()


if __name__ == '__main__':
    unittest.main(verbosity=2)
