"""S3 PR·고정 코드 수집 테스트. DB·네트워크 없이 실행된다(고정 local Git fixture + 가짜 GitHub client).

fixture 저장소는 테스트 시작 시 plumbing 명령으로 만든다. 사용자의 git 설정(autocrlf 등)과 무관하게
같은 결과가 나오도록 전역 설정을 끄고, 작성자·날짜를 고정한다.
"""
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ai_test_support as support  # noqa: E402
sys.path.insert(0, str(support.ROOT))
os.environ['SESSION_SECRET_KEY'] = 'ai-collection-test-secret'
os.environ['GIT_CONFIG_NOSYSTEM'] = '1'
os.environ['GIT_CONFIG_GLOBAL'] = os.devnull

from app.collectors.ai_errors import (InputTooLarge, InvalidSelection, MergeBaseUnavailable, PRChanged,  # noqa: E402
                                      RateLimited, ReauthRequired, ResourceNotFound, UpstreamUnavailable)
from app.collectors.git import ai_diff_collector as diffc  # noqa: E402
from app.collectors.github import ai_pr_collector as prc  # noqa: E402
from app.collectors.github.github_client import GitHubClient, GitHubRateLimited  # noqa: E402
from app.schemas.ai import (ChangeType, ContentCoverage, ContentState, InputManifest, InventoryCoverage,  # noqa: E402
                            Limitation, canonical_hash)
from app.services.ai import content_policy  # noqa: E402

A = 'a' * 40
B = 'b' * 40
C = 'c' * 40
D = 'd' * 40


# ── local Git fixture (tests/server/ai_git_fixture.py) ───────────────────────

from ai_git_fixture import Fixture  # noqa: E402


ALL_PATHS = ['src/new_mod.py', 'src/old_mod.py', 'src/b.py', 'main.py', 'weird.py', 'app/config.py', '.env',
             'package-lock.json', 'gen/api.py', 'latin.py', 'big.py', 'notes.xyz', 'link.py', 'sub', 'a[1].py', 'a1.py', 'star*.py', 'star1.py']
EXPECTED = {
    'src/new_mod.py': ('read', None), 'src/old_mod.py': ('read', None), 'src/b.py': ('read', None),
    'main.py': ('read', None), 'weird.py': ('excluded', 'binary'), 'app/config.py': ('excluded', 'secret_suspected'),
    '.env': ('excluded', 'secret_suspected'), 'package-lock.json': ('excluded', 'generated_file'),
    'gen/api.py': ('excluded', 'generated_file'), 'latin.py': ('excluded', 'binary'),
    'big.py': ('excluded', 'too_large'), 'notes.xyz': ('excluded', 'unsupported_language'),
    'link.py': ('excluded', 'link_or_submodule'), 'sub': ('excluded', 'link_or_submodule'),
    'a[1].py': ('read', None), 'a1.py': ('read', None), 'star*.py': ('read', None), 'star1.py': ('read', None),
}
PR_FILES = 18            # GitHub가 세는 PR 변경 파일 수(이름 변경은 1개)


class GitCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix='gitviz-ai-test-')
        cls.fx = Fixture(cls.tmp)

    @classmethod
    def tearDownClass(cls):
        diffc._remove_tree(cls.tmp)

    def collect(self, paths=None, head=None, **kw):
        return diffc.collect_selected_code(self.fx.path, self.fx.c0, head or self.fx.f1, paths or ALL_PATHS,
                                           protocols='file', **kw)


