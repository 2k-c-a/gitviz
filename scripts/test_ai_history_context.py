"""S11 GitViz 이력 근거 테스트: 분석 snapshot의 저장된 값만 옮기는지, 검증하지 못한 snapshot을 근거에서 제외하는지,
코드 지적과 분리되고 "누락·오류"로 단정하지 않는지. 실제 PostgreSQL을 쓰고 모델·외부 호출은 없다."""
import json
import os
import sys
import threading
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID, uuid4

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ai_test_support as support  # noqa: E402

support.require_test_database()
sys.path.insert(0, str(support.ROOT))

import ai_credential_support as C  # noqa: E402
import ai_input_support as S  # noqa: E402
from fastapi import FastAPI, Request  # noqa: E402
from fastapi.exceptions import RequestValidationError  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.exc import DBAPIError  # noqa: E402
from starlette.middleware.sessions import SessionMiddleware  # noqa: E402

from app.api.ai import history as history_api  # noqa: E402
from app.api.ai import jobs as jobs_api  # noqa: E402
from app.api.ai import results as results_api  # noqa: E402
from app.api.ai.dependencies import ai_error_response, ai_validation_error_response  # noqa: E402
from app.routers import auth as auth_router  # noqa: E402
from app.schemas.ai import ErrorCode, canonical_hash  # noqa: E402
from app.services.ai import history_context, job_service  # noqa: E402
from app.services.ai.access import AIError  # noqa: E402
from app.services.analysis_snapshots import payload_digest  # noqa: E402
from app.services.metric_policy import FORMULA_VERSION  # noqa: E402

ORIGIN = 'http://localhost:8000'
URL = 'https://github.com/acme/widgets'
BASE_SHA_PLACEHOLDER = 'a' * 40


def setUpModule():
    S.migrate()


def now():
    return datetime.now(timezone.utc)


def pair(a, b, count, score=0.5, low=False):
    (aid, apath), (bid, bpath) = a, b
    return {'file_a_id': aid, 'file_b_id': bid, 'file_a_path': apath, 'file_b_path': bpath, 'co_change_count': count, 'coupling_score': score,
            'adjusted_score': score / 2, 'change_count_a': 10, 'change_count_b': 8, 'a_to_b_ratio': 0.6, 'b_to_a_ratio': 0.75, 'low_support': low}


def default_spec():
    files = {1: 'main.py', 2: 'src/a.py', 3: 'other.py', 4: 'tests/test_main.py', 5: 'docs/readme.md'}
    return {
        'files': files,
        'risk': {1: dict(change_freq=12, churn=340, bugfix_count=4, unidentified_share=0.0, evidence_status='observed'),
                 2: dict(change_freq=3, churn=40, bugfix_count=0, unidentified_share=0.25, evidence_status='low_support'),
                 3: dict(change_freq=9, churn=90, bugfix_count=1, unidentified_share=0.0, evidence_status='observed'),
                 4: dict(change_freq=7, churn=60, bugfix_count=0, unidentified_share=0.0, evidence_status='observed')},
        'ownership': {1: {'entropy': 1.2, 'entropy_normalized': 0.76, 'bus_factor': 2, 'top_owner_share': 0.55, 'contributor_count': 3, 'observation_count': 12,
                          'low_support': False, 'shares': [{'contributor_id': 1, 'name': 'ann', 'share': 0.55}, {'contributor_id': 2, 'name': 'bo', 'share': 0.3},
                                                           {'contributor_id': 3, 'name': 'cy', 'share': 0.15}]},
                      2: {'entropy': 0.0, 'entropy_normalized': 0.0, 'bus_factor': 1, 'top_owner_share': 1.0, 'contributor_count': 1, 'observation_count': 3,
                          'low_support': True, 'shares': [{'contributor_id': 1, 'name': 'ann', 'share': 1.0}]}},
        'pairs': [pair((1, 'main.py'), (4, 'tests/test_main.py'), 6, 0.8), pair((1, 'main.py'), (3, 'other.py'), 3, 0.3, low=True),
                  pair((2, 'src/a.py'), (3, 'other.py'), 2, 0.2, low=True)],
        'quality': {'commit_count': 42, 'unidentified_commits': 5, 'contributor_count': 4, 'github_account_unlinked': 1, 'merged_pr_count': 20, 'prs_with_files': 18,
                    'prs_detail_complete': 15, 'prs_detail_partial': 2, 'prs_detail_failed': 1, 'prs_detail_unknown': 2, 'low_support_threshold': 5,
                    'target_tree_file_count': 5, 'coverage_scope': '수집된 이력 기준'},
    }


def build_payload(repo_id, url, target_sha, spec):
    files = spec['files']
    risk_rows = [{'file_id': fid, 'path': files[fid], 'maintenance_priority': 0.5, 'risk_score': 0.5, 'priority_components': {},
                  'window_start': '2026-04-01T00:00:00+00:00', 'window_end': '2026-10-01T00:00:00+00:00', **m} for fid, m in spec['risk'].items()]
    return {
        'summary': {'repository': {'id': repo_id, 'name': 'widgets', 'url': url, 'default_branch': 'main', 'last_analyzed_at': '2026-10-01T00:00:00+00:00',
                                   'target_sha': target_sha}, 'total_commits': 42},
        'tree': {'files': [{'file_id': fid, 'path': p, 'evidence_status': (spec['risk'].get(fid) or {}).get('evidence_status', 'no_history')} for fid, p in files.items()]},
        'risk-scores': {'files': risk_rows},
        'coupling': {'meta': {'co_change_unit': 'pull_request', 'total_units': 20, 'fallback_reason': None, 'pr_file_coverage': 0.9}, 'pairs': spec['pairs']},
        'ownership': {str(fid): own for fid, own in spec['ownership'].items()},
        'file_index': [{'id': fid, 'path': p} for fid, p in files.items()],
        'data_quality': {**spec['quality'], 'collected_target_file_count': len(files)},
    }


