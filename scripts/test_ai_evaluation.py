"""평가 도구(evaluate_ai_review.py)의 채점·세트 검증·예산 보호 테스트. 네트워크·DB·모델을 쓰지 않는다."""
import copy
import json
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import evaluate_ai_review as ev  # noqa: E402

H40 = lambda c: c * 40  # noqa: E731


def case(cid='c1', head='b', **over):
    data = {'id': cid, 'repo_full_name': 'acme/widgets', 'visibility': 'public', 'license': 'MIT', 'pr_number': 3, 'base_sha': H40('a'), 'head_sha': H40(head),
            'selected_paths': ['a.py'], 'labeled_by': 'human', 'truths': [{'path': 'a.py', 'side': 'RIGHT', 'start_line': 10, 'end_line': 12}]}
    data.update(over)
    return data


def manifest(tuning=None, final=None, **over):
    data = {'sets': {'tuning': tuning if tuning is not None else [case('t1', 'b')], 'final': final if final is not None else [case('f1', 'c')]},
            'criteria': {'min_recall': None, 'min_precision': None, 'max_cost_per_case_usd': None}}
    data.update(over)
    return data


def finding(path='a.py', start=10, end=10, side='RIGHT', verdict='unverified', title='t'):
    return {'finding': {'title': title, 'human_validation': verdict, 'location': {'path': path, 'side': side, 'start_line': start, 'end_line': end}}}


class Manifest(unittest.TestCase):
    def test_shipped_manifest_is_structurally_valid_but_not_release_ready(self):
        m = ev.load_manifest()
        self.assertEqual(ev.validate_manifest(m), [])
        blockers = ev.release_blockers(m)
        self.assertTrue(any('final 세트에 실제' in b for b in blockers))                        # 사람이 마련해야 할 자료가 비어 있다
        self.assertTrue(any('통과 기준' in b for b in blockers))
        self.assertTrue(any('아직 실행되지 않았' in b for b in blockers))
        self.assertTrue(all(c.get('synthetic') for c in ev.cases_of(m, 'tuning')))              # 합성 자료는 조정 세트에만 있다

    def test_validation_catches_each_problem(self):
        self.assertEqual(ev.validate_manifest(manifest()), [])
        cases = {
            'sets 누락': ({'sets': {'tuning': []}}, 'sets에는'),
            'id 겹침': (manifest(tuning=[case('x', 'b')], final=[case('x', 'c')]), '세트 분리 위반'),
            'head 겹침': (manifest(tuning=[case('t', 'b')], final=[case('f', 'b')]), '같은 저장소·head SHA'),
            '짧은 SHA': (manifest(tuning=[case('t', 'b', head_sha='abc123')]), 'head_sha'),
            '비공개·허락 없음': (manifest(tuning=[case('t', 'b', visibility='private')]), '사용 허락'),
            '공개인데 license 없음': (manifest(tuning=[case('t', 'b', license='')]), 'license'),
            '합성 final': (manifest(final=[case('f', 'c', synthetic=True)]), '합성 자료는 최종 세트'),
            '정답 없음': (manifest(tuning=[case('t', 'b', truths=[])]), 'negative_control'),
            '정답 형식': (manifest(tuning=[case('t', 'b', truths=[{'path': 'a', 'side': 'X', 'start_line': 1, 'end_line': 1}])]), 'truths 항목'),
            '정답 줄 역순': (manifest(tuning=[case('t', 'b', truths=[{'path': 'a', 'side': 'LEFT', 'start_line': 5, 'end_line': 2}])]), 'truths 항목'),
        }
        for name, (m, expected) in cases.items():
            with self.subTest(name):
                self.assertTrue(any(expected in p for p in ev.validate_manifest(m)), ev.validate_manifest(m))
        self.assertEqual(ev.validate_manifest(manifest(tuning=[case('t', 'b', visibility='private', permission_note='소유자 허락 2026-10-01')])), [])
        self.assertEqual(ev.validate_manifest(manifest(tuning=[case('t', 'b', truths=[], negative_control=True)])), [])

    def test_release_blockers_clear_only_when_everything_is_filled(self):
        m = manifest(final=[case('f1', 'c'), case('f2', 'd', truths=[], negative_control=True)], criteria={'min_recall': 0.5, 'min_precision': 0.5, 'max_cost_per_case_usd': 1},
                     final_run_done=True)
        self.assertEqual(ev.release_blockers(m), [])
        self.assertTrue(any('negative_control' in b for b in ev.release_blockers({**m, 'sets': {**m['sets'], 'final': [case('f1', 'c')]}})))