class DiffCollection(GitCase):
    def test_changed_files_are_computed_from_merge_base_not_base_tip(self):
        changed = {c.path: c for c in diffc.list_changed_files(self.fx.path, self.fx.c0, self.fx.f1)}
        self.assertEqual(set(changed), set(ALL_PATHS) | {'src/a.py'})   # 내용이 바뀐 이름 변경은 목록에서 삭제+추가
        self.assertNotIn('other.py', changed)         # main이 이후에 추가한 파일은 PR 변경이 아니다
        # base tip과 직접 비교하면 other.py가 삭제로 보이는 잘못된 목록이 된다.
        wrong = {c.path for c in diffc.list_changed_files(self.fx.path, self.fx.m1, self.fx.f1)}
        self.assertIn('other.py', wrong)
        self.assertEqual(changed['src/b.py'].change_type, ChangeType.added)
        self.assertEqual(changed['src/a.py'].change_type, ChangeType.deleted)
        self.assertEqual(changed['src/new_mod.py'].change_type, ChangeType.added)
        self.assertIsNone(changed['src/new_mod.py'].old_oid)
        self.assertEqual(changed['src/old_mod.py'].change_type, ChangeType.deleted)
        self.assertIsNone(changed['src/old_mod.py'].new_oid)
        self.assertEqual(changed['link.py'].new_mode, '120000')
        self.assertEqual(changed['sub'].new_mode, '160000')

    def test_every_selected_file_has_state_and_reason(self):
        result = self.collect()
        self.assertEqual([f.manifest.path for f in result.files], ALL_PATHS)
        for f in result.files:
            state, reason = EXPECTED[f.manifest.path]
            with self.subTest(path=f.manifest.path):
                self.assertEqual(f.manifest.content_state.value, state)
                self.assertEqual(f.manifest.exclusion_reason.value if f.manifest.exclusion_reason else None, reason)
                self.assertEqual(f.payload is not None, state == 'read')
        self.assertEqual(set(result.exclusions), {p for p, (s, _) in EXPECTED.items() if s != 'read'})

    def test_coverage_separates_inventory_selection_and_analysis(self):
        full = self.collect(pr_total_files=PR_FILES)
        self.assertEqual(full.coverage.inventory_coverage, InventoryCoverage.complete)
        self.assertEqual(full.coverage.selected_content_coverage, ContentCoverage.partial)
        self.assertEqual(full.coverage.analysis_coverage, ContentCoverage.none)
        self.assertEqual((full.coverage.selected_files, full.coverage.pr_total_files), (18, 18))
        self.assertNotIn(Limitation.not_selected, full.coverage.limitations)
        self.assertTrue({Limitation.binary, Limitation.secret_suspected, Limitation.too_large,
                         Limitation.generated_file, Limitation.link_or_submodule,
                         Limitation.unsupported_language} <= set(full.coverage.limitations))

        derived = self.collect(['main.py'])           # 총수를 주지 않으면 목록 항목 수(이름 변경이 2개로 셈)
        self.assertEqual(derived.coverage.pr_total_files, 19)
        some = self.collect(['main.py', 'src/b.py'], pr_total_files=PR_FILES)
        self.assertEqual(some.coverage.selected_content_coverage, ContentCoverage.complete)
        self.assertEqual((some.coverage.selected_files, some.coverage.pr_total_files), (2, 18))
        self.assertEqual(some.coverage.limitations, [Limitation.not_selected])

        none = self.collect(['weird.py', '.env'])
        self.assertEqual(none.coverage.selected_content_coverage, ContentCoverage.none)

    def test_manifest_is_valid_and_hashes_match_payload(self):
        result = self.collect()
        manifest = InputManifest(revision_id=UUID(int=1), policy_version=result.policy_version,
                                 revision={'repo_id': 1, 'pr_number': 7, 'base_sha': self.fx.m1,
                                           'head_sha': self.fx.f1, 'merge_base_sha': self.fx.c0},
                                 files=[f.manifest for f in result.files])
        self.assertEqual(len(manifest.files), 18)
        for f in result.files:
            if f.payload is not None:
                self.assertEqual(f.manifest.content_hash, hashlib.sha256(f.payload).hexdigest())
                self.assertEqual(f.byte_count, len(f.payload))
        self.assertEqual(result.total_bytes, sum(f.byte_count for f in result.files))

    def test_blob_oids_and_sides(self):
        by = {f.manifest.path: f for f in self.collect().files}
        added, deleted, renamed = by['src/new_mod.py'], by['src/old_mod.py'], by['src/b.py']
        self.assertEqual((added.side_availability, deleted.side_availability, renamed.side_availability),
                         ('RIGHT', 'LEFT', 'BOTH'))
        self.assertIsNone(added.manifest.old_blob_oid)
        self.assertEqual(added.manifest.new_blob_oid, self.fx.git('rev-parse', f'{self.fx.f1}:src/new_mod.py'))
        self.assertEqual(deleted.manifest.old_blob_oid, self.fx.git('rev-parse', f'{self.fx.c0}:src/old_mod.py'))
        self.assertEqual(renamed.manifest.old_blob_oid, self.fx.git('rev-parse', f'{self.fx.c0}:src/a.py'))
        self.assertEqual(renamed.manifest.old_path, 'src/a.py')

    def test_line_maps_point_at_real_lines_of_the_commits(self):
        by = {f.manifest.path: f for f in self.collect().files}
        # 이름 변경 + 수정: 맥락 줄의 좌우 대응이 실제 blob 내용과 일치해야 한다.
        old = self.fx.blob_lines(self.fx.c0, 'src/a.py')
        new = self.fx.blob_lines(self.fx.f1, 'src/b.py')
        lm = by['src/b.py'].line_map
        self.assertTrue(lm['right_to_left'])
        for right, left in lm['right_to_left'].items():
            self.assertEqual(new[int(right) - 1], old[left - 1])
        changed_right = [n for n in lm['right'] if str(n) not in lm['right_to_left']]
        self.assertEqual([new[n - 1] for n in changed_right], ['line two', 'line 10b'])
        removed_left = [n for n in lm['left'] if n not in lm['right_to_left'].values()]
        self.assertEqual([old[n - 1] for n in removed_left], ['line 2'])
        hunk = by['src/b.py'].manifest.hunks[0]
        self.assertEqual((hunk.old_lines, hunk.new_lines), (20, 21))
        # 새 파일은 RIGHT 줄만, 삭제 파일은 LEFT 줄만
        self.assertEqual(by['src/new_mod.py'].line_map['left'], [])
        self.assertEqual(by['src/new_mod.py'].line_map['right'], [1, 2, 3])
        self.assertEqual(by['src/old_mod.py'].line_map['right'], [])
        self.assertEqual(by['src/old_mod.py'].line_map['left'], [1, 2])

    def test_s0_fixture_line_maps_are_reproduced_by_the_production_parser(self):
        expected = support.load_fixture('line_maps.json')
        chunks = support.load_fixture('sample.diff').split('diff --git ')[1:]
        for chunk in chunks:
            path = chunk.split('\n', 1)[0].split(' b/')[1].strip()
            hunks, line_map = diffc.parse_file_diff(chunk)
            with self.subTest(path=path):
                want = expected[path]
                self.assertEqual({k: line_map[k] for k in ('left', 'right', 'right_to_left')},
                                 {k: want[k] for k in ('left', 'right', 'right_to_left')})

    def test_output_is_deterministic_and_pinned_to_the_given_head(self):
        first = self.collect()
        second = self.collect()
        self.assertEqual([f.manifest.content_hash for f in first.files], [f.manifest.content_hash for f in second.files])
        # feature가 이후 커밋으로 움직여도 고정한 f1 기준 결과는 같다.
        self.assertNotEqual(self.fx.f1, self.fx.f2)
        pinned = self.collect(['main.py'], head=self.fx.f1).files[0].manifest.content_hash
        self.assertEqual(self.collect(['main.py']).files[0].manifest.content_hash, pinned)
        moved = self.collect(['main.py'], head=self.fx.f2).files[0].manifest.content_hash
        self.assertNotEqual(moved, pinned)          # 다른 head를 주면 다른 입력이다

    def test_lines_containing_unicode_separators_keep_git_line_numbers(self):
        """str.splitlines()는 form feed·U+2028·NEL에서도 줄을 나눠 줄 번호가 어긋난다. git의 줄 구분만 따라야 한다."""
        content = 'a = 1\n\x0c\nb = "x\u2028y"\nc = 3\n'.encode('utf-8')
        head = self.fx.commit(self.fx.f1, {'sep.py': content}, 'separators')
        result = diffc.collect_selected_code(self.fx.path, self.fx.f1, head, ['sep.py'], protocols='file')
        item = result.files[0]
        self.assertEqual(item.manifest.content_state, ContentState.read)
        self.assertEqual(item.line_map['right'], [1, 2, 3, 4])                       # 4줄(5줄로 어긋나지 않는다)
        self.assertEqual(item.manifest.hunks[0].new_lines, 4)
        from app.services.ai.difftext import diff_lines
        lines = diff_lines(item.payload.decode('utf-8'))
        self.assertEqual([l.right_line for l in lines], [1, 2, 3, 4])
        self.assertEqual(lines[2].text, 'b = "x\u2028y"')

    def test_selection_validation_and_limits(self):
        with self.assertRaises(InvalidSelection):
            self.collect(['not/in/pr.py'])
        with self.assertRaises(InvalidSelection):
            self.collect(['other.py'])                 # main에만 있는 파일은 PR 목록에 없다
        with self.assertRaises(InvalidSelection):
            diffc.collect_selected_code(self.fx.path, self.fx.c0, self.fx.f1, [], protocols='file')
        with self.assertRaises(InvalidSelection):
            self.collect(['main.py', 'main.py'])
        with self.assertRaises(InputTooLarge):
            self.collect(max_files=3)
        with self.assertRaises(InputTooLarge):
            self.collect(['src/b.py', 'main.py'], max_bytes=100)

    def test_selected_paths_are_literal_not_pathspec_patterns(self):
        for chosen, wanted, unwanted in (('a[1].py', 'bracket', 'plain'), ('star*.py', 'star_literal', 'star_other')):
            with self.subTest(path=chosen):
                result = self.collect([chosen])
                self.assertEqual([f.manifest.path for f in result.files], [chosen])
                payload = result.files[0].payload.decode()
                self.assertIn(wanted, payload)
                self.assertNotIn(unwanted, payload)          # 다른 파일의 코드가 전송 범위에 섞이지 않는다
                self.assertEqual(payload.count('diff --git'), 1)
                self.assertEqual(result.files[0].line_map['right'], [1])
                self.assertEqual(result.total_bytes, len(result.files[0].payload))

    def test_diff_that_contains_another_file_is_rejected(self):
        two_files = self.fx.git('diff', '--no-color', self.fx.c0, self.fx.f1, '--', 'a1.py', 'a[1].py').encode()
        self.assertEqual(two_files.count(b'diff --git'), 2)
        with patch.object(diffc, '_file_diff', return_value=two_files):
            result = self.collect(['a1.py'])
        item = result.files[0]
        self.assertIsNone(item.payload)
        self.assertEqual((item.manifest.content_state, item.manifest.exclusion_reason),
                         (ContentState.failed, Limitation.fetch_failed))
        with patch.object(diffc, '_file_diff', return_value=b''):
            self.assertIsNone(self.collect(['a1.py']).files[0].payload)

    def test_total_file_count_can_be_supplied_from_github(self):
        self.assertEqual(self.collect(['main.py'], pr_total_files=18).coverage.pr_total_files, 18)

    def test_only_selected_files_blobs_are_downloaded(self):
        def missing(work):
            out = subprocess.run(['git', '--git-dir', str(work), 'rev-list', '--objects', '--missing=print',
                                  self.fx.c0, self.fx.f1],
                                 capture_output=True, check=True).stdout.decode().split('\n')
            return {line[1:].split(' ')[0] for line in out if line.startswith('?')}
        big_oid = self.fx.git('rev-parse', f'{self.fx.f1}:big.py')
        main_oids = {self.fx.git('rev-parse', f'{self.fx.c0}:main.py'), self.fx.git('rev-parse', f'{self.fx.f1}:main.py')}
        with diffc.revision_workspace(self.fx.path.as_uri(), self.fx.c0, self.fx.f1, protocols='file') as work:
            before = missing(work)
            self.assertIn(big_oid, before)
            diffc.list_changed_files(work, self.fx.c0, self.fx.f1, env=diffc._env('file'))
            self.assertEqual(missing(work), before)                      # 전체 목록 조회는 blob을 받지 않는다
            diffc.collect_selected_code(work, self.fx.c0, self.fx.f1, ['.env', 'package-lock.json', 'link.py', 'sub',
                                                                         'notes.xyz'], protocols='file')
            self.assertEqual(missing(work), before)                      # 경로·모드로 제외되는 파일은 내용을 읽지 않는다
            diffc.collect_selected_code(work, self.fx.c0, self.fx.f1, ['main.py'], protocols='file')
            after_main = missing(work)
            self.assertEqual(before - after_main, main_oids)             # 선택한 파일의 두 blob만 받았다
            self.assertIn(big_oid, after_main)
            diffc.collect_selected_code(work, self.fx.c0, self.fx.f1, ['src/b.py'], protocols='file')
            after_rename = missing(work)
            self.assertLessEqual(len(after_main - after_rename), 4)      # 새 파일 + 이름 변경 후보 몇 개뿐
            self.assertIn(big_oid, after_rename)

    def test_rename_pairing_is_skipped_when_there_are_too_many_candidates(self):
        with patch.object(diffc, 'MAX_RENAME_CANDIDATES', 1):
            result = self.collect(['src/b.py'])
        item = result.files[0]
        self.assertEqual(item.manifest.change_type, ChangeType.added)    # 짝을 못 찾으면 추가 파일로 다룬다
        self.assertIsNone(item.manifest.old_path)
        self.assertEqual(self.collect(['src/b.py']).files[0].manifest.change_type, ChangeType.renamed)

    def test_missing_commit_is_not_returned_as_partial_result(self):
        with self.assertRaises(MergeBaseUnavailable):
            diffc.collect_selected_code(self.fx.path, D, self.fx.f1, ['main.py'], protocols='file')
        with self.assertRaises(MergeBaseUnavailable):
            diffc.collect_selected_code(self.fx.path, self.fx.c0, D, ['main.py'], protocols='file')

    def test_acquisition_over_shallow_blobless_fetch_matches_local_result(self):
        remote = self.fx.path.as_uri()
        with diffc.revision_workspace(remote, self.fx.c0, self.fx.f1, protocols='file') as work:
            self.assertFalse({'main.py', 'src', 'README.md'} & set(os.listdir(work)))   # checkout 없음(bare)
            remote_result = diffc.collect_selected_code(work, self.fx.c0, self.fx.f1, ALL_PATHS, protocols='file')
        self.assertFalse(Path(work).exists())      # 작업 공간과 원문은 삭제된다
        local = self.collect()
        self.assertEqual([(f.manifest.path, f.manifest.content_hash) for f in remote_result.files],
                         [(f.manifest.path, f.manifest.content_hash) for f in local.files])
        self.assertEqual(remote_result.coverage, local.coverage)

    def test_acquisition_failures(self):
        remote = self.fx.path.as_uri()
        with self.assertRaises(UpstreamUnavailable):          # 허용하지 않은 프로토콜
            with diffc.revision_workspace(remote, self.fx.c0, self.fx.f1, protocols='https'):
                pass
        with self.assertRaises(UpstreamUnavailable):          # 없는 commit
            with diffc.revision_workspace(remote, self.fx.c0, D, protocols='file'):
                pass
        with self.assertRaises(UpstreamUnavailable):          # 없는 remote
            with diffc.revision_workspace((Path(self.tmp) / 'missing.git').as_uri(), self.fx.c0, self.fx.f1,
                                          protocols='file'):
                pass

    def test_token_is_not_persisted_in_workspace_config(self):
        with diffc.revision_workspace(self.fx.path.as_uri(), self.fx.c0, self.fx.f1, token='tok-secret-123',
                                      protocols='file') as work:
            config = (Path(work) / 'config').read_text()
            self.assertNotIn('tok-secret-123', config)
            self.assertNotIn('extraheader', config)


