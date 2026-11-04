"""AI 인사이트: 도구 허용 목록·근거 검증·대화 루프·보고서 범주. DB와 외부 호출 없음(가짜 공급자)."""
import os
import sys
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ['PYTHON_DOTENV_DISABLED'] = '1'
os.environ.setdefault('DATABASE_URL', 'postgresql://gitviz_test:unused@127.0.0.1:1/gitviz_test')
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ai_fake_provider import build_insight_output  # noqa: E402
from app.services.ai import insight_service as S  # noqa: E402
from app.services.ai import prompts  # noqa: E402
from app.services.ai.insight_tools import TOOLS, InsightData, Ledger, ToolCall, run_tool  # noqa: E402
from app.services.ai.providers import registry  # noqa: E402
from app.services.ai.providers.base import GenerationRequest, ProviderResponse  # noqa: E402
from app.services.ai.providers.json_provider import strict_schema  # noqa: E402

SHA = 'a1b2c3d4e5f60718293a4b5c6d7e8f9012345678'


def payload():
    files = [{'id': 1, 'path': 'app/core/service.py'}, {'id': 2, 'path': 'app/core/models.py'},
             {'id': 3, 'path': 'docs/readme.md'}]
    risk = [{'file_id': 1, 'path': 'app/core/service.py', 'change_freq': 12, 'churn': 340.0, 'bugfix_count': 5,
             'bug_ratio': 5 / 12, 'maintenance_priority': 0.9167, 'bus_factor': 1, 'contributor_count': 2,
             'top_owner_share': 0.8333, 'last_refactor_at': None, 'evidence_status': 'observed'},
            {'file_id': 2, 'path': 'app/core/models.py', 'change_freq': 7, 'churn': 90.0, 'bugfix_count': 1,
             'bug_ratio': 1 / 7, 'maintenance_priority': 0.5, 'bus_factor': 2, 'contributor_count': 2,
             'top_owner_share': 0.57, 'last_refactor_at': None, 'evidence_status': 'observed'}]
    return {
        'summary': {'repository': {'name': 'demo', 'default_branch': 'main'}, 'total_commits': 40, 'commits_30d': 6,
                    'total_contributors': 2, 'active_contributors_90d': 2, 'total_releases': 1,
                    'latest_release': 'v1.0', 'bugfix_pct': 22.5},
        'file_index': files,
        'contributor_index': [{'id': 10, 'display_name': 'Alice Kim', 'github_login': 'alice'},
                              {'id': 11, 'display_name': 'Bob', 'github_login': None}],
        'top-contributors': {'contributors': [{'id': 10, 'commit_count': 30, 'last_commit_at': '2026-10-01T00:00:00Z'},
                                              {'id': 11, 'commit_count': 10, 'last_commit_at': '2026-09-01T00:00:00Z'}]},
        'risk-scores': {'files': risk},
        'bus-factor': {'summary': {'file_bf80_median': 1}, 'distribution': [{'bus_factor': '1', 'file_count': 1}],
                       'top_owners': [{'contributor_id': 10, 'name': 'alice', 'exclusive_file_count': 1}],
                       'risky_modules': [{'module': 'app/core', 'file_count': 2, 'bf1_count': 1, 'bf1_ratio': 0.5}],
                       'files': [{'file_id': 1, 'path': 'app/core/service.py', 'bus_factor': 1, 'total_change_count': 12,
                                  'top_owner_share': 0.8333, 'low_support': False, 'owners': [{'name': 'alice'}]}]},
        'coupling': {'meta': {'co_change_unit': 'commit'}, 'pairs': [
            {'file_a_id': 1, 'file_a_path': 'app/core/service.py', 'file_b_id': 2, 'file_b_path': 'app/core/models.py',
             'co_change_count': 6, 'change_count_a': 12, 'change_count_b': 7, 'a_to_b_ratio': 0.5,
             'b_to_a_ratio': 0.8571, 'low_support': False, 'coupling_score': 0.8571, 'adjusted_score': 0.1}]},
        'network/top-pairs': [{'contributor_a': {'id': 10, 'name': 'alice'}, 'contributor_b': {'id': 11, 'name': 'Bob'},
                               'edge_weight': 1.5, 'co_edit_raw': 1.0, 'review_directed_a_to_b': 0.5,
                               'review_directed_b_to_a': 0.0}],
        'ownership': {'1': {'shares': [{'contributor_id': 10, 'share': 0.8333}], 'bus_factor': 1, 'observation_count': 12}},
        'timeline': {'months': ['2026-09', '2026-10'], 'commits': [18, 22], 'active_contributors': [2, 2],
                     'bugfix_pct': [20.0, 25.0]},
        'commit-types': {'total': 40, 'types': [{'type': 'feature', 'count': 20, 'pct': 50.0}]},
        'data_quality': {'target_tree_file_count': 3},
    }


