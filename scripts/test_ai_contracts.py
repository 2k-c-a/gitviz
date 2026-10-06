"""S0 AI 계약 구조 테스트. DB·네트워크 없이 실행된다."""
import copy
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ai_test_support as support  # noqa: E402
sys.path.insert(0, str(support.ROOT))

from pydantic import ValidationError  # noqa: E402
from app.schemas.ai import (  # noqa: E402
    ERROR_HTTP_STATUS, CodeLocation, Coverage, ErrorCode, ErrorEnvelope, Finding,
    InputManifest, JobState, ManifestFile, PRRevision, PrepareInputRequest, Side,
    canonical_hash)

SHA_A, SHA_B, SHA_C = 'a' * 40, 'b' * 40, 'c' * 40
REV_ID = '00000000-0000-4000-8000-000000000101'
JOB_ID = '00000000-0000-4000-8000-000000000201'
H64 = 'd' * 64

COVERAGE = {'inventory_coverage': 'complete', 'selected_content_coverage': 'complete',
            'analysis_coverage': 'none', 'selected_files': 3, 'pr_total_files': 8}


def revision(**kw):
    return {'repo_id': 14, 'pr_number': 27, 'base_sha': SHA_A, 'head_sha': SHA_B,
            'merge_base_sha': SHA_C, **kw}


def read_file(path='a.py', **kw):
    return {'path': path, 'change_type': 'modified', 'content_state': 'read', 'content_hash': H64, **kw}


class RevisionAndLocation(unittest.TestCase):
    def test_valid_revision(self):
        PRRevision(**revision())
        PRRevision(**revision(base_sha='e' * 64))  # sha256 저장소

    def test_invalid_sha_rejected(self):
        for bad in ['', 'abc', 'A' * 40, 'g' * 40, 'a' * 39, 'a' * 41]:
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                PRRevision(**revision(head_sha=bad))

    def test_missing_or_nonpositive_ids_rejected(self):
        for kw in ({'repo_id': 0}, {'pr_number': -1}):
            with self.assertRaises(ValidationError):
                PRRevision(**revision(**kw))
        with self.assertRaises(ValidationError):
            PRRevision(**{k: v for k, v in revision().items() if k != 'merge_base_sha'})

    def test_location_bounds(self):
        base = {'path': 'a.py', 'side': 'RIGHT', 'start_line': 1, 'end_line': 1, 'revision_id': REV_ID}
        CodeLocation(**base)
        for patch in ({'start_line': 0}, {'start_line': -1}, {'end_line': 0},
                      {'start_line': 5, 'end_line': 4}, {'side': 'BOTH'}):
            with self.subTest(patch=patch), self.assertRaises(ValidationError):
                CodeLocation(**{**base, **patch})


class CoverageRules(unittest.TestCase):
    def test_example_from_design(self):
        c = Coverage(**COVERAGE)
        self.assertEqual((c.selected_files, c.pr_total_files), (3, 8))

    def test_unknown_inventory_has_no_denominator(self):
        Coverage(**{**COVERAGE, 'inventory_coverage': 'unknown', 'pr_total_files': None})
        with self.assertRaises(ValidationError):
            Coverage(**{**COVERAGE, 'inventory_coverage': 'unknown'})
        with self.assertRaises(ValidationError):
            Coverage(**{**COVERAGE, 'pr_total_files': None})

    def test_inconsistent_values_rejected(self):
        for patch in ({'selected_files': 9}, {'selected_files': -1},
                      {'limitations': ['truncated', 'truncated']},
                      {'selected_content_coverage': 'none', 'analysis_coverage': 'partial'},
                      {'selected_content_coverage': 'partial', 'analysis_coverage': 'complete'}):
            with self.subTest(patch=patch), self.assertRaises(ValidationError):
                Coverage(**{**COVERAGE, **patch})