class ContentPolicy(unittest.TestCase):
    def test_path_rules(self):
        cases = {
            'src/app.py': None, 'README.md': None, 'a/b/c.ts': None,
            'package-lock.json': Limitation.generated_file, 'web/app.min.js': Limitation.generated_file,
            'node_modules/x/index.js': Limitation.generated_file, 'api/x_pb2.py': Limitation.generated_file,
            '.env': Limitation.secret_suspected, '.env.production': Limitation.secret_suspected,
            'deploy/id_rsa': Limitation.secret_suspected, 'certs/server.pem': Limitation.secret_suspected,
            'notes.txt': Limitation.unsupported_language, 'image.png': Limitation.unsupported_language,
            'Makefile': Limitation.unsupported_language,
        }
        for path, expected in cases.items():
            with self.subTest(path=path):
                self.assertEqual(content_policy.path_exclusion(path), expected)
        self.assertEqual(content_policy.path_exclusion('new.py', old_path='.env'), Limitation.secret_suspected)

    def test_mode_rules(self):
        self.assertEqual(content_policy.mode_exclusion('100644', '120000'), Limitation.link_or_submodule)
        self.assertEqual(content_policy.mode_exclusion('160000', '160000'), Limitation.link_or_submodule)
        self.assertIsNone(content_policy.mode_exclusion('100644', '100755'))

    def test_secret_detection_covers_removed_and_context_lines(self):
        secrets = [
            '-----BEGIN RSA PRIVATE KEY-----', 'AKIAABCDEFGHIJKLMNOP', 'ghp_' + 'a' * 36,
            'github_pat_' + 'A' * 50, 'xoxb-1234567890-abcdef', 'sk-' + 'a' * 40,
            'password = "hunter2hunter2hunter2"', "api_key: 'abcdefghijklmnop'",
        ]
        for secret in secrets:
            for prefix in ('+', '-', ' '):
                text = f'@@ -1 +1 @@\n{prefix}{secret}\n'
                with self.subTest(secret=secret[:12], prefix=prefix):
                    self.assertEqual(content_policy.content_exclusion(text, byte_count=len(text)),
                                     Limitation.secret_suspected)
        for safe in ('password = get_password()', 'token = None', 'secret = "short"', 'AKIA_not_a_key'):
            text = f'@@ -1 +1 @@\n+{safe}\n'
            with self.subTest(safe=safe):
                self.assertIsNone(content_policy.content_exclusion(text, byte_count=len(text)))

    def test_size_and_generated_marker(self):
        self.assertEqual(content_policy.content_exclusion('x', byte_count=content_policy.MAX_FILE_BYTES + 1),
                         Limitation.too_large)
        self.assertIsNone(content_policy.content_exclusion('x', byte_count=content_policy.MAX_FILE_BYTES))
        self.assertEqual(content_policy.content_exclusion('@@ -0,0 +1 @@\n+// @generated\n', byte_count=30),
                         Limitation.generated_file)

    def test_language_detection(self):
        self.assertEqual(content_policy.language_for('a/B.PY'), 'python')
        self.assertEqual(content_policy.language_for('x.tsx'), 'typescript')
        self.assertIsNone(content_policy.language_for('x'))