def snapshot(**overrides):
    return SimpleNamespace(id=7, repository_id=3, payload=payload(), window_start='2026-05-01T00:00:00+00:00',
                           window_end='2026-11-01T00:00:00+00:00', target_sha=SHA, formula_version='f1',
                           payload_sha256='0' * 64, **overrides)


class FakeProvider:
    def __init__(self, mode='ok', profile_id='claude-opus-5-5-v1'):
        self.profile = registry.get_profile(profile_id)
        self.mode = mode
        self.requests = []

    def estimate_tokens(self, request):
        return 100

    def generate(self, request: GenerationRequest):
        self.requests.append(request)
        parsed = build_insight_output(request.system, request.user, self.mode)
        return ProviderResponse(request_id='r', model=self.profile.model, finish='complete', text='{}', parsed=parsed,
                                parse_error=None, usage=None, cost=Decimal('0.01'), latency_seconds=0.0)


def run_with(provider, data):
    return S.InsightRun(provider=provider, data=data, owner_id='owner', repository_id=3, profile_id=provider.profile.id,
                        session_factory=None)


class ToolsAndLedger(unittest.TestCase):
    def setUp(self):
        self.data = InsightData(None, snapshot())

    def test_unknown_tools_and_arguments_are_not_executed(self):
        self.assertEqual(run_tool(self.data, 'dropTables', {})['error'], 'UNKNOWN_TOOL')
        self.assertEqual(run_tool(self.data, 'getCoChangedFiles', {'repository_id': 9})['error'], 'INVALID_ARGUMENT')
        self.assertEqual(run_tool(self.data, 'getProjectOverview', {'path': 'x'})['error'], 'INVALID_ARGUMENT')
        self.assertEqual(run_tool(self.data, 'getContributorStatistics', {'limit': '5'})['error'], 'INVALID_ARGUMENT')
        self.assertEqual(run_tool(self.data, 'getFileChangeFrequency', {'sortBy': 'risk; drop'})['error'], 'INVALID_ARGUMENT')
        self.assertEqual(run_tool(self.data, 'getCommitDetails', {'sha': "a' OR 1=1"})['error'], 'INVALID_ARGUMENT')

    def test_tools_are_bound_to_the_snapshot_and_resolve_names(self):
        result = run_tool(self.data, 'getCoChangedFiles', {'path': 'service.py'})
        self.assertEqual(result['path'], 'app/core/service.py')
        self.assertEqual(result['co_changed_files'][0]['path'], 'app/core/models.py')
        self.assertEqual(result['period']['snapshot_id'], 7)
        self.assertIn('코드 의존성', ' '.join(result['notes']))
        missing = run_tool(self.data, 'getCoChangedFiles', {'path': 'UserService.java'})
        self.assertEqual(missing['error'], 'FILE_NOT_FOUND')
        stats = run_tool(self.data, 'getContributorStatistics', {'limit': 5})
        self.assertEqual([c['name'] for c in stats['contributors']], ['alice', 'Bob'])
        self.assertNotIn('email', str(stats))
        self.assertEqual(run_tool(self.data, 'getContributorCollaboration', {'contributor': 'Alice Kim'})['contributor'], 'alice')
        ranked = run_tool(self.data, 'getFileChangeFrequency', {'sortBy': 'bugfix_count'})
        self.assertEqual(ranked['files'][0]['bugfix_count'], 5)
        self.assertEqual(self.data.link_ids(['app/core/service.py', 'ghost.py'], ['alice', 'nobody']),
                         {'files': {'app/core/service.py': 1}, 'contributors': {'alice': 10}})

    def test_every_tool_documents_only_known_arguments(self):
        from app.services.ai.insight_tools import ARG_NAMES
        for spec in TOOLS.values():
            self.assertTrue(set(spec.args) <= set(ARG_NAMES), spec.name)
            self.assertTrue(hasattr(InsightData, spec.method), spec.name)

    def test_ledger_supports_only_values_present_in_that_call(self):
        ledger = Ledger()
        ledger.add(ToolCall('T1', 'getFileChangeFrequency', {}, run_tool(self.data, 'getFileChangeFrequency', {})))
        ledger.add(ToolCall('T2', 'getCommitDetails', {}, {'sha': SHA, 'author': 'alice'}))
        self.assertTrue(ledger.supports('T1', '12'))
        self.assertTrue(ledger.supports('T1', '0.92'))          # 0.9167을 둘째 자리까지 쓴 값
        self.assertTrue(ledger.supports('T1', '91.67%'))        # 비율의 백분율 표기
        self.assertFalse(ledger.supports('T1', '13'))
        self.assertFalse(ledger.supports('T2', '12'))           # 다른 호출의 값은 근거가 아니다
        self.assertFalse(ledger.supports('T9', '12'))
        self.assertTrue(ledger.supports('T1', 'app/core/service.py'))
        self.assertEqual(ledger.match_sha(SHA[:8]), SHA)
        self.assertIsNone(ledger.match_sha('deadbeef'))
        self.assertTrue(ledger.known_path('app/core'))
        self.assertEqual(ledger.known_contributor('ALICE'), 'alice')