class HistoryCase(S.InputCase):
    def runTest(self):                                                                           # 다른 시험 클래스가 snapshot 도우미만 빌려 쓸 때의 진입점
        pass

    def setUp(self):
        super().setUp()
        self.snapshot_repos = []

    def tearDown(self):
        self.db.rollback()
        with S.Session() as db:                                                                # 불변 trigger가 있어 시험 데이터는 trigger를 우회해 지운다
            db.execute(text('SET LOCAL session_replication_role = replica'))
            db.execute(text('DELETE FROM ai_evidence'))
            db.execute(text('DELETE FROM analysis_snapshots WHERE repository_id = ANY(:r)'), {'r': self.snapshot_repos})
            db.commit()
        super().tearDown()

    # ── 데이터 ──
    def snapshot(self, repo, *, spec=None, target_sha=None, window_end=None, mutate=None, formula=FORMULA_VERSION, hash_format='canonical-json-numbers-v1',
                 sha_override=None, repo_url=URL, repo_id=None):
        spec = spec or default_spec()
        target_sha = target_sha or self.fx.m1
        payload = build_payload(repo if repo_id is None else repo_id, repo_url, target_sha, spec)
        if mutate:
            mutate(payload)
        digest = payload_digest(payload)
        self.db.execute(text("""INSERT INTO analysis_snapshots (repository_id, target_sha, analyzed_at, window_start, window_end, formula_version, parameters, payload, payload_sha256)
                                VALUES (:r, :t, :a, :ws, :we, :f, CAST(:p AS jsonb), CAST(:pl AS jsonb), :h)"""),
                        {'r': repo, 't': target_sha, 'a': now() - timedelta(days=1), 'ws': now() - timedelta(days=181), 'we': window_end or now() - timedelta(days=1), 'f': formula,
                         'p': json.dumps({'hash_format': hash_format}), 'pl': json.dumps(payload), 'h': sha_override or digest})
        self.db.commit()
        if repo not in self.snapshot_repos:
            self.snapshot_repos.append(repo)
        return self.db.execute(text('SELECT max(id) FROM analysis_snapshots WHERE repository_id = :r'), {'r': repo}).scalar(), payload

    def review(self, finding_paths=('main.py',), kind='review', paths=None):
        """실제 입력 위에 성공한 리뷰 결과와 지적을 만든다. (owner, repo, result_id, input_id)"""
        owner, repo = self.make_user(), self.make_repo()
        _, _, input_id, _ = self.prepared(owner, repo, paths, now=now())
        pinned = C.pinned_for(self.db, owner, repo, input_id, manifest_hash=self.input_row(input_id)['manifest_hash'].strip())
        job = job_service.create_job(self.db, owner_id=owner, repository_id=repo, input_id=input_id, kind=kind, fingerprint=canonical_hash(str(uuid4())), route='r',
                                     idempotency_key=uuid4().hex, body_hash=canonical_hash(str(uuid4())), pinned=pinned).job
        lease = job_service.claim_job(self.db, job.id)
        job_service.finish_job(self.db, lease, 'succeeded')
        result_id = self.db.execute(text("""INSERT INTO ai_results (job_id, owner_user_id, repository_id, payload, payload_hash, coverage, structure_validation, expires_at)
                                            VALUES (:j, :o, :r, '{}'::jsonb, repeat('a', 64), '{}'::jsonb, 'passed', :e) RETURNING id"""),
                                   {'j': job.id, 'o': owner, 'r': repo, 'e': now() + timedelta(days=30)}).scalar()
        file_id = self.db.execute(text('SELECT id FROM ai_input_files WHERE input_id = :i LIMIT 1'), {'i': input_id}).scalar()
        for n, path in enumerate(finding_paths, start=1):
            self.db.execute(text("""INSERT INTO ai_findings (result_id, ordinal, fingerprint, category, priority, title, detail, source_file_id, side, start_line, end_line)
                                    VALUES (:r, :n, :f, 'x', :n, :t, CAST(:d AS jsonb), :s, 'RIGHT', 1, 1)"""),
                            {'r': result_id, 'n': n, 'f': canonical_hash(path), 't': f'finding {n}', 's': file_id, 'd': json.dumps({'location': {'path': path, 'side': 'RIGHT', 'start_line': 1, 'end_line': 1}})})
        self.db.commit()
        return owner, repo, result_id, input_id

    def evidence_count(self):
        return self.scalar('SELECT count(*) FROM ai_evidence')