# ── GitHub API (가짜 client) ────────────────────────────────────────────

REPO = prc.RepoRef(repository_id=14, github_id=555, owner='acme', name='widgets')


def pr_json(number=7, state='open', base=A, head=B, merged=False, head_repo='acme/widgets', draft=False, **kw):
    data = {'number': number, 'id': 9000 + number, 'title': f'PR {number}', 'state': state, 'draft': draft,
            'merged': merged, 'merged_at': '2026-01-01T00:00:00Z' if merged else None, 'body': 'desc',
            'user': {'login': 'dev'}, 'updated_at': '2026-01-02T00:00:00Z',
            'base': {'ref': 'main', 'sha': base, 'repo': {'full_name': 'acme/widgets'}},
            'head': {'ref': 'feature', 'sha': head, 'repo': {'full_name': head_repo} if head_repo else None},
            'changed_files': 3, 'commits': 2, 'additions': 10, 'deletions': 4}
    data.update(kw)
    return data


class FakeClient:
    BASE_URL = 'https://api.github.com'

    def __init__(self, routes):
        self.routes = {k: list(v) if isinstance(v, list) else [v] for k, v in routes.items()}
        self.calls = []

    def get(self, url, params=None):
        self.calls.append((url, dict(params or {})))
        path = url.replace(self.BASE_URL, '')
        queue = self.routes[path]
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, Exception):
            raise item
        data, headers = item if isinstance(item, tuple) else (item, {})
        return SimpleNamespace(json=lambda: data, headers=headers, status_code=200)