class Validation(unittest.TestCase):
    def setUp(self):
        self.ledger = Ledger()
        data = InsightData(None, snapshot())
        self.ledger.add(ToolCall('T1', 'getFileChangeFrequency', {}, run_tool(data, 'getFileChangeFrequency', {})))

    def insight(self, **changes):
        item = {'title': '변경 집중', 'summary': 'app/core/service.py가 12회 변경되었습니다.', 'severity': 'high',
                'confidence': 'high', 'evidence': [{'toolCallId': 'T1', 'label': '변경 횟수', 'value': '12'}],
                'interpretation': '유지보수 부담이 있을 수 있습니다.', 'recommendation': '테스트를 보강해 보세요.',
                'relatedFiles': ['app/core/service.py'], 'relatedCommits': [], 'relatedContributors': []}
        return {**item, **changes}

    def test_valid_insight_keeps_evidence_and_caps_confidence(self):
        checked = S.validate_insight(self.insight(), self.ledger)
        self.assertIsNotNone(checked.value)
        self.assertEqual(checked.value['evidence'][0]['tool'], 'getFileChangeFrequency')
        self.assertEqual(checked.value['confidence'], 'medium')   # 근거 1개면 high를 주지 않는다

    def test_invented_values_and_mentions_are_rejected_or_removed(self):
        self.assertIsNone(S.validate_insight(self.insight(evidence=[{'toolCallId': 'T1', 'label': 'x', 'value': '999'}]),
                                             self.ledger).value)
        self.assertIsNone(S.validate_insight(self.insight(summary='src/ghost/module.py가 원인입니다.'), self.ledger).value)
        self.assertIsNone(S.validate_insight(self.insight(summary='커밋 0badc0ffee1234567 이후 늘었습니다.'), self.ledger).value)
        self.assertIsNone(S.validate_insight(self.insight(severity='fatal'), self.ledger).value)
        cleaned = S.validate_insight(self.insight(relatedFiles=['app/core/service.py', 'ghost.py'],
                                                  relatedContributors=['nobody']), self.ledger)
        self.assertEqual(cleaned.value['relatedFiles'], ['app/core/service.py'])
        self.assertEqual(cleaned.value['relatedContributors'], [])
        self.assertIn('file:ghost.py', cleaned.value['validation']['removedReferences'])

    def test_answer_blocks_are_bounded_and_need_evidence(self):
        answer = {'text': '데이터: 12회', 'blocks': [{'type': 'table', 'title': 't', 'columns': ['a', 'b'],
                                                   'rows': [['1', '2']] * 50, 'items': [], 'value': '', 'unit': ''},
                                                  {'type': 'html', 'title': 'x'}],
                  'evidence': [{'toolCallId': 'T1', 'label': '변경', 'value': '12'}], 'relatedFiles': [],
                  'relatedCommits': [], 'relatedContributors': [], 'limitations': []}
        checked = S.validate_answer(answer, self.ledger)
        self.assertEqual(len(checked.value['blocks']), 1)
        self.assertEqual(len(checked.value['blocks'][0]['rows']), 30)
        self.assertIsNone(S.validate_answer({**answer, 'evidence': []}, self.ledger).value)