class ManifestRules(unittest.TestCase):
    def manifest(self, files):
        return {'revision_id': REV_ID, 'revision': revision(),
                'policy_version': 'code-transfer-v1', 'files': files}

    def test_valid_manifest_and_sides(self):
        m = InputManifest(**self.manifest([
            read_file('src/new_mod.py', change_type='added', new_blob_oid=SHA_A),
            read_file('src/old_mod.py', change_type='deleted', old_blob_oid=SHA_B),
            read_file('src/b.py', change_type='renamed', old_path='src/a.py'),
            {'path': 'img.png', 'change_type': 'modified', 'content_state': 'excluded',
             'exclusion_reason': 'binary'},
        ]))
        sides = {f.path: set(f.sides) for f in m.files}
        self.assertEqual(sides['src/new_mod.py'], {Side.RIGHT})
        self.assertEqual(sides['src/old_mod.py'], {Side.LEFT})
        self.assertEqual(sides['src/b.py'], {Side.LEFT, Side.RIGHT})

    def test_file_state_rules(self):
        bad_files = [
            read_file(change_type='added', old_blob_oid=SHA_A),
            read_file(change_type='deleted', new_blob_oid=SHA_A),
            read_file(change_type='renamed'),
            read_file(change_type='renamed', old_path='a.py'),
            read_file(old_path='other.py'),
            {k: v for k, v in read_file().items() if k != 'content_hash'},
            read_file(exclusion_reason='binary'),
            {'path': 'a.py', 'change_type': 'modified', 'content_state': 'failed'},
            {'path': 'a.py', 'change_type': 'modified', 'content_state': 'excluded',
             'exclusion_reason': 'binary', 'content_hash': H64},
            read_file('../x.py'),
        ]
        for f in bad_files:
            with self.subTest(file=f), self.assertRaises(ValidationError):
                ManifestFile(**f)

    def test_duplicate_paths_rejected(self):
        with self.assertRaises(ValidationError):
            InputManifest(**self.manifest([read_file('a.py'), read_file('a.py')]))

    def test_canonical_hash_independent_of_field_order(self):
        files = [read_file('a.py'), read_file('b.py')]
        m1 = InputManifest(**self.manifest(files))
        data = self.manifest(files)
        reordered = dict(reversed(list(data.items())))
        reordered['files'] = [dict(reversed(list(f.items()))) for f in files]
        self.assertEqual(canonical_hash(m1), canonical_hash(InputManifest(**reordered)))
        # 파일 순서는 의미가 있으므로 해시가 달라져야 한다.
        m3 = InputManifest(**self.manifest(list(reversed(files))))
        self.assertNotEqual(canonical_hash(m1), canonical_hash(m3))
        m4 = InputManifest(**self.manifest([read_file('a.py', content_hash='e' * 64), read_file('b.py')]))
        self.assertNotEqual(canonical_hash(m1), canonical_hash(m4))

    def test_canonical_hash_of_dict_matches_model(self):
        m = InputManifest(**self.manifest([read_file('a.py')]))
        self.assertEqual(canonical_hash(m), canonical_hash(m.model_dump(mode='json')))


class RequestRules(unittest.TestCase):
    base = {'repository_id': 14, 'pr_number': 27, 'expected_base_sha': SHA_A,
            'expected_head_sha': SHA_B, 'selected_paths': ['app/services/session.py']}

    def test_valid(self):
        PrepareInputRequest(**self.base)

    def test_zero_selection_and_bad_paths_rejected(self):
        for patch in ({'selected_paths': []}, {'selected_paths': ['a.py', 'a.py']},
                      {'selected_paths': ['/etc/passwd']}, {'selected_paths': ['a/../b']},
                      {'selected_paths': ['a\\b']}, {'expected_head_sha': 'xyz'},
                      {'owner_user_id': 'x'}):
            with self.subTest(patch=patch), self.assertRaises(ValidationError):
                PrepareInputRequest(**{**self.base, **patch})


class JobStateRules(unittest.TestCase):
    def state(self, **kw):
        return {'job_id': JOB_ID, 'kind': 'review', 'status': 'running', 'stage': 'provider_call',
                'coverage': COVERAGE, **kw}

    def test_design_example_and_status_independent_of_coverage(self):
        s = JobState(**self.state())
        self.assertEqual(s.coverage.analysis_coverage.value, 'none')
        JobState(**self.state(status='succeeded', stage=None, result_id=JOB_ID,
                              coverage={**COVERAGE, 'analysis_coverage': 'partial'}))

    def test_inconsistent_states_rejected(self):
        for patch in ({'status': 'succeeded', 'stage': None}, {'result_id': JOB_ID},
                      {'status': 'failed', 'stage': None},
                      {'status': 'succeeded', 'stage': None, 'result_id': JOB_ID, 'error_code': 'X'},
                      {'status': 'cancelled', 'stage': 'queued'}, {'retry_after_seconds': -1},
                      {'status': 'done'}):
            with self.subTest(patch=patch), self.assertRaises(ValidationError):
                JobState(**self.state(**patch))
        JobState(**self.state(status='failed', stage=None, error_code='UPSTREAM_UNAVAILABLE'))