def http_error(status):
    response = requests.Response()
    response.status_code = status
    return requests.HTTPError(response=response)


PULLS = '/repos/acme/widgets/pulls'


class ListPullRequests(unittest.TestCase):
    def test_state_filters(self):
        mixed = [pr_json(1, 'open'), pr_json(2, 'closed'), pr_json(3, 'closed', merged=True, ),
                 pr_json(4, 'open', draft=True)]
        for state, numbers, api_state in (('open', [1, 4], 'open'), ('closed', [2], 'closed'),
                                          ('merged', [3], 'closed')):
            client = FakeClient({PULLS: [mixed]})
            page = prc.list_ai_prs(client, REPO, state)
            with self.subTest(state=state):
                self.assertEqual([i['number'] for i in page.items], numbers)
                self.assertEqual(client.calls[0][1]['state'], api_state)
        open_items = prc.list_ai_prs(FakeClient({PULLS: [mixed]}), REPO, 'open').items
        self.assertTrue(open_items[1]['draft'])
        self.assertEqual((open_items[0]['base_sha'], open_items[0]['head_sha']), (A, B))
        self.assertEqual(prc.list_ai_prs(FakeClient({PULLS: [mixed]}), REPO, 'merged').items[0]['state'], 'merged')

    def test_fork_and_deleted_fork_are_flagged_without_following_their_urls(self):
        items = [pr_json(1, head_repo='stranger/widgets'), pr_json(2, head_repo=None), pr_json(3)]
        client = FakeClient({PULLS: [items]})
        got = {i['number']: i for i in prc.list_ai_prs(client, REPO, 'open').items}
        self.assertTrue(got[1]['is_fork'])
        self.assertFalse(got[1]['head_repo_deleted'])
        self.assertTrue(got[2]['head_repo_deleted'])
        self.assertFalse(got[2]['is_fork'])
        self.assertFalse(got[3]['is_fork'])
        self.assertTrue(all(url.startswith('https://api.github.com/repos/acme/widgets/') for url, _ in client.calls))

    def test_pagination_cursor_is_signed_and_bound(self):
        link = {'Link': '<https://api.github.com/x?page=2>; rel="next"'}
        client = FakeClient({PULLS: [([pr_json(1)], link), ([pr_json(2)], {})]})
        first = prc.list_ai_prs(client, REPO, 'open', limit=10)
        self.assertIsNotNone(first.next_cursor)
        second = prc.list_ai_prs(client, REPO, 'open', cursor=first.next_cursor, limit=10)
        self.assertIsNone(second.next_cursor)
        self.assertEqual([c[1]['page'] for c in client.calls], [1, 2])
        self.assertEqual(client.calls[1][1]['per_page'], 10)
        bad = [first.next_cursor + 'x', first.next_cursor[:-3], 'garbage', '', 'a.b',
               first.next_cursor.split('.')[0] + '.' + 'A' * 22]
        for cursor in bad:
            with self.subTest(cursor=cursor[:12]), self.assertRaises(prc.InvalidCursor):
                prc.list_ai_prs(FakeClient({PULLS: [[]]}), REPO, 'open', cursor=cursor or 'x', limit=10)
        for kw in ({'state': 'closed', 'limit': 10}, {'state': 'open', 'limit': 11}):   # 다른 조건에 재사용 불가
            with self.subTest(kw=kw), self.assertRaises(prc.InvalidCursor):
                prc.list_ai_prs(FakeClient({PULLS: [[]]}), REPO, kw['state'], cursor=first.next_cursor, limit=kw['limit'])
        other_repo = prc.RepoRef(15, 556, 'acme', 'widgets')
        with self.assertRaises(prc.InvalidCursor):
            prc.list_ai_prs(FakeClient({PULLS: [[]]}), other_repo, 'open', cursor=first.next_cursor, limit=10)

    def test_argument_limits(self):
        for kw in ({'state': 'all'}, {'state': 'open', 'limit': 0}, {'state': 'open', 'limit': 51},
                   {'state': 'open', 'limit': True}, {'state': 'open', 'limit': '5'}):
            with self.subTest(kw=kw), self.assertRaises(InvalidSelection):
                prc.list_ai_prs(FakeClient({PULLS: [[]]}), REPO, **kw)
        for owner, name in (('a/b', 'c'), ('..', 'x'), ('a', 'b?x=1'), ('a', '')):
            with self.assertRaises(ValueError):
                prc.RepoRef(1, 1, owner, name)

    def test_malformed_responses(self):
        for payload in ({'not': 'a list'}, [pr_json(1, base='nope')], [{'number': 1}]):
            with self.subTest(payload=str(payload)[:30]), self.assertRaises(UpstreamUnavailable):
                prc.list_ai_prs(FakeClient({PULLS: [payload]}), REPO, 'open')