class Workflows(unittest.TestCase):
    def setUp(self):
        self.authorize = patch.object(S.InsightRun, 'authorize', lambda self: None).start()
        self.addCleanup(patch.stopall)
        self.events = []

    def test_conversation_calls_tools_then_answers_with_verified_evidence(self):
        provider = FakeProvider()
        run = run_with(provider, InsightData(None, snapshot()))
        answer = S.answer_question(run, [], '가장 많이 기여한 개발자는 누구야?', self.events.append)
        self.assertEqual(run.calls, 2)
        self.assertEqual([c.tool for c in run.ledger.calls.values()], ['getContributorStatistics'])
        self.assertTrue(answer['evidence'] and answer['evidence'][0]['toolCallId'] == 'T1')
        self.assertEqual(answer['blocks'][0]['type'], 'table')
        self.assertIn('tool', {e['stage'] for e in self.events})
        first = provider.requests[0]
        self.assertEqual(first.kind, 'insight_chat')
        self.assertNotIn('가장 많이 기여한', first.system)          # 질문은 경계 블록 안 user 메시지에만 있다
        self.assertIn('BEGIN question>>>\n가장 많이 기여한 개발자는 누구야?', first.user)

    def test_fabricated_answers_are_refused_after_one_repair(self):
        provider = FakeProvider(mode='fabricate')
        run = run_with(provider, InsightData(None, snapshot()))
        with self.assertRaises(S.InsightFailed) as ctx:
            S.answer_question(run, [], '위험한 파일은?', self.events.append)
        self.assertEqual(ctx.exception.code, 'OUTPUT_UNVERIFIED')
        self.assertIn('도구 결과에 없는', provider.requests[-1].user)  # 보완 요청에 검증 사유를 돌려준다

    def test_report_category_uses_server_plan_and_records_tool_calls(self):
        provider = FakeProvider()
        run = run_with(provider, InsightData(None, snapshot()))
        with patch.object(S, 'evidence_plan', lambda category, data: [('getFileChangeFrequency', {'limit': 5}),
                                                                     ('getOwnershipConcentration', {})]):
            category = S._report_category(run, 'maintenance_risk', '기술 부채', self.events.append)
        self.assertEqual(len(category['insights']), 1)
        insight = category['insights'][0]
        self.assertEqual(insight['category'], 'maintenance_risk')
        self.assertEqual(set(insight) >= {'id', 'severity', 'confidence', 'evidence', 'interpretation', 'recommendation',
                                          'relatedFiles', 'relatedCommits', 'relatedContributors', 'analysisPeriod',
                                          'generatedAt'}, True)
        self.assertEqual([c['tool'] for c in category['toolCalls']], ['getFileChangeFrequency', 'getOwnershipConcentration'])
        for path in insight['relatedFiles']:                    # 화면이 기존 파일 상세를 열 수 있는 id
            self.assertEqual(insight['links']['files'][path], run.data.files[path])
        self.assertEqual(provider.requests[0].kind, 'insight_report')

    def test_fabricated_report_insights_are_excluded(self):
        provider = FakeProvider(mode='fabricate')
        run = run_with(provider, InsightData(None, snapshot()))
        with patch.object(S, 'evidence_plan', lambda category, data: [('getFileChangeFrequency', {'limit': 5})]):
            category = S._report_category(run, 'maintenance_risk', '기술 부채', self.events.append)
        self.assertEqual(category['insights'], [])
        self.assertEqual(category['rejectedCount'], 1)
        self.assertEqual(provider.requests.__len__(), 2)           # 한 번만 보완 요청한다


class ProviderWiring(unittest.TestCase):
    def test_insight_kinds_have_versioned_prompts_and_strict_schemas(self):
        prompts.verify_prompt_digests()
        for profile_id in ('claude-opus-5-5-v1', 'gpt-5.4-v4', 'ollama-local-v1'):
            profile = registry.get_profile(profile_id)
            for kind in ('insight_report', 'insight_chat'):
                self.assertEqual(profile.schema_version(kind), f'{kind}-output-v1')
                schema = strict_schema(kind, profile.schema_version(kind))
                self.assertFalse(schema['additionalProperties'])
        self.assertIn('{{BOUNDARY}}', prompts.system_template('insight_chat', 'insight_chat-v1'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