class Build(HistoryCase):
    def test_unselected_changed_files_have_candidates_without_ai_findings(self):
        owner, repo, result_id, input_id = self.review([], paths=['main.py'])
        spec = default_spec()
        spec['files'][6] = 'src/new_mod.py'
        spec['pairs'].append(pair((6, 'src/new_mod.py'), (4, 'tests/test_main.py'), 7, 0.9))
        sid, _ = self.snapshot(repo, spec=spec)
        evidence = history_context.build_history_evidence(self.db, result_id, sid).payload
        candidate = next(p for p in evidence['paths'] if p['path'] == 'src/new_mod.py')
        self.assertEqual(evidence['scope'], 'all_changed_files')
        self.assertEqual(candidate['co_change'][0]['path'], 'tests/test_main.py')
        self.assertFalse(candidate['co_change'][0]['changed_in_pr'])
        self.assertEqual([f['path'] for f in self.input_row(input_id)['manifest']['files']], ['main.py'])

    def test_numbers_come_from_the_stored_snapshot_and_findings_are_untouched(self):
        owner, repo, result_id, _ = self.review(['main.py'])
        sid, payload = self.snapshot(repo)
        before = (self.scalar('SELECT payload_hash FROM ai_results WHERE id = :r', r=result_id), self.scalar('SELECT count(*) FROM ai_findings'))
        row = history_context.build_history_evidence(self.db, result_id, sid)
        self.db.commit()
        self.assertEqual((row.status, row.exclusion_reasons, row.kind), ('ok', [], 'history'))
        ev = row.payload
        basis = ev['basis']
        self.assertEqual((basis['snapshot_id'], basis['target_sha'], basis['formula_version'], basis['payload_sha256']),
                         (sid, self.fx.m1, FORMULA_VERSION, payload_digest(payload)))
        self.assertEqual((basis['pr_base_sha'], basis['pr_head_sha'], basis['relation']), (self.fx.m1, self.fx.f1, 'same_as_pr_base'))
        main = next(p for p in ev['paths'] if p['path'] == 'main.py')
        risk = payload['risk-scores']['files'][0]
        own = payload['ownership']['1']
        self.assertEqual((main['path'], main['match'], main['status'], main['file_id']), ('main.py', 'path', 'observed', 1))
        self.assertEqual((main['observations']['change_freq'], main['observations']['churn'], main['observations']['bugfix_count']),
                         (risk['change_freq'], risk['churn'], risk['bugfix_count']))                         # snapshot 값 그대로
        self.assertEqual((main['ownership']['observation_count'], main['ownership']['contributor_count'], main['ownership']['top_owner_share'], main['ownership']['bus_factor_80']),
                         (own['observation_count'], own['contributor_count'], own['top_owner_share'], own['bus_factor']))
        self.assertEqual([s['name'] for s in main['ownership']['top_shares']], ['ann', 'bo', 'cy'])
        self.assertEqual([(c['path'], c['co_change_count'], c['coupling_score']) for c in main['co_change']],
                         [('tests/test_main.py', 6, 0.8), ('other.py', 3, 0.3)])                                # 상대 파일 기준 전개, coupling 순
        self.assertEqual(main['co_change_total'], 2)
        q = ev['data_quality']
        self.assertEqual((q['commit_count'], q['unidentified_commits'], q['contributor_count'], q['prs_detail_partial'], q['co_change_unit']),
                         (42, 5, 4, 2, 'pull_request'))                                                      # 미식별 분모·수집 품질을 숨기지 않는다
        # 결과·지적은 바뀌지 않았다(별도 테이블)
        self.assertEqual((self.scalar('SELECT payload_hash FROM ai_results WHERE id = :r', r=result_id), self.scalar('SELECT count(*) FROM ai_findings')), before)
        self.assertEqual(row.payload_hash.strip(), canonical_hash(row.payload))
        self.assertEqual(row.snapshot_sha256.strip(), payload_digest(payload))

    def test_co_change_files_are_candidates_never_stated_as_missing_fixes(self):
        owner, repo, result_id, _ = self.review(['main.py'])
        sid, _ = self.snapshot(repo)
        ev = history_context.build_history_evidence(self.db, result_id, sid).payload
        flags = {c['path']: c['changed_in_pr'] for c in next(p for p in ev['paths'] if p['path'] == 'main.py')['co_change']}
        self.assertEqual(flags, {'tests/test_main.py': False, 'other.py': False})                            # 이 PR에서 함께 바뀌지 않았다는 사실만
        joined = ' '.join(ev['notes'])
        self.assertIn(history_context.TEXTS['NOT_AN_ERROR'], ev['notes'])
        self.assertIn(history_context.TEXTS['SEPARATE'], ev['notes'])
        for claim in ('누락됐습니다', '오류입니다', '버그입니다', '수정해야 합니다', '빠졌습니다'):
            self.assertNotIn(claim, joined)
            self.assertNotIn(claim, json.dumps(ev, ensure_ascii=False))
        self.assertTrue(all(k in c for c in next(p for p in ev['paths'] if p['path'] == 'main.py')['co_change'] for k in ('co_change_count', 'low_support', 'changed_in_pr')))
        # PR에서 함께 바뀐 파일은 changed_in_pr=True
        owner2, repo2, result2, _ = self.review(['main.py'], paths=['main.py', 'src/b.py'])
        spec = default_spec()
        spec['files'][6] = 'src/b.py'
        spec['pairs'] = [pair((1, 'main.py'), (6, 'src/b.py'), 4, 0.6), pair((1, 'main.py'), (4, 'tests/test_main.py'), 6, 0.8)]
        spec['risk'][6] = dict(change_freq=2, churn=5, bugfix_count=0, unidentified_share=0.0, evidence_status='low_support')
        sid2, _ = self.snapshot(repo2, spec=spec)
        ev2 = history_context.build_history_evidence(self.db, result2, sid2).payload
        self.assertEqual({c['path']: c['changed_in_pr'] for c in next(p for p in ev2['paths'] if p['path'] == 'main.py')['co_change']}, {'tests/test_main.py': False, 'src/b.py': True})

    def test_renamed_files_use_the_old_path_history_and_new_files_have_none(self):
        owner, repo, result_id, _ = self.review(['src/b.py', 'src/new_mod.py', 'main.py'])
        sid, _ = self.snapshot(repo)
        paths = {p['path']: p for p in history_context.build_history_evidence(self.db, result_id, sid).payload['paths']}
        renamed = paths['src/b.py']                                                                         # src/a.py → src/b.py
        self.assertEqual((renamed['match'], renamed['matched_path'], renamed['status']), ('old_path', 'src/a.py', 'observed'))
        self.assertIn(history_context.TEXTS['RENAMED'], renamed['limitations'])
        self.assertEqual(renamed['observations']['change_freq'], 3)
        new = paths['src/new_mod.py']
        self.assertEqual((new['status'], new['match'], new['observations'], new['co_change']), ('no_history', 'none', None, []))
        self.assertEqual(new['limitations'], [history_context.TEXTS['NEW_FILE']])                           # 이력이 없다고 해서 문제가 있다고 쓰지 않는다
        self.assertIn('문제가 있다는 뜻은 아닙니다', new['limitations'][0])                                    # 이력 없음을 문제로 단정하지 않는다

    def test_low_support_and_unidentified_shares_are_reported_with_the_numbers(self):
        owner, repo, result_id, _ = self.review(['src/b.py'])
        sid, _ = self.snapshot(repo)
        p = next(p for p in history_context.build_history_evidence(self.db, result_id, sid).payload['paths'] if p['path'] == 'src/b.py')
        self.assertEqual((p['observations']['unidentified_share'], p['observations']['evidence_status'], p['ownership']['low_support'], p['ownership']['observation_count']),
                         (0.25, 'low_support', True, 3))
        self.assertIn(history_context.TEXTS['LOW_SUPPORT'], p['limitations'])
        self.assertIn(history_context.TEXTS['UNIDENTIFIED'], p['limitations'])

    def test_file_in_the_snapshot_without_observations_and_unknown_files(self):
        owner, repo, result_id, _ = self.review(['docs/readme.md', 'main.py'])
        spec = default_spec()
        spec['files'][5] = 'src/new_mod.py'
        sid, _ = self.snapshot(repo, spec=spec)
        paths = {p['path']: p for p in history_context.build_history_evidence(self.db, result_id, sid).payload['paths']}
        quiet = paths['src/new_mod.py']                                                                     # 트리에는 있지만 관측 기간에 변경이 없다
        self.assertEqual((quiet['status'], quiet['ownership']['observation_count']), ('no_observations', 0))
        self.assertIn(history_context.TEXTS['NO_OBSERVATIONS'], quiet['limitations'])
        owner2, repo2, result2, _ = self.review(['main.py'])
        spec = default_spec()
        for table in ('risk', 'ownership'):
            spec[table].pop(1, None)
        spec['files'].pop(1)
        spec['pairs'] = []
        sid2, _ = self.snapshot(repo2, spec=spec)
        gone = next(p for p in history_context.build_history_evidence(self.db, result2, sid2).payload['paths'] if p['path'] == 'main.py')
        self.assertEqual((gone['status'], gone['match']), ('not_in_snapshot', 'none'))                      # 수정된 파일이 snapshot에 없다: 이력 없음으로 단정하지 않는다
        self.assertIn(history_context.TEXTS['NOT_IN_SNAPSHOT'], gone['limitations'])

    def test_basis_difference_between_the_pr_and_the_snapshot_is_shown(self):
        owner, repo, result_id, _ = self.review(['main.py'])
        sid, _ = self.snapshot(repo, target_sha='c' * 40)
        ev = history_context.build_history_evidence(self.db, result_id, sid).payload
        self.assertEqual((ev['basis']['relation'], ev['basis']['target_sha'], ev['basis']['pr_base_sha']), ('differs_from_pr_base', 'c' * 40, self.fx.m1))
        self.assertIn(history_context.TEXTS['BASIS_DIFFERS'], ev['notes'])
        self.assertNotIn(history_context.TEXTS['BASIS_SAME'], ev['notes'])

    def test_a_review_without_findings_still_works_and_says_so(self):
        owner, repo, result_id, _ = self.review([])
        sid, _ = self.snapshot(repo)
        row = history_context.build_history_evidence(self.db, result_id, sid)
        self.assertEqual(row.status, 'ok')
        self.assertTrue(row.payload['paths'])
        self.assertEqual(row.payload['scope'], 'all_changed_files')
        self.assertIn('AI 지적 유무와 무관', ' '.join(row.payload['notes']))

    def test_only_review_results_of_the_same_repository_can_get_evidence(self):
        owner, repo, result_id, _ = self.review(['main.py'])
        other_repo = self.make_repo()
        foreign, _ = self.snapshot(other_repo)
        with self.assertRaises(AIError) as ctx:
            history_context.build_history_evidence(self.db, result_id, foreign)
        self.assertEqual(ctx.exception.code, ErrorCode.RESOURCE_NOT_FOUND)                                   # 다른 저장소의 snapshot은 존재를 숨긴다
        with self.assertRaises(AIError) as ctx:
            history_context.build_history_evidence(self.db, result_id, 987654)
        self.assertEqual(ctx.exception.code, ErrorCode.RESOURCE_NOT_FOUND)
        owner2, repo2, summary_result, _ = self.review([], kind='summary')
        sid2, _ = self.snapshot(repo2)
        with self.assertRaises(AIError) as ctx:
            history_context.build_history_evidence(self.db, summary_result, sid2)
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)
        self.assertEqual(self.evidence_count(), 0)

    def test_evidence_is_created_once_immutable_and_deleted_with_its_result(self):
        owner, repo, result_id, _ = self.review(['main.py'])
        sid, _ = self.snapshot(repo)
        first = history_context.build_history_evidence(self.db, result_id, sid)
        self.db.commit()
        again = history_context.build_history_evidence(self.db, result_id, sid)
        self.assertEqual((again.id, self.evidence_count()), (first.id, 1))
        with self.assertRaises(DBAPIError):
            with self.db.begin_nested():
                self.db.execute(text("UPDATE ai_evidence SET status = 'excluded', exclusion_reasons = '[\"x\"]'::jsonb"))
        self.db.rollback()
        # 동시 요청도 하나만 남는다
        owner2, repo2, result2, _ = self.review(['main.py'])
        sid2, _ = self.snapshot(repo2)
        ids = []

        def run():
            with S.Session() as db:
                ids.append(history_context.build_history_evidence(db, result2, sid2).id)
                db.commit()
        threads = [threading.Thread(target=run) for _ in range(5)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual((len(set(ids)), self.scalar('SELECT count(*) FROM ai_evidence WHERE result_id = :r', r=result2)), (1, 1))
        self.db.execute(text('DELETE FROM ai_results WHERE id = :r'), {'r': result_id})
        self.db.commit()
        self.assertEqual(self.scalar('SELECT count(*) FROM ai_evidence WHERE result_id = :r', r=result_id), 0)   # 결과 삭제와 함께 사라진다

    def test_database_rejects_evidence_pointing_at_another_repositorys_snapshot(self):
        owner, repo, result_id, _ = self.review(['main.py'])
        other = self.make_repo()
        foreign, _ = self.snapshot(other)
        with self.assertRaises(DBAPIError):
            self.db.execute(text("""INSERT INTO ai_evidence (result_id, owner_user_id, repository_id, snapshot_id, snapshot_sha256, status, as_of, payload, payload_hash)
                                    VALUES (:r, :o, :repo, :s, repeat('a', 64), 'ok', now(), '{}'::jsonb, repeat('b', 64))"""),
                            {'r': result_id, 'o': owner, 'repo': repo, 's': foreign})
        self.db.rollback()

    def test_history_text_is_pinned(self):
        self.assertEqual(history_context.text_digest(), history_context.TEXT_DIGEST)                         # 문구를 바꾸면 새 버전 파일을 만든다
        self.assertEqual(len(history_context.TEXTS), 12)


class Eligibility(HistoryCase):
    def excluded(self, **kw):
        owner, repo, result_id, _ = self.review(['main.py'])
        mutate = kw.pop('mutate', None)
        sid, payload = self.snapshot(repo, mutate=mutate, **kw)
        row = history_context.build_history_evidence(self.db, result_id, sid)
        self.db.commit()
        return row

    def assertExcluded(self, row, reason):
        self.assertEqual(row.status, 'excluded')
        self.assertIn(reason, row.exclusion_reasons)
        self.assertEqual(row.payload['paths'], [])                                                           # 값을 싣지 않는다
        self.assertEqual(row.payload['data_quality'], {})
        self.assertIn(history_context.TEXTS['EXCLUDED'], row.payload['notes'])
        self.assertNotIn('co_change', json.dumps(row.payload))

    def test_valid_snapshot_has_no_exclusion_reasons(self):
        owner, repo, result_id, _ = self.review(['main.py'])
        sid, _ = self.snapshot(repo)
        from app.models.analysis_snapshot import AnalysisSnapshot
        self.assertEqual(history_context.snapshot_eligibility(self.db, self.db.get(AnalysisSnapshot, sid), as_of=now()), [])

    def test_hash_mismatch_legacy_format_and_old_formula_are_excluded(self):
        self.assertExcluded(self.excluded(sha_override='0' * 64), 'integrity_failed')
        self.assertExcluded(self.excluded(hash_format='legacy-v1'), 'legacy_format')
        self.assertExcluded(self.excluded(formula='gitviz-2026.09-risk-v2'), 'formula_unsupported')

    def test_missing_or_inconsistent_sha_and_repository_identity_are_excluded(self):
        self.assertExcluded(self.excluded(target_sha='not-a-sha'), 'target_sha_missing')
        self.assertExcluded(self.excluded(mutate=lambda p: p['summary']['repository'].update(target_sha='d' * 40)), 'target_sha_missing')   # payload와 행의 SHA가 다르다
        self.assertExcluded(self.excluded(mutate=lambda p: p['summary']['repository'].pop('url')), 'repository_identity_missing')
        self.assertExcluded(self.excluded(mutate=lambda p: p['summary']['repository'].pop('id')), 'repository_identity_missing')
        self.assertExcluded(self.excluded(repo_url='https://github.com/other/project'), 'repository_identity_mismatch')
        self.assertExcluded(self.excluded(repo_id=999999), 'repository_identity_mismatch')

    def test_incomplete_or_inconsistent_payloads_are_excluded(self):
        self.assertExcluded(self.excluded(mutate=lambda p: p.pop('coupling')), 'payload_incomplete')
        self.assertExcluded(self.excluded(mutate=lambda p: p.pop('ownership')), 'payload_incomplete')
        self.assertExcluded(self.excluded(mutate=lambda p: p['data_quality'].update(collected_target_file_count=99)), 'payload_incomplete')   # 파일 수가 맞지 않는다
        self.assertExcluded(self.excluded(mutate=lambda p: p['tree'].update(files='x')), 'payload_incomplete')

    def test_future_data_is_excluded_for_review_time_and_for_an_earlier_evaluation_time(self):
        self.assertExcluded(self.excluded(window_end=now() + timedelta(hours=2)), 'future_data')             # 리뷰 결과가 만들어진 뒤의 이력
        owner, repo, result_id, _ = self.review(['main.py'])
        sid, _ = self.snapshot(repo, window_end=now() - timedelta(days=1))
        early = history_context.build_history_evidence(self.db, result_id, sid, as_of=now() - timedelta(days=3))
        self.assertExcluded(early, 'future_data')                                                            # 평가 시점(더 이른 as_of) 이후의 자료
        self.assertLess(early.as_of, now() - timedelta(days=2))
        owner2, repo2, result2, _ = self.review(['main.py'])
        sid2, _ = self.snapshot(repo2, window_end=now() - timedelta(days=1))
        late = history_context.build_history_evidence(self.db, result2, sid2, as_of=now() + timedelta(days=30))
        self.assertEqual(late.status, 'ok')                                                                  # as_of는 더 엄격하게만 작용한다(결과 시각보다 늦게 못 잡는다)

    def test_existing_evidence_is_not_returned_for_an_as_of_that_judges_differently(self):
        # 더 이른 평가 시점에서는 미래 자료가 되는데 이미 ok로 만든 근거를 조용히 돌려주면 안 된다
        owner, repo, result_id, _ = self.review(['main.py'])
        sid, _ = self.snapshot(repo, window_end=now() - timedelta(days=1))
        ok = history_context.build_history_evidence(self.db, result_id, sid)
        self.db.commit()
        self.assertEqual(ok.status, 'ok')
        with self.assertRaises(AIError) as ctx:
            history_context.build_history_evidence(self.db, result_id, sid, as_of=now() - timedelta(days=3))
        self.assertEqual((ctx.exception.code, ctx.exception.details['reason']), (ErrorCode.VERSION_CONFLICT, 'HISTORY_AS_OF_CONFLICT'))
        self.assertEqual(self.evidence_count(), 1)                                                           # 새 행도 만들지 않는다
        same = history_context.build_history_evidence(self.db, result_id, sid, as_of=now() - timedelta(hours=1))
        self.assertEqual(same.id, ok.id)                                                                     # 같은 판정이면 기존 근거
        # 반대로 이른 시점으로 excluded를 만든 뒤 풀리는 늦은 시점을 요청해도 다른 판정을 돌려주지 않는다
        owner2, repo2, result2, _ = self.review(['main.py'])
        sid2, _ = self.snapshot(repo2, window_end=now() - timedelta(days=1))
        early = history_context.build_history_evidence(self.db, result2, sid2, as_of=now() - timedelta(days=3))
        self.db.commit()
        self.assertEqual((early.status, early.exclusion_reasons), ('excluded', ['future_data']))
        with self.assertRaises(AIError) as ctx:
            history_context.build_history_evidence(self.db, result2, sid2)
        self.assertEqual(ctx.exception.code, ErrorCode.VERSION_CONFLICT)

    def test_a_concurrent_winner_is_compared_with_this_requests_as_of(self):
        # 판정과 저장 사이에 다른 요청이 근거를 먼저 저장하는 경쟁: UNIQUE 충돌 뒤에도 요청 시점의 판정과 비교해야 한다
        owner, repo, result_id, _ = self.review(['main.py'])
        sid, _ = self.snapshot(repo, window_end=now() - timedelta(days=1))
        real = history_context.snapshot_eligibility
        calls = []

        def racing(db, snapshot, *, as_of):
            reasons = real(db, snapshot, as_of=as_of)
            if not calls:
                calls.append(1)
                with S.Session() as other:                                                                   # 먼저 도착한 요청이 as_of 없이 ok로 저장한다
                    self.assertEqual(history_context.build_history_evidence(other, result_id, sid).status, 'ok')
                    other.commit()
            return reasons
        with patch.object(history_context, 'snapshot_eligibility', side_effect=racing):
            with self.assertRaises(AIError) as ctx:
                history_context.build_history_evidence(self.db, result_id, sid, as_of=now() - timedelta(days=3))   # 이 요청의 시점에서는 미래 자료
        self.assertEqual((ctx.exception.code, ctx.exception.details['reason']), (ErrorCode.VERSION_CONFLICT, 'HISTORY_AS_OF_CONFLICT'))
        self.db.rollback()
        self.assertEqual([(r['status']) for r in self.db.execute(text('SELECT status FROM ai_evidence')).mappings()], ['ok'])

    def test_concurrent_requests_with_different_as_of_never_return_two_judgements(self):
        for _ in range(3):
            owner, repo, result_id, _ = self.review(['main.py'])
            sid, _ = self.snapshot(repo, window_end=now() - timedelta(days=1))
            barrier, outcomes = threading.Barrier(2), []

            def run(as_of):
                with S.Session() as db:
                    barrier.wait()
                    try:
                        row = history_context.build_history_evidence(db, result_id, sid, as_of=as_of)
                        db.commit()
                        outcomes.append(('row', row.status))
                    except AIError as e:
                        db.rollback()
                        outcomes.append(('error', e.code))
            threads = [threading.Thread(target=run, args=(a,)) for a in (None, now() - timedelta(days=3))]
            [t.start() for t in threads]
            [t.join() for t in threads]
            self.assertEqual(len({o for o in outcomes if o[0] == 'row'}), 1, outcomes)                       # 서로 다른 판정을 둘 다 돌려주지 않는다
            self.assertEqual(sorted(o[0] for o in outcomes), ['error', 'row'], outcomes)
            self.assertEqual(self.scalar('SELECT count(*) FROM ai_evidence WHERE result_id = :r', r=result_id), 1)

    def test_multiple_reasons_are_all_reported_and_options_list_them(self):
        owner, repo, result_id, _ = self.review(['main.py'])
        bad, _ = self.snapshot(repo, hash_format='legacy-v1', formula='old', target_sha='x')
        good, _ = self.snapshot(repo)
        options = {o['snapshot_id']: o for o in history_context.snapshot_options(self.db, repo, as_of=now())}
        self.assertTrue(options[good]['eligible'])
        self.assertFalse(options[bad]['eligible'])
        self.assertTrue({'legacy_format', 'formula_unsupported', 'target_sha_missing'} <= set(options[bad]['reasons']))
        self.assertTrue(all(options[bad]['reason_texts']))

    def test_excluded_evidence_never_changes_the_review_and_reads_back(self):
        owner, repo, result_id, _ = self.review(['main.py'])
        sid, _ = self.snapshot(repo, formula='old')
        row = history_context.build_history_evidence(self.db, result_id, sid)
        self.db.commit()
        self.assertEqual([e.status for e in history_context.list_evidence(self.db, result_id)], ['excluded'])
        self.assertEqual(self.scalar('SELECT count(*) FROM ai_findings'), 1)


class Safety(HistoryCase):
    def test_attach_safely_never_breaks_the_surrounding_transaction(self):
        owner, repo, result_id, _ = self.review(['main.py'])
        sid, _ = self.snapshot(repo)
        marker = self.db.execute(text("INSERT INTO audit_events (action, object_type, request_id) VALUES ('marker', 'x', 'm') RETURNING id")).scalar()
        with patch.object(history_context, 'build_history_evidence', side_effect=RuntimeError('boom')):
            self.assertIsNone(history_context.attach_safely(self.db, result_id, sid))                       # 실패해도 예외를 올리지 않는다
        self.db.commit()
        self.assertEqual(self.scalar('SELECT count(*) FROM audit_events WHERE id = :i', i=marker), 1)         # 바깥 transaction의 작업은 그대로 커밋된다
        self.assertEqual(self.evidence_count(), 0)
        self.assertIsNotNone(history_context.attach_safely(self.db, result_id, sid))
        self.db.commit()
        self.assertEqual(self.evidence_count(), 1)
        self.assertIsNone(history_context.attach_safely(self.db, result_id, 424242))                        # 없는 snapshot: 조용히 건너뛴다
        self.assertIsNone(history_context.attach_safely(self.db, uuid4(), sid))


from test_ai_generation import Gen, USAGE, USER_KEY  # noqa: E402  (생성 실행 테스트의 공용 대역)


class WithGeneration(Gen):
    """리뷰 작업이 history_snapshot_id를 받으면 결과를 저장할 때 이력 근거를 붙이고, 어떤 경우에도 리뷰 결과 저장은 막지 않는다."""

    def tearDown(self):
        with S.Session() as db:
            db.execute(text('SET LOCAL session_replication_role = replica'))
            db.execute(text('DELETE FROM ai_evidence'))
            db.execute(text('DELETE FROM analysis_snapshots'))
            db.commit()
        super().tearDown()

    def history_snapshot(self, repo, **kw):
        helper = HistoryCase('runTest')
        helper.fx, helper.db, helper.snapshot_repos = self.fx, self.db, []
        return helper.snapshot(repo, **kw)[0]

    def world_with_history(self, snapshot_kwargs=None, snapshot_id=None):
        owner, repo = self.make_user(), self.make_repo()
        sid = snapshot_id if snapshot_id is not None else self.history_snapshot(repo, **(snapshot_kwargs or {}))
        _, _, input_id, _ = self.prepared(owner, repo, now=now(), pr_number=7)
        C.ensure_credential(self.db, owner, key=C.TEST_KEY, profile_id=C.OPUS)
        pinned = C.pinned_for(self.db, owner, repo, input_id, manifest_hash=self.input_row(input_id)['manifest_hash'].strip())
        job = job_service.create_job(self.db, owner_id=owner, repository_id=repo, input_id=input_id, kind='review', fingerprint=canonical_hash(str(uuid4())),
                                     route='r', idempotency_key=uuid4().hex, body_hash=canonical_hash(str(uuid4())), request_options={'history_snapshot_id': sid},
                                     pinned=pinned).job
        self.output = self.valid_output('review', job.id)
        return SimpleNamespace(owner=owner, repo=repo, job=job, sid=sid)

    def evidence_rows(self):
        return self.rows('SELECT status, exclusion_reasons, snapshot_id FROM ai_evidence')

    def test_review_job_attaches_verified_history_when_it_succeeds(self):
        w = self.world_with_history()
        self.assertEqual(self.run_job(w), 'succeeded')
        rows = self.evidence_rows()
        self.assertEqual([(r['status'], r['snapshot_id']) for r in rows], [('ok', w.sid)])
        result = self.rows('SELECT id FROM ai_results')[0]['id']
        self.assertEqual(self.rows('SELECT result_id FROM ai_evidence')[0]['result_id'], result)
        self.assertEqual(self.rows('SELECT count(*) AS n FROM ai_findings')[0]['n'], 1)                      # 코드 지적은 그대로, 근거는 별도 행
        self.assertEqual(len(self.messages_calls()), 1)                                                     # 이력 근거는 모델 호출을 늘리지 않는다
        for _, _, body in self.messages_calls():
            self.assertNotIn(b'co_change', body)                                                            # 이력 값은 모델로 전송되지 않는다

    def test_an_unverified_snapshot_is_recorded_as_excluded_and_the_review_still_succeeds(self):
        w = self.world_with_history({'formula': 'old-formula'})
        self.assertEqual(self.run_job(w), 'succeeded')
        rows = self.evidence_rows()
        self.assertEqual((rows[0]['status'], rows[0]['exclusion_reasons']), ('excluded', ['formula_unsupported']))
        self.assertEqual(self.rows('SELECT count(*) AS n FROM ai_results')[0]['n'], 1)

    def test_evidence_failure_never_blocks_saving_the_review_result(self):
        w = self.world_with_history()
        with patch.object(history_context, 'build_history_evidence', side_effect=RuntimeError('boom')):
            self.assertEqual(self.run_job(w), 'succeeded')
        self.assertEqual((self.rows('SELECT count(*) AS n FROM ai_results')[0]['n'], len(self.evidence_rows())), (1, 0))
        self.assertEqual(self.job_row(w.job.id)['status'], 'succeeded')

    def test_job_without_the_option_has_no_evidence(self):
        w = self.world('review')
        self.assertEqual(self.run_job(w), 'succeeded')
        self.assertEqual(self.evidence_rows(), [])


class Api(Gen):
    def setUp(self):
        super().setUp()
        self.deny = None

        def authorize(db, user_id, repo_id, action, policy):
            if self.deny:
                raise self.deny
            return None, {'private': False}
        self._api_patch = patch.object(jobs_api, 'authorize_ai', side_effect=authorize)
        self._api_patch.start()
        app = FastAPI()
        app.add_middleware(SessionMiddleware, secret_key='api-test', same_site='lax')
        app.add_exception_handler(AIError, ai_error_response)
        app.add_exception_handler(RequestValidationError, ai_validation_error_response)
        for module in (auth_router, jobs_api, results_api, history_api):
            app.include_router(module.router)

        @app.post('/test/login/{user_id}')
        async def login(user_id: str, request: Request):
            request.session['user_id'] = user_id
            return {}
        self.client = TestClient(app)

    def tearDown(self):
        self._api_patch.stop()
        self.client.close()
        with S.Session() as db:
            db.execute(text('SET LOCAL session_replication_role = replica'))
            db.execute(text('DELETE FROM ai_evidence'))
            db.execute(text('DELETE FROM analysis_snapshots'))
            db.commit()
        super().tearDown()

    def login(self, user):
        self.client.post(f'/test/login/{user}')
        self.token = self.client.get('/api/auth/csrf').json()['token']

    def headers(self, key=None):
        return {'Origin': ORIGIN, 'X-CSRF-Token': self.token, **({'Idempotency-Key': key} if key else {})}

    def err(self, r, status, code):
        self.assertEqual(r.status_code, status, r.text)
        self.assertEqual(r.json()['error']['code'], code)
        self.assertEqual(r.headers['cache-control'], 'private, no-store')

    def succeeded_review(self):
        w = self.world('review')
        self.assertEqual(self.run_job(w), 'succeeded')
        result_id = self.rows('SELECT id FROM ai_results')[0]['id']
        self.login(w.owner)
        return w, str(result_id)

    def snap(self, repo, **kw):
        helper = HistoryCase('runTest')
        helper.fx, helper.db, helper.snapshot_repos = self.fx, self.db, []
        return helper.snapshot(repo, **kw)[0]

    def test_options_attach_read_and_idempotent_replay(self):
        w, result_id = self.succeeded_review()
        good, bad = self.snap(w.repo), self.snap(w.repo, formula='old')
        options = self.client.get('/api/ai/v1/history/snapshots', params={'repository_id': w.repo})
        self.assertEqual(options.status_code, 200)
        self.assertEqual(options.headers['cache-control'], 'private, no-store')
        by_id = {o['snapshot_id']: o for o in options.json()['snapshots']}
        self.assertEqual((by_id[good]['eligible'], by_id[bad]['eligible'], by_id[bad]['reasons']), (True, False, ['formula_unsupported']))
        self.assertEqual(self.client.get(f'/api/ai/v1/results/{result_id}/history').json()['evidence'], [])
        created = self.client.post(f'/api/ai/v1/results/{result_id}/history', json={'snapshot_id': good}, headers=self.headers())
        self.assertEqual(created.status_code, 201, created.text)
        body = created.json()
        self.assertEqual((body['status'], body['snapshot_id'], body['evidence']['basis']['snapshot_id'], next(p for p in body['evidence']['paths'] if p['path'] == 'main.py')['path']), ('ok', good, good, 'main.py'))
        again = self.client.post(f'/api/ai/v1/results/{result_id}/history', json={'snapshot_id': good}, headers=self.headers())
        self.assertEqual((again.status_code, again.json()['evidence_id']), (200, body['evidence_id']))
        excluded = self.client.post(f'/api/ai/v1/results/{result_id}/history', json={'snapshot_id': bad}, headers=self.headers())
        self.assertEqual((excluded.status_code, excluded.json()['status'], excluded.json()['exclusion_reasons']), (201, 'excluded', ['formula_unsupported']))
        listed = self.client.get(f'/api/ai/v1/results/{result_id}/history').json()['evidence']
        self.assertEqual(sorted(e['status'] for e in listed), ['excluded', 'ok'])
        self.assertEqual(self.rows("SELECT count(*) AS n FROM audit_events WHERE action = 'history.attached'")[0]['n'], 2)

    def test_access_ownership_and_validation(self):
        w, result_id = self.succeeded_review()
        sid = self.snap(w.repo)
        anon = TestClient(self.client.app)
        self.err(anon.get(f'/api/ai/v1/results/{result_id}/history'), 401, 'AUTH_REQUIRED')
        self.err(self.client.post(f'/api/ai/v1/results/{result_id}/history', json={'snapshot_id': sid}), 403, 'CSRF_FAILED')
        self.err(self.client.post(f'/api/ai/v1/results/{result_id}/history', json={'snapshot_id': 0}, headers=self.headers()), 422, 'INVALID_SELECTION')
        self.err(self.client.post(f'/api/ai/v1/results/{result_id}/history', json={'snapshot_id': sid, 'x': 1}, headers=self.headers()), 422, 'INVALID_SELECTION')
        other_repo_snapshot = self.snap(self.make_repo())
        self.err(self.client.post(f'/api/ai/v1/results/{result_id}/history', json={'snapshot_id': other_repo_snapshot}, headers=self.headers()), 404, 'RESOURCE_NOT_FOUND')
        self.deny = AIError(ErrorCode.RESOURCE_NOT_FOUND, 'gone')                                           # 저장소 접근 권한을 잃음
        self.err(self.client.get(f'/api/ai/v1/results/{result_id}/history'), 404, 'RESOURCE_NOT_FOUND')
        self.err(self.client.post(f'/api/ai/v1/results/{result_id}/history', json={'snapshot_id': sid}, headers=self.headers()), 404, 'RESOURCE_NOT_FOUND')
        self.err(self.client.get('/api/ai/v1/history/snapshots', params={'repository_id': w.repo}), 404, 'RESOURCE_NOT_FOUND')
        self.deny = None
        self.login(self.make_user())                                                                         # 남의 결과
        self.err(self.client.get(f'/api/ai/v1/results/{result_id}/history'), 404, 'RESOURCE_NOT_FOUND')
        self.err(self.client.post(f'/api/ai/v1/results/{result_id}/history', json={'snapshot_id': sid}, headers=self.headers()), 404, 'RESOURCE_NOT_FOUND')
        self.assertEqual(self.rows('SELECT count(*) AS n FROM ai_evidence')[0]['n'], 0)

    def test_expired_result_and_summary_results_are_refused(self):
        w, result_id = self.succeeded_review()
        sid = self.snap(w.repo)
        self.bypass("UPDATE ai_results SET expires_at = now() - interval '1 minute'")
        self.err(self.client.post(f'/api/ai/v1/results/{result_id}/history', json={'snapshot_id': sid}, headers=self.headers()), 410, 'RESULT_EXPIRED')
        self.err(self.client.get(f'/api/ai/v1/results/{result_id}/history'), 410, 'RESULT_EXPIRED')

    def test_job_request_accepts_a_snapshot_of_the_same_repository_only(self):
        owner, repo = self.make_user(), self.make_repo()
        _, _, input_id, _ = self.prepared(owner, repo, now=now())
        C.ensure_credential(self.db, owner, key=C.TEST_KEY)
        self.login(owner)
        row = self.input_row(input_id)
        consent = {'input_id': str(input_id), 'manifest_hash': row['manifest_hash'].strip(), 'provider': 'anthropic', 'profile_id': C.OPUS,
                   'credential_version': 1, 'policy_version': 'code-transfer-v1'}
        foreign = self.snap(self.make_repo())
        self.err(self.client.post('/api/ai/v1/jobs', json={'kind': 'review', 'consent': consent, 'history_snapshot_id': foreign}, headers=self.headers('k1')),
                 422, 'INVALID_SELECTION')
        mine = self.snap(repo)
        r = self.client.post('/api/ai/v1/jobs', json={'kind': 'review', 'consent': consent, 'history_snapshot_id': mine}, headers=self.headers('k2'))
        self.assertEqual(r.status_code, 202, r.text)
        self.assertEqual(self.job_row(r.json()['job_id'])['request_options'], {'history_snapshot_id': mine})
        summary = self.client.post('/api/ai/v1/jobs', json={'kind': 'summary', 'consent': consent, 'history_snapshot_id': mine}, headers=self.headers('k3'))
        self.assertEqual(self.job_row(summary.json()['job_id'])['request_options'], {})                      # 설명 작업에는 이력 근거가 없다
        self.err(self.client.post('/api/ai/v1/jobs', json={'kind': 'review', 'consent': consent, 'history_snapshot_id': 0}, headers=self.headers('k4')), 422, 'INVALID_SELECTION')

    def start_review(self, snapshot=None, key='r1'):
        owner, repo = self.make_user(), self.make_repo()
        _, _, input_id, _ = self.prepared(owner, repo, now=now(), pr_number=7)
        C.ensure_credential(self.db, owner, key=USER_KEY)
        self.login(owner)
        consent = {'input_id': str(input_id), 'manifest_hash': self.input_row(input_id)['manifest_hash'].strip(), 'provider': 'anthropic', 'profile_id': C.OPUS,
                   'credential_version': 1, 'policy_version': 'code-transfer-v1'}
        self.consent = consent
        return owner, repo, input_id, self.post_job(consent, snapshot, key)

    def post_job(self, consent, snapshot, key):
        body = {'kind': 'review', 'consent': consent}
        if snapshot is not None:
            body['history_snapshot_id'] = snapshot
        return self.client.post('/api/ai/v1/jobs', json=body, headers=self.headers(key))

    def test_reusing_a_finished_review_attaches_the_newly_requested_snapshot(self):
        owner, repo = None, None
        first = self.start_review()
        owner, repo, input_id, r = first
        self.assertEqual(r.status_code, 202, r.text)
        job_id = r.json()['job_id']
        w = SimpleNamespace(owner=owner, repo=repo, input_id=input_id, job=SimpleNamespace(id=UUID(job_id)), kind='review')
        self.output = self.valid_output('review', w.job.id)
        self.assertEqual(self.run_job(w), 'succeeded')
        snap = self.snap(repo)
        again = self.post_job(self.consent, snap, 'r2')                                                      # 같은 입력·설정: 새 작업 없이 재사용
        self.assertEqual((again.status_code, again.json()['job_id']), (200, job_id), again.text)
        rows = self.rows('SELECT snapshot_id, status FROM ai_evidence')
        self.assertEqual([(x['snapshot_id'], x['status']) for x in rows], [(snap, 'ok')])                    # 지정한 snapshot이 무시되지 않았다
        self.assertEqual(self.rows("SELECT count(*) AS n FROM ai_jobs WHERE kind = 'review'")[0]['n'], 1)
        other = self.snap(repo, formula='old')
        self.assertEqual(self.post_job(self.consent, other, 'r3').status_code, 200)
        self.assertEqual(sorted(x['status'] for x in self.rows('SELECT status FROM ai_evidence')), ['excluded', 'ok'])
        self.assertEqual(self.post_job(self.consent, snap, 'r4').status_code, 200)                           # 이미 있으면 중복 없음
        self.assertEqual(self.rows('SELECT count(*) AS n FROM ai_evidence')[0]['n'], 2)
        self.assertEqual(self.rows("SELECT count(*) AS n FROM audit_events WHERE action = 'history.attached'")[0]['n'], 2)

    def test_retry_with_the_same_key_recovers_an_attachment_that_failed(self):
        owner, repo, input_id, r = self.start_review()
        job_id = r.json()['job_id']
        w = SimpleNamespace(owner=owner, repo=repo, input_id=input_id, job=SimpleNamespace(id=UUID(job_id)), kind='review')
        self.output = self.valid_output('review', w.job.id)
        self.assertEqual(self.run_job(w), 'succeeded')
        snap = self.snap(repo)
        real = history_context.build_history_evidence
        with patch.object(history_context, 'build_history_evidence', side_effect=RuntimeError('boom')):
            with self.assertRaises(RuntimeError):
                self.post_job(self.consent, snap, 'again')                                                   # 멱등 key는 확정됐지만 첨부가 실패
        self.assertEqual(self.rows('SELECT count(*) AS n FROM ai_evidence')[0]['n'], 0)
        retry = self.post_job(self.consent, snap, 'again')                                                   # 같은 key 재전송
        self.assertEqual((retry.status_code, retry.json()['job_id']), (200, job_id), retry.text)
        self.assertEqual([(x['snapshot_id'], x['status']) for x in self.rows('SELECT snapshot_id, status FROM ai_evidence')], [(snap, 'ok')])
        again = self.post_job(self.consent, snap, 'again')                                                   # 세 번째는 중복 없이 그대로
        self.assertEqual((again.status_code, self.rows('SELECT count(*) AS n FROM ai_evidence')[0]['n']), (200, 1))
        self.assertEqual(self.rows("SELECT count(*) AS n FROM audit_events WHERE action = 'history.attached'")[0]['n'], 1)
        self.assertIs(history_context.build_history_evidence, real)

    def test_reusing_an_in_progress_review_never_drops_a_different_snapshot_silently(self):
        owner, repo, input_id, r = self.start_review()
        self.assertEqual(r.status_code, 202)
        job_id = r.json()['job_id']
        snap = self.snap(repo)
        conflict = self.post_job(self.consent, snap, 'p2')
        self.err(conflict, 409, 'VERSION_CONFLICT')
        self.assertEqual(conflict.json()['error']['details']['reason'], 'HISTORY_SNAPSHOT_DIFFERS')
        self.assertEqual(self.post_job(self.consent, None, 'p3').json()['job_id'], job_id)                   # 근거를 요청하지 않으면 그대로 재사용
        self.assertEqual(self.rows("SELECT count(*) AS n FROM ai_jobs WHERE kind = 'review'")[0]['n'], 1)

    def test_attach_endpoint_reports_a_conflicting_as_of(self):
        w, result_id = self.succeeded_review()
        sid = self.snap(w.repo, window_end=now() - timedelta(days=1))
        self.assertEqual(self.client.post(f'/api/ai/v1/results/{result_id}/history', json={'snapshot_id': sid}, headers=self.headers()).status_code, 201)
        early = (now() - timedelta(days=3)).isoformat()
        self.err(self.client.post(f'/api/ai/v1/results/{result_id}/history', json={'snapshot_id': sid, 'as_of': early}, headers=self.headers()), 409, 'VERSION_CONFLICT')

    def test_existing_idempotency_keys_stay_valid_when_the_option_is_absent(self):
        body = SimpleNamespace(model_dump=lambda mode: {'kind': 'review', 'consent': {'input_id': 'x', 'profile_fingerprint': None}, 'force_regenerate': False,
                                                         'history_snapshot_id': None})
        legacy = SimpleNamespace(model_dump=lambda mode: {'kind': 'review', 'consent': {'input_id': 'x'}, 'force_regenerate': False})
        self.assertEqual(jobs_api._request_hash(body), jobs_api._request_hash(legacy))
        with_option = SimpleNamespace(model_dump=lambda mode: {'kind': 'review', 'consent': {'input_id': 'x'}, 'force_regenerate': False, 'history_snapshot_id': 3})
        self.assertNotEqual(jobs_api._request_hash(with_option), jobs_api._request_hash(legacy))


if __name__ == '__main__':
    unittest.main(verbosity=2)