class PRFiles(unittest.TestCase):
    FILES = f'{PULLS}/7/files'

    def page(self, pr=None, files=None, headers=None):
        files = files if files is not None else [
            {'filename': 'a.py', 'status': 'added', 'additions': 3, 'deletions': 0, 'sha': A, 'patch': '@@'},
            {'filename': 'b.py', 'status': 'renamed', 'previous_filename': 'old.py', 'sha': B, 'patch': '@@'},
            {'filename': 'c.bin', 'status': 'removed', 'sha': C},
            {'filename': 'd.py', 'status': 'changed', 'sha': D}]
        return FakeClient({f'{PULLS}/7': pr or pr_json(), self.FILES: (files, headers or {})})

    def test_page_reports_shas_total_and_quality(self):
        page = prc.list_pr_files(self.page(), REPO, 7)
        self.assertEqual((page.base_sha, page.head_sha, page.pr_total_files), (A, B, 3))
        self.assertEqual(page.inventory_coverage, InventoryCoverage.complete)
        self.assertEqual([f['change_type'] for f in page.files], ['added', 'renamed', 'deleted', 'modified'])
        self.assertEqual(page.files[1]['old_path'], 'old.py')
        self.assertEqual([f['has_patch'] for f in page.files], [True, True, False, False])   # patch 누락 표시

    def test_inventory_quality_levels(self):
        cases = ((2999, InventoryCoverage.complete), (3000, InventoryCoverage.complete),
                 (3001, InventoryCoverage.partial), (None, InventoryCoverage.unknown),
                 ('3', InventoryCoverage.unknown), (True, InventoryCoverage.unknown))
        for total, expected in cases:
            with self.subTest(total=total):
                page = prc.list_pr_files(self.page(pr_json(changed_files=total)), REPO, 7)
                self.assertEqual(page.inventory_coverage, expected)
                self.assertEqual(page.pr_total_files, total if isinstance(total, int) and total is not True else None)

    def test_pagination_stops_at_github_cap_and_cursor_is_bound(self):
        link = {'Link': '<x>; rel="next"'}
        page = prc.list_pr_files(self.page(headers=link), REPO, 7, limit=100)
        self.assertIsNotNone(page.next_cursor)
        client = self.page(headers=link)
        prc.list_pr_files(client, REPO, 7, cursor=page.next_cursor, limit=100)
        self.assertEqual(client.calls[-1][1]['page'], 2)
        capped = prc.encode_cursor('files', 14, 31, n=7, l=100)       # 3000번째 이후는 나열하지 않는다
        self.assertIsNone(prc.list_pr_files(self.page(headers=link), REPO, 7, cursor=capped, limit=100).next_cursor)
        with self.assertRaises(prc.InvalidCursor):                     # 다른 PR·다른 종류의 cursor
            prc.list_pr_files(self.page(), REPO, 8, cursor=page.next_cursor, limit=100)
        pr_cursor = prc.encode_cursor('pr', 14, 2, s='open', l=100)
        with self.assertRaises(prc.InvalidCursor):
            prc.list_pr_files(self.page(), REPO, 7, cursor=pr_cursor, limit=100)
        with self.assertRaises(InvalidSelection):
            prc.list_pr_files(self.page(), REPO, 7, limit=101)


