"""AI 인사이트 소스 코드 열람: 기준 커밋 고정·비밀/생성/바이너리 차단·양 제한·인용 검증·도구 노출. DB·외부 호출 없음.

임시 git 저장소를 만들어 실제 git 명령으로 읽는다(가짜 공급자로 대화·리포트 흐름까지 확인).
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_ai_insights import FakeProvider, run_with, snapshot  # noqa: E402  (같은 가짜 스냅샷·공급자)

from app.services.ai import insight_code  # noqa: E402
from app.services.ai import insight_service as S  # noqa: E402
from app.services.ai.insight_code import CodeReader  # noqa: E402
from app.services.ai.insight_tools import InsightData, Ledger, ToolCall, run_tool, tool_catalog  # noqa: E402

SERVICE = '\n'.join(['"""서비스 계층."""', 'from app.core.helpers import normalize', ''] +
                    [f'def handler_{i}(value):\n    return normalize(value) + {i}' for i in range(120)]) + '\n'


def git(cwd, *args):
    return subprocess.run(['git', '-c', 'user.name=t', '-c', 'user.email=t@example.invalid', *args], cwd=cwd,
                          check=True, capture_output=True, text=True).stdout.strip()


class CodeRepo(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.mkdtemp(prefix='gitviz-insight-code-')
        files = {
            'app/core/service.py': SERVICE,
            'app/core/models.py': 'from app.core.service import handler_1\n\nclass Model:\n    pass\n',
            'app/core/helpers.py': 'def normalize(v):\n    return v\n',
            'config/settings.py': 'DEBUG = False\napi_key = "abcdefghijklmnop12345"\n',
            '.env': 'TOKEN=abc\n',
            'package-lock.json': '{}\n',
            'docs/guide.md': '# 안내\n서비스 구조 설명\n',
            'README.md': '# demo\n',
        }
        for path, body in files.items():
            target = Path(cls.root, path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body, encoding='utf-8', newline='\n')
        Path(cls.root, 'assets').mkdir()
        Path(cls.root, 'assets/logo.bin').write_bytes(b'\x89PNG\0\0binary')
        git(cls.root, 'init', '--quiet')
        git(cls.root, 'add', '-A')
        git(cls.root, 'commit', '--quiet', '-m', 'snapshot')
        cls.sha = git(cls.root, 'rev-parse', 'HEAD')
        # 스냅샷 뒤의 커밋과 커밋하지 않은 작업 트리 변경은 읽지 않아야 한다.
        Path(cls.root, 'app/core/helpers.py').write_text('def normalize(v):\n    return "LATER"\n', encoding='utf-8')
        git(cls.root, 'commit', '--quiet', '-am', 'later')
        Path(cls.root, 'app/core/helpers.py').write_text('UNCOMMITTED = True\n', encoding='utf-8')

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def reader(self):
        return CodeReader.open(self.root, self.sha)


class ReaderRules(CodeRepo):
    def test_reads_only_the_snapshot_commit(self):
        result = self.reader().read_file('helpers.py')
        self.assertEqual(result['path'], 'app/core/helpers.py')
        self.assertIn('return v', result['content'])
        self.assertNotIn('LATER', result['content'])
        self.assertNotIn('UNCOMMITTED', result['content'])
        self.assertEqual(git(self.root, 'status', '--porcelain'), 'M app/core/helpers.py')   # 작업 트리 그대로

    def test_open_requires_a_clone_with_that_commit(self):
        self.assertIsNone(CodeReader.open(self.root, 'f' * 40))
        self.assertIsNone(CodeReader.open(tempfile.gettempdir() + '/gitviz-no-such-clone', self.sha))
        self.assertIsNone(CodeReader.open(None, self.sha))

    def test_secret_generated_and_binary_files_are_not_sent(self):
        reader = self.reader()
        for path in ('.env', 'config/settings.py', 'package-lock.json', 'assets/logo.bin'):
            result = reader.read_file(path)
            self.assertEqual(result.get('error'), 'FILE_NOT_READABLE', path)
            self.assertNotIn('content', result)
        self.assertEqual(reader.chars_used, 0)
        self.assertIn('안내', reader.read_file('docs/guide.md')['content'])           # 문서 텍스트는 읽는다

    def test_line_ranges_are_bounded_and_numbered(self):
        reader = self.reader()
        result = reader.read_file('app/core/service.py', 10, 900)
        self.assertEqual(result['start_line'], 10)
        self.assertLessEqual(result['end_line'] - result['start_line'], insight_code.READ_MAX_LINES - 1)
        self.assertLessEqual(len(result['content']), insight_code.READ_MAX_CHARS)
        self.assertTrue(result['content'].startswith('   10| '))
        self.assertEqual(reader.read_file('app/core/service.py', 5000)['error'], 'INVALID_ARGUMENT')
        self.assertEqual(reader.read_file('nothing.py')['error'], 'FILE_NOT_FOUND')

    def test_search_is_fixed_string_and_skips_secrets(self):
        reader = self.reader()
        hits = reader.search('app.core.service', 'app/core/models.py')
        self.assertEqual([(h['path'], h['line']) for h in hits['matches']], [('app/core/models.py', 1)])
        self.assertEqual(reader.search('api_key')['matches'], [])                   # 비밀 파일의 줄은 보내지 않는다
        self.assertEqual(reader.search('.*')['matches'], [])                        # 정규식이 아니라 고정 문자열
        self.assertEqual(reader.search('a')['error'], 'INVALID_ARGUMENT')
        self.assertEqual(reader.search('normalize', 'no/such/dir')['error'], 'PATH_NOT_FOUND')

    def test_directory_listing(self):
        root = self.reader().list_directory('')
        names = {e['name']: e['type'] for e in root['entries']}
        self.assertEqual(names['app'], 'directory')
        self.assertEqual(names['README.md'], 'file')
        self.assertEqual(self.reader().list_directory('missing')['error'], 'PATH_NOT_FOUND')

    def test_run_budget_limits_files_and_characters(self):
        reader = self.reader()
        with patch.object(insight_code, 'RUN_MAX_FILES', 1):
            self.assertIn('content', reader.read_file('README.md'))
            self.assertEqual(reader.read_file('docs/guide.md')['error'], 'CODE_BUDGET_EXCEEDED')
            self.assertIn('content', reader.read_file('README.md'))                 # 이미 읽은 파일은 다시 읽을 수 있다
        reader.chars_used = insight_code.RUN_MAX_CHARS
        self.assertEqual(reader.read_file('README.md')['error'], 'CODE_BUDGET_EXCEEDED')
        self.assertEqual(reader.search('normalize')['error'], 'CODE_BUDGET_EXCEEDED')


class ToolExposure(CodeRepo):
    def test_code_tools_exist_only_when_code_access_is_on(self):
        self.assertNotIn('readFile', tool_catalog())
        self.assertIn('readFile', tool_catalog(include_code=True))
        off = InsightData(None, snapshot())
        self.assertEqual(run_tool(off, 'readFile', {'path': 'README.md'})['error'], 'CODE_ACCESS_DISABLED')
        on = InsightData(None, snapshot(), self.reader())
        self.assertIn('content', run_tool(on, 'readFile', {'path': 'README.md', 'startLine': 1, 'endLine': 5}))
        self.assertEqual(run_tool(on, 'readFile', {'path': 'README.md', 'startLine': -1})['error'], 'INVALID_ARGUMENT')
        self.assertEqual(run_tool(on, 'searchCode', {'query': 'x', 'path': 'README.md'})['error'], 'INVALID_ARGUMENT')

    def test_git_failures_become_tool_errors(self):
        on = InsightData(None, snapshot(), self.reader())
        with patch.object(CodeReader, 'tree', side_effect=RuntimeError('boom')):
            self.assertEqual(run_tool(on, 'readFile', {'path': 'README.md'})['error'], 'CODE_UNAVAILABLE')


class CitationRules(CodeRepo):
    def ledger(self):
        data = InsightData(None, snapshot(), self.reader())
        ledger = Ledger()
        ledger.add(ToolCall('T1', 'readFile', {}, run_tool(data, 'readFile', {'path': 'app/core/models.py'})))
        ledger.add(ToolCall('T2', 'searchCode', {}, run_tool(data, 'searchCode', {'query': 'normalize', 'directory': 'app/core/service.py'})))
        return ledger

    def test_code_citations_must_be_inside_lines_that_were_read(self):
        ledger = self.ledger()
        item = {'title': '참조', 'summary': 'models.py는 handler_1을 가져옵니다.', 'severity': 'info', 'confidence': 'medium',
                'evidence': [{'toolCallId': 'T1', 'label': '전체 줄 수', 'value': '4'}],
                'interpretation': 'app.core.service 모듈에 의존할 수 있습니다.', 'recommendation': '함께 검토하세요.',
                'relatedFiles': [], 'relatedCommits': [], 'relatedContributors': [],
                'relatedCode': [{'path': 'app/core/models.py', 'startLine': 1, 'endLine': 3},
                                {'path': 'app/core/models.py', 'startLine': 1, 'endLine': 40},
                                {'path': 'app/core/service.py', 'startLine': 2, 'endLine': 2},
                                {'path': 'app/core/service.py', 'startLine': 50, 'endLine': 60},
                                {'path': 'src/ghost.py', 'startLine': 1, 'endLine': 2}]}
        checked = S.validate_insight(item, ledger)
        self.assertIsNotNone(checked.value, checked.issues)
        self.assertEqual(checked.value['relatedCode'], [{'path': 'app/core/models.py', 'startLine': 1, 'endLine': 3},
                                                        {'path': 'app/core/service.py', 'startLine': 2, 'endLine': 2}])
        self.assertEqual(len([r for r in checked.value['validation']['removedReferences'] if r.startswith('code:')]), 3)

    def test_names_seen_in_code_may_be_mentioned_but_unseen_files_may_not(self):
        ledger = self.ledger()
        self.assertEqual(S.unverified_mentions(['models.py가 app.core.service의 handler_1을 가져옵니다.'], ledger), [])
        self.assertEqual(S.unverified_mentions(['helpers.py가 원인'], ledger), ['helpers.py'])   # 읽은 코드에 없는 파일명
        self.assertEqual(S.unverified_mentions(['payment/gateway.py가 원인'], ledger), ['payment/gateway.py'])


class EntryPoints(unittest.TestCase):
    def test_picks_entry_files_nearest_the_root(self):
        f = ('100644', 10)
        tree = {'src/cli/main.py': f, 'app/main.py': f, 'main.go': f, 'cmd/server/main.go': f, 'README.md': f}
        self.assertEqual(S.entry_points(tree), ['main.go', 'app/main.py'])
        self.assertEqual(S.entry_points(tree, 5), ['main.go', 'app/main.py', 'src/cli/main.py', 'cmd/server/main.go'])
        self.assertEqual(S.entry_points({'index.js': f, 'main.py': f}), ['main.py', 'index.js'])   # 같은 깊이면 이름 목록 순

    def test_skips_dependency_test_build_and_hidden_folders_and_links(self):
        f = ('100644', 10)
        tree = {'node_modules/pkg/index.js': f, 'tests/main.py': f, 'dist/main.js': f, '.github/app.py': f,
                'link/main.py': ('120000', 10), 'sub/main.go': ('160000', 0), 'a/b/c/d/e/main.py': f,
                'utils/helpers.py': f}
        self.assertEqual(S.entry_points(tree), [])
        self.assertEqual(S.entry_points({**tree, 'a/b/c/d/main.py': f}), ['a/b/c/d/main.py'])   # 폴더 깊이 4까지


class Workflows(CodeRepo):
    def setUp(self):
        patch.object(S.InsightRun, 'authorize', lambda self: None).start()
        self.addCleanup(patch.stopall)

    def test_conversation_reads_code_and_cites_verified_lines(self):
        provider = FakeProvider()
        run = run_with(provider, InsightData(None, snapshot(), self.reader()))
        run.code_requested = True
        answer = S.answer_question(run, [], 'app/core/service.py 코드의 역할은?', lambda e: None)
        self.assertEqual([c.tool for c in run.ledger.calls.values()], ['readFile'])
        self.assertEqual(answer['relatedCode'], [{'path': 'app/core/service.py', 'startLine': 1, 'endLine': 5}])
        self.assertIn('readFile', provider.requests[0].user)                             # 코드 도구가 목록에 있다
        self.assertIn('Source code access: ON', provider.requests[0].user)
        self.assertEqual(S._basis(run, 'insight_chat')['code']['filesRead'], ['app/core/service.py'])

    def test_without_code_access_the_model_is_told_and_tools_are_hidden(self):
        provider = FakeProvider()
        run = run_with(provider, InsightData(None, snapshot()))
        S.answer_question(run, [], '최근 6개월 동안 가장 많이 기여한 개발자는?', lambda e: None)
        self.assertNotIn('readFile(', provider.requests[0].user)
        self.assertIn('Source code access: OFF', provider.requests[0].user)
        self.assertEqual(S._basis(run, 'insight_chat')['code'], {'requested': False, 'available': False, 'charsSent': 0, 'filesRead': []})

    def test_report_plan_adds_code_steps_only_with_code_access(self):
        on = InsightData(None, snapshot(), self.reader())
        self.assertEqual(S.code_plan('maintenance_risk', on), [('readFile', {'path': 'app/core/service.py', 'endLine': 150}),
                                                               ('readFile', {'path': 'app/core/models.py', 'endLine': 150})])
        self.assertEqual(S.code_plan('change_impact', on), [('searchCode', {'query': 'models', 'directory': 'app/core/service.py'})])
        self.assertEqual(S.code_plan('overview', on), [('listDirectory', {}), ('readFile', {'path': 'README.md', 'endLine': 80})])
        self.assertEqual(S.code_plan('maintenance_risk', InsightData(None, snapshot())), [])
        tree = {**self.reader().tree(), 'app/main.py': ('100644', 10)}
        with patch.object(CodeReader, 'tree', return_value=tree):
            self.assertEqual(S.code_plan('overview', on), [('listDirectory', {}), ('readFile', {'path': 'README.md', 'endLine': 80}),
                                                           ('readFile', {'path': 'app/main.py', 'endLine': 100})])
        provider = FakeProvider()
        run = run_with(provider, on)
        with patch.object(S, 'metric_plan', lambda category, data: [('getFileChangeFrequency', {'limit': 5})]):  # DB 없는 지표 도구
            category = S._report_category(run, 'maintenance_risk', '기술 부채', lambda e: None)
        self.assertIn('readFile', [c['tool'] for c in category['toolCalls']])
        self.assertTrue(category['insights'][0]['relatedCode'])
        self.assertIn('Source code access: ON', provider.requests[0].user)

    def test_fabricated_code_citation_is_dropped(self):
        provider = FakeProvider(mode='fabricate')
        run = run_with(provider, InsightData(None, snapshot(), self.reader()))
        with self.assertRaises(S.InsightFailed):
            S.answer_question(run, [], 'app/core/service.py 코드의 역할은?', lambda e: None)


if __name__ == '__main__':
    os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
    unittest.main(verbosity=2)