class Scoring(unittest.TestCase):
    def test_line_overlap_with_tolerance_and_same_side(self):
        truth = {'path': 'a.py', 'side': 'RIGHT', 'start_line': 10, 'end_line': 12}
        for start, end, ok in ((10, 10, True), (12, 14, True), (14, 15, True), (15, 16, False), (6, 7, False), (8, 9, True)):
            self.assertEqual(ev.overlaps(truth, finding(start=start, end=end)['finding']), ok, (start, end))
        self.assertFalse(ev.overlaps(truth, finding(side='LEFT')['finding']))
        self.assertFalse(ev.overlaps(truth, finding(path='b.py')['finding']))

    def test_case_score_separates_found_defects_unmatched_findings_and_human_verdicts(self):
        c = case(truths=[{'path': 'a.py', 'side': 'RIGHT', 'start_line': 10, 'end_line': 12}, {'path': 'a.py', 'side': 'RIGHT', 'start_line': 40, 'end_line': 41}])
        findings = [finding(start=11, end=11, verdict='valid'), finding(start=12, end=12, verdict='unverified'), finding(path='z.py', start=3, end=3, verdict='false_positive', title='x')]
        score = ev.score_case(c, {'status': 'succeeded', 'coverage': {'analysis_coverage': 'complete'}}, findings, 0.07)
        self.assertEqual((score.truths, score.matched_truths, score.findings, score.matched_findings), (2, 1, 3, 2))   # 같은 정답에 겹친 두 지적은 정답 1개만 찾은 것
        self.assertEqual(score.unmatched, [{'path': 'z.py', 'lines': [3, 3], 'side': 'RIGHT', 'title': 'x', 'human_validation': 'false_positive'}])
        self.assertEqual(score.verdicts, {'valid': 1, 'false_positive': 1, 'uncertain': 0, 'unverified': 1})

    def test_summary_counts_unjudged_separately_and_does_not_invent_a_verdict(self):
        a = ev.score_case(case(), {'status': 'succeeded', 'coverage': {'analysis_coverage': 'complete'}},
                          [finding(start=10, verdict='valid'), finding(path='q.py', verdict='uncertain'), finding(path='r.py', verdict='unverified')], 0.1)
        b = ev.score_case(case('c2', truths=[], negative_control=True), {'status': 'failed', 'error_code': 'OUTCOME_UNCERTAIN', 'coverage': {}}, [], 0.05)
        summary = ev.summarize([a, b])
        self.assertEqual((summary['cases'], summary['succeeded'], summary['failed']), (2, 1, ['c2']))
        self.assertEqual((summary['known_defects'], summary['known_defects_found'], summary['recall_on_known_defects']), (1, 1, 1.0))
        self.assertEqual((summary['human_verdicts']['valid'], summary['unjudged_findings'], summary['precision_among_human_judged']), (1, 2, 1.0))
        self.assertAlmostEqual(summary['cost_usd'], 0.15)
        self.assertIn('합의한 통과 기준이 없어', summary['verdict'])                               # 기준이 없으면 합격/불합격을 만들지 않는다
        none = ev.summarize([ev.score_case(case('n', truths=[], negative_control=True), {'status': 'succeeded', 'coverage': {}}, [], 0.0)])
        self.assertEqual((none['recall_on_known_defects'], none['precision_among_human_judged']), (None, None))   # 분모가 없으면 0이 아니라 없음

    def test_criteria_pass_and_fail(self):
        s = [ev.score_case(case(), {'status': 'succeeded', 'coverage': {}}, [finding(start=10, verdict='valid')], 0.2)]
        self.assertEqual(ev.summarize(s, {'min_recall': 0.5, 'min_precision': 0.5, 'max_cost_per_case_usd': 0.5})['verdict'], 'pass')
        self.assertEqual(ev.summarize(s, {'min_recall': 1.1})['verdict'], 'fail')
        self.assertEqual(ev.summarize(s, {'max_cost_per_case_usd': 0.1})['verdict'], 'fail')
        weak = [ev.score_case(case(), {'status': 'succeeded', 'coverage': {}}, [], 0.0)]
        self.assertEqual(ev.summarize(weak, {'min_precision': 0.5})['verdict'], 'fail')           # 판정된 지적이 없으면 기준을 채우지 못한 것

    def test_report_does_not_claim_correctness(self):
        s = [ev.score_case(case(), {'status': 'succeeded', 'coverage': {'analysis_coverage': 'complete'}}, [finding(start=10)], 0.1)]
        text = ev.render_report('tuning', 'live', s, ev.summarize(s))
        self.assertIn('구조 검증 통과는 내용이 옳다는 뜻이 아닙니다', text)
        for bad in ('오류 없음', '문제 없음', '정확합니다'):
            self.assertNotIn(bad, text)


class FakeResponse:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        return self._body