class CaptureRevision(unittest.TestCase):
    PR = f'{PULLS}/7'
    CMP = f'/repos/acme/widgets/compare/{A}...{B}'

    def client(self, first=None, second=None, compare=None):
        first = first or pr_json()
        return FakeClient({self.PR: [first, second or first],
                           self.CMP: compare if compare is not None else {'merge_base_commit': {'sha': C}}})

    def test_success_pins_revision_and_fingerprint(self):
        client = self.client()
        got = prc.capture_revision(client, REPO, 7, A, B)
        self.assertEqual((got.base_sha, got.head_sha, got.merge_base_sha), (A, B, C))
        self.assertEqual(got.state_at_capture, 'open')
        self.assertEqual(got.github_pr_id, 9007)
        self.assertEqual(got.revision.repo_id, 14)
        self.assertEqual([c[0].split('/repos/acme/widgets/')[1] for c in client.calls],
                         ['pulls/7', f'compare/{A}...{B}', 'pulls/7'])          # 조회 → merge-base → 재조회
        self.assertEqual(got.revision_fingerprint, canonical_hash({
            'github_repo_id': 555, 'github_pr_id': 9007, 'base_sha': A, 'head_sha': B, 'merge_base_sha': C,
            'compare': 'merge-base-diff'}))
        again = prc.capture_revision(self.client(), REPO, 7, A, B)
        self.assertEqual(got.revision_fingerprint, again.revision_fingerprint)
        other = prc.capture_revision(self.client(compare={'merge_base_commit': {'sha': D}}), REPO, 7, A, B)
        self.assertNotEqual(got.revision_fingerprint, other.revision_fingerprint)

    def test_metadata_includes_description_and_truncates_long_body(self):
        got = prc.capture_revision(self.client(pr_json(body='x' * 9000)), REPO, 7, A, B)
        self.assertEqual(len(got.metadata['body']), prc.BODY_LIMIT)
        self.assertTrue(got.metadata['body_truncated'])
        short = prc.capture_revision(self.client(pr_json(body=None, head_repo=None, draft=True)), REPO, 7, A, B)
        self.assertEqual((short.metadata['body'], short.metadata['body_truncated']), ('', False))
        self.assertTrue(short.metadata['head_repo_deleted'])
        self.assertTrue(short.metadata['draft'])
        merged = prc.capture_revision(self.client(pr_json(state='closed', merged=True)), REPO, 7, A, B)
        self.assertEqual(merged.state_at_capture, 'merged')

    def test_expected_sha_mismatch_does_not_query_compare(self):
        for expected in ((C, B), (A, C)):
            client = self.client()
            with self.subTest(expected=expected), self.assertRaises(PRChanged):
                prc.capture_revision(client, REPO, 7, *expected)
            self.assertEqual(len(client.calls), 1)

    def test_change_during_capture_is_detected(self):
        with self.assertRaises(PRChanged):       # head가 취득 중 바뀜
            prc.capture_revision(self.client(second=pr_json(head=D)), REPO, 7, A, B)
        with self.assertRaises(PRChanged):       # base가 바뀜
            prc.capture_revision(self.client(second=pr_json(base=D)), REPO, 7, A, B)
        with self.assertRaises(PRChanged):       # 상태가 바뀜(병합)
            prc.capture_revision(self.client(second=pr_json(state='closed', merged=True)), REPO, 7, A, B)

    def test_merge_base_unavailable(self):
        for compare in ({}, {'merge_base_commit': None}, {'merge_base_commit': {'sha': 'bad'}},
                        {'merge_base_commit': {}}):
            with self.subTest(compare=compare), self.assertRaises(MergeBaseUnavailable):
                prc.capture_revision(self.client(compare=compare), REPO, 7, A, B)
        client = FakeClient({self.PR: pr_json(), self.CMP: http_error(404)})
        with self.assertRaises(MergeBaseUnavailable):
            prc.capture_revision(client, REPO, 7, A, B)

    def test_http_failures_are_classified(self):
        cases = ((401, ReauthRequired), (403, ResourceNotFound), (404, ResourceNotFound),
                 (500, UpstreamUnavailable), (502, UpstreamUnavailable))
        for status, expected in cases:
            with self.subTest(status=status), self.assertRaises(expected):
                prc.capture_revision(FakeClient({self.PR: http_error(status)}), REPO, 7, A, B)
        with self.assertRaises(PRChanged):
            prc.capture_revision(FakeClient({self.PR: pr_json(), self.CMP: http_error(422)}), REPO, 7, A, B)
        for exc in (requests.ConnectionError(), requests.Timeout()):
            with self.subTest(exc=type(exc).__name__), self.assertRaises(UpstreamUnavailable):
                prc.capture_revision(FakeClient({self.PR: exc}), REPO, 7, A, B)
        with self.assertRaises(RateLimited) as ctx:
            prc.capture_revision(FakeClient({self.PR: GitHubRateLimited(900)}), REPO, 7, A, B)
        self.assertEqual(ctx.exception.retry_after, 900)
        self.assertTrue(ctx.exception.retryable)

    def test_malformed_responses(self):
        with self.assertRaises(UpstreamUnavailable):
            prc.capture_revision(FakeClient({self.PR: [1, 2, 3]}), REPO, 7, A, B)
        with self.assertRaises(UpstreamUnavailable):
            prc.capture_revision(FakeClient({self.PR: pr_json(base='XYZ')}), REPO, 7, A, B)
        with self.assertRaises(UpstreamUnavailable):
            prc.capture_revision(FakeClient({self.PR: {'number': 7}}), REPO, 7, A, B)

    def test_only_base_repository_endpoints_are_called_for_fork_prs(self):
        client = self.client(pr_json(head_repo='stranger/widgets'))
        got = prc.capture_revision(client, REPO, 7, A, B)
        self.assertTrue(got.metadata['is_fork'])
        self.assertTrue(all(url.startswith('https://api.github.com/repos/acme/widgets/') for url, _ in client.calls))