class FindingFixtures(unittest.TestCase):
    def test_valid_findings(self):
        for raw in support.load_fixture('valid_findings.json'):
            f = Finding(**raw)
            self.assertEqual(f.human_validation.value, 'unverified')  # 자동으로 valid가 되지 않음

    def test_invalid_findings_rejected(self):
        valid = support.load_fixture('valid_findings.json')[0]
        for case in support.load_fixture('invalid_findings.json'):
            raw = copy.deepcopy(valid)
            for key, value in case['patch'].items():
                if isinstance(value, dict):
                    raw[key].update(value)
                elif value is None:
                    raw.pop(key)
                else:
                    raw[key] = value
            with self.subTest(case=case['case']), self.assertRaises(ValidationError):
                Finding(**raw)


class ErrorEnvelopeRules(unittest.TestCase):
    def test_envelope_shape(self):
        env = ErrorEnvelope(error={'code': 'PR_CHANGED', 'message': 'PR이 변경됐습니다.',
                                   'retryable': True, 'request_id': 'req-1', 'details': {}})
        self.assertEqual(env.model_dump(mode='json')['error']['code'], 'PR_CHANGED')

    def test_unknown_code_and_extra_rejected(self):
        body = {'code': 'PR_CHANGED', 'message': 'm', 'retryable': True, 'request_id': 'r'}
        with self.assertRaises(ValidationError):
            ErrorEnvelope(error={**body, 'code': 'SOMETHING'})
        with self.assertRaises(ValidationError):
            ErrorEnvelope(error={**body, 'provider_raw': 'x'})

    def test_every_code_has_http_status(self):
        self.assertEqual(set(ERROR_HTTP_STATUS), set(ErrorCode))
        self.assertEqual(ERROR_HTTP_STATUS[ErrorCode.IDEMPOTENCY_CONFLICT], 409)
        self.assertEqual(ERROR_HTTP_STATUS[ErrorCode.INPUT_EXPIRED], 410)


class LineMapFixtures(unittest.TestCase):
    def test_diff_matches_expected_line_maps(self):
        parsed = support.parse_line_maps(support.load_fixture('sample.diff'))
        self.assertEqual(parsed, support.load_fixture('line_maps.json'))

    def test_added_has_only_right_and_deleted_only_left(self):
        parsed = support.parse_line_maps(support.load_fixture('sample.diff'))
        self.assertEqual(parsed['src/new_mod.py']['left'], [])
        self.assertEqual(parsed['src/old_mod.py']['right'], [])


class TestDatabaseGuard(unittest.TestCase):
    def test_accepts_only_gitviz_test_database(self):
        env = {'GITVIZ_TEST_DATABASE_URL': 'postgresql://u:p@localhost:5432/gitviz_test_ai'}
        self.assertEqual(support.require_test_database(env), env['GITVIZ_TEST_DATABASE_URL'])
        self.assertEqual(env['DATABASE_URL_ASYNC'], 'postgresql+asyncpg://u:p@localhost:5432/gitviz_test_ai')
        for url in (None, 'postgresql://u:p@localhost/gitviz', 'postgresql://u:p@localhost/prod'):
            with self.subTest(url=url), self.assertRaises(RuntimeError):
                support.require_test_database({} if url is None else {'GITVIZ_TEST_DATABASE_URL': url})

    def test_does_not_leak_url_in_error(self):
        with self.assertRaises(RuntimeError) as ctx:
            support.require_test_database({'GITVIZ_TEST_DATABASE_URL': 'postgresql://u:secretpw@h/prod'})
        self.assertNotIn('secretpw', str(ctx.exception))


class SchemaIsDependencyLight(unittest.TestCase):
    def test_schema_import_does_not_load_app_or_db(self):
        code = ("import sys; sys.path.insert(0, %r); import app.schemas.ai; "
                "sys.exit(1 if 'app.core.database' in sys.modules or 'app.main' in sys.modules else 0)"
                % str(support.ROOT))
        self.assertEqual(subprocess.run([sys.executable, '-c', code]).returncode, 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