class FakeHttp:
    """GitViz API를 흉내 내는 http. 호출 순서와 접수 여부를 기록한다."""

    def __init__(self, estimate='0.10', reviewable=True, spent=('0.00', '0.07')):
        self.calls, self.estimate, self.reviewable, self.spent = [], estimate, reviewable, list(spent)

    def get(self, url, **kw):
        return FakeResponse(200, {'token': 'csrf'})

    def request(self, method, url, json=None, headers=None, timeout=None):
        path = url.split('://', 1)[1].split('/', 1)[1]
        self.calls.append((method, '/' + path))
        if path == 'api/ai/v1/usage':
            return FakeResponse(200, {'user_spent': self.spent.pop(0) if len(self.spent) > 1 else self.spent[0]})
        if path == 'api/ai/v1/inputs' and method == 'POST':
            return FakeResponse(202, {'input_id': 'i1'})
        if path == 'api/ai/v1/inputs/i1':
            return FakeResponse(200, {'status': 'ready', 'reviewable': self.reviewable, 'manifest_hash': 'c' * 64, 'policy_version': 'p',
                                      'token_estimate': {'estimated_max_cost': self.estimate}, 'transfer': {'provider': 'anthropic', 'profile_id': 'x', 'credential_version': 1}})
        if path == 'api/ai/v1/jobs' and method == 'POST':
            return FakeResponse(202, {'job_id': 'j1'})
        if path == 'api/ai/v1/jobs/j1':
            return FakeResponse(200, {'status': 'succeeded', 'result_id': 'r1', 'coverage': {'analysis_coverage': 'complete'}})
        if path.startswith('api/ai/v1/results/r1/findings'):
            return FakeResponse(200, {'findings': [finding(start=10)]})
        raise AssertionError(path)


class RunnerTests(unittest.TestCase):
    def runner(self, http, budget):
        return ev.Runner(http, 'https://gitviz.example', budget=budget, sleep=lambda s: None, timeout=5)

    def test_runs_a_case_and_scores_it(self):
        http = FakeHttp()
        score = self.runner(http, 1.0).run_case(case(), 5)
        self.assertEqual((score.status, score.matched_truths, score.findings), ('succeeded', 1, 1))
        self.assertAlmostEqual(score.cost, 0.07)
        self.assertIn(('POST', '/api/ai/v1/jobs'), http.calls)

    def test_budget_guard_stops_before_any_provider_transfer(self):
        http = FakeHttp(estimate='0.50')
        with self.assertRaises(ev.BudgetExceeded):
            self.runner(http, 0.30).run_case(case(), 5)
        self.assertNotIn(('POST', '/api/ai/v1/jobs'), http.calls)                               # 접수(=전송)하지 않았다
        runner = self.runner(FakeHttp(estimate='0.10'), 0.15)
        runner.run_case(case(), 5)                                                               # 0.07 사용
        runner.http = FakeHttp(estimate='0.10')
        with self.assertRaises(ev.BudgetExceeded):                                               # 남은 0.08 < 0.10
            runner.run_case(case('c2'), 5)

    def test_not_reviewable_input_is_reported_not_run(self):
        http = FakeHttp(reviewable=False)
        score = self.runner(http, 1.0).run_case(case(), 5)
        self.assertEqual((score.status, 'reviewable' in score.note), ('not_started', True))
        self.assertNotIn(('POST', '/api/ai/v1/jobs'), http.calls)


class Cli(unittest.TestCase):
    def test_live_needs_explicit_budget_and_never_runs_by_default(self):
        with patch('requests.Session') as session:
            self.assertEqual(ev.main(['--validate-manifest']), 0)
            self.assertEqual(ev.main([]), 0)                                                     # --live 없이는 호출하지 않는다
            self.assertEqual(ev.main(['--live']), 2)
            self.assertEqual(ev.main(['--live', '--budget', '0', '--base-url', 'x', '--cookie', 'a=b', '--repository-id', '1']), 2)
            self.assertEqual(ev.main(['--live', '--budget', '5']), 2)
            session.assert_not_called()

    def test_check_release_reports_open_items_with_a_failing_exit_code(self):
        self.assertEqual(ev.main(['--check-release']), 1)

    def test_invalid_manifest_blocks_a_live_run(self):
        import tempfile
        bad = manifest(tuning=[case('t', 'b', head_sha='x')])
        with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False, encoding='utf-8') as f:
            json.dump(bad, f)
        try:
            self.assertEqual(ev.main(['--manifest', f.name, '--live', '--budget', '5', '--base-url', 'x', '--cookie', 'a=b', '--repository-id', '1']), 2)
        finally:
            os.unlink(f.name)

    def test_final_set_runs_once(self):
        import tempfile
        done = manifest(final_run_done=True)
        with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False, encoding='utf-8') as f:
            json.dump(done, f)
        try:
            self.assertEqual(ev.main(['--manifest', f.name, '--live', '--set', 'final', '--budget', '5', '--base-url', 'x', '--cookie', 'a=b', '--repository-id', '1']), 2)
        finally:
            os.unlink(f.name)


if __name__ == '__main__':
    unittest.main(verbosity=2)