class GitHubClientLimits(unittest.TestCase):
    def response(self, status, headers):
        return SimpleNamespace(status_code=status, headers=headers, raise_for_status=lambda: None)

    def test_long_rate_limit_wait_is_reported_instead_of_blocking(self):
        client = GitHubClient(token='t', max_wait_seconds=30)
        with patch.object(client.session, 'get', return_value=self.response(429, {'Retry-After': '3600'})), \
                patch('time.sleep') as sleep:
            with self.assertRaises(GitHubRateLimited) as ctx:
                client.get('https://api.github.com/x')
        self.assertEqual(ctx.exception.wait_seconds, 3601)
        sleep.assert_not_called()

    def test_default_behaviour_still_waits_and_retries(self):
        client = GitHubClient(token='t')
        ok = self.response(200, {})
        with patch.object(client.session, 'get', side_effect=[self.response(429, {'Retry-After': '2'}), ok]), \
                patch('time.sleep') as sleep:
            self.assertIs(client.get('https://api.github.com/x'), ok)
        sleep.assert_called_once_with(3)
        self.assertEqual((client.timeout, client.max_retries, client.max_wait_seconds), (30, 5, None))

    def test_timeout_and_retries_are_configurable(self):
        client = GitHubClient(token='t', timeout=5, max_retries=2)
        with patch.object(client.session, 'get', side_effect=requests.Timeout()) as get, patch('time.sleep'):
            with self.assertRaises(requests.Timeout):
                client.get('https://api.github.com/x')
        self.assertEqual(get.call_count, 2)
        self.assertEqual(get.call_args.kwargs['timeout'], 5)


if __name__ == '__main__':
    unittest.main(verbosity=2)
