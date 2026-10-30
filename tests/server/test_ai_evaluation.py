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


def finding(path='a.py', start=10, end=None, side='RIGHT', verdict='unverified', title='t'):
    end = start if end is None else end
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


COMPLETE = {'inventory_coverage': 'complete', 'selected_content_coverage': 'complete', 'analysis_coverage': 'complete', 'selected_files': 1, 'pr_total_files': 1, 'limitations': []}
JOB_OK = {'status': 'succeeded', 'error_code': None}


def scored(c, findings, cost=0.1, job=None, coverage=COMPLETE, source='result', exposure=None):
    return ev.score_case(c, job or JOB_OK, findings, cost, coverage=coverage, coverage_source=source, exposure=exposure)


def clean_pair():
    """끝까지 실행되고 모든 지적이 사람이 판정한 두 사례(결함 있음 + 결함 없는 대조)."""
    return [scored(case(), [finding(start=10, verdict='valid')]), scored(case('neg', truths=[], negative_control=True), [])]


class Scoring(unittest.TestCase):
    def test_line_overlap_with_tolerance_and_same_side(self):
        truth = {'path': 'a.py', 'side': 'RIGHT', 'start_line': 10, 'end_line': 12}
        for start, end, ok in ((10, 10, True), (12, 14, True), (14, 15, True), (15, 16, False), (6, 7, False), (8, 9, True)):
            self.assertEqual(ev.overlaps(truth, finding(start=start, end=end)['finding']), ok, (start, end))
        self.assertFalse(ev.overlaps(truth, finding(side='LEFT')['finding']))
        self.assertFalse(ev.overlaps(truth, finding(path='b.py')['finding']))

    def test_position_match_is_only_a_candidate_a_false_positive_is_not_a_discovery(self):
        c = case()                                                                                  # 정답: a.py RIGHT 10-12
        fp = scored(c, [finding(start=11, verdict='false_positive')])
        self.assertEqual((fp.matched_truths, fp.candidate_truths, len(fp.pending), fp.verdicts['false_positive']), (0, 1, 0, 1))   # 위치가 겹쳐도 발견이 아니다
        self.assertEqual(ev.summarize([fp])['recall_on_known_defects'], 0.0)                         # 재현율 100%로 나오지 않는다
        for verdict in ('unverified', 'uncertain'):
            pending = scored(c, [finding(start=11, verdict=verdict)])
            self.assertEqual((pending.matched_truths, pending.candidate_truths, len(pending.pending)), (0, 1, 1))   # 사람이 확인할 때까지 발견이 아니다
        valid = scored(c, [finding(start=11, verdict='valid')])
        self.assertEqual((valid.matched_truths, len(valid.pending)), (1, 0))
        mixed = scored(c, [finding(start=11, verdict='false_positive'), finding(start=12, verdict='valid')])
        self.assertEqual(mixed.matched_truths, 1)                                                    # 다른 지적이 같은 결함을 확인하면 발견

    def test_case_score_separates_confirmed_pending_and_unmatched(self):
        c = case(truths=[{'path': 'a.py', 'side': 'RIGHT', 'start_line': 10, 'end_line': 12}, {'path': 'a.py', 'side': 'RIGHT', 'start_line': 40, 'end_line': 41}])
        findings = [finding(start=11, end=11, verdict='valid'), finding(start=41, verdict='unverified'), finding(path='z.py', start=3, end=3, verdict='false_positive', title='x')]
        score = scored(c, findings, 0.07)
        self.assertEqual((score.truths, score.matched_truths, score.candidate_truths, score.findings), (2, 1, 2, 3))
        self.assertEqual([p['lines'] for p in score.pending], [[41, 41]])
        self.assertEqual(score.unmatched, [{'path': 'z.py', 'lines': [3, 3], 'side': 'RIGHT', 'title': 'x', 'human_validation': 'false_positive'}])
        self.assertEqual(score.verdicts, {'valid': 1, 'false_positive': 1, 'uncertain': 0, 'unverified': 1})

    def test_summary_counts_only_confirmed_discoveries_and_reports_the_rest_separately(self):
        a = scored(case(), [finding(start=10, verdict='valid'), finding(path='q.py', verdict='uncertain'), finding(path='r.py', verdict='unverified')])
        summary = ev.summarize([a, scored(case('neg', truths=[], negative_control=True), [])])
        self.assertEqual((summary['known_defects'], summary['known_defects_found'], summary['recall_on_known_defects']), (1, 1, 1.0))
        self.assertEqual((summary['human_verdicts']['valid'], summary['unjudged_findings'], summary['precision_among_human_judged']), (1, 2, 1.0))
        self.assertEqual(summary['verdict'], 'hold')                                                 # 미판정 지적이 남아 있다
        none = ev.summarize([scored(case('n', truths=[], negative_control=True), [])])
        self.assertEqual((none['recall_on_known_defects'], none['precision_among_human_judged']), (None, None))   # 분모가 없으면 0이 아니라 없음

    def test_verdict_is_held_whenever_the_evaluation_is_incomplete_or_unjudged(self):
        crit = {'min_recall': 0.5, 'min_precision': 0.5, 'max_cost_per_case_usd': 1}
        self.assertEqual(ev.summarize(clean_pair(), crit, expected_cases=2)['verdict'], 'pass')      # 모두 끝나고 판정된 경우에만 합격
        failed_control = [clean_pair()[0], ev.score_case(case('neg', truths=[], negative_control=True), {'status': 'failed', 'error_code': 'OUTPUT_VALIDATION_FAILED'}, [], 0.1)]
        s = ev.summarize(failed_control, crit, expected_cases=2)                                      # 수치 기준은 모두 넘지만 대조 사례가 실패했다
        self.assertEqual(s['verdict'], 'hold')
        self.assertTrue(any('neg' in r and '끝나지 않았' in r for r in s['hold_reasons']))
        s = ev.summarize(clean_pair()[:1], crit, expected_cases=2)                                    # 사례 누락
        self.assertEqual(s['verdict'], 'hold')
        self.assertTrue(any('1개만 실행' in r for r in s['hold_reasons']))
        s = ev.summarize(clean_pair(), crit, expected_cases=2, stopped='예산')                        # 예산 중단
        self.assertEqual((s['verdict'], s['stopped_early']), ('hold', '예산'))
        s = ev.summarize([scored(case(), [finding(start=10, verdict='valid')], job={'status': 'succeeded'}, coverage={**COMPLETE, 'analysis_coverage': 'partial'})], crit, expected_cases=1)
        self.assertEqual(s['verdict'], 'hold')                                                        # 분석 범위가 완전하지 않다
        s = ev.summarize([scored(case(), [finding(start=10, verdict='valid')], coverage={}, source='none')], crit, expected_cases=1)
        self.assertEqual(s['verdict'], 'hold')                                                        # 분석 범위를 확인하지 못했다
        control_with_finding = [clean_pair()[0], scored(case('neg', truths=[], negative_control=True), [finding(path='x.py', verdict='unverified')])]
        self.assertEqual(ev.summarize(control_with_finding, crit, expected_cases=2)['verdict'], 'hold')   # 대조 사례의 지적은 사람이 판정해야 한다
        self.assertEqual(ev.summarize([scored(case(), [finding(start=11, verdict='unverified')])], crit)['verdict'], 'hold')   # 확인 대기

    def test_uncertain_calls_hold_the_verdict_and_keep_their_exposure(self):
        job = {'status': 'failed', 'error_code': 'OUTCOME_UNCERTAIN', 'outcome_uncertain': True}
        u = ev.score_case(case('u'), job, [], 0.0, exposure=0.10)
        self.assertEqual((u.uncertain, u.cost, u.exposure), (True, 0.0, 0.10))
        s = ev.summarize([u], {'max_cost_per_case_usd': 1}, expected_cases=1)
        self.assertEqual((s['verdict'], s['uncertain_calls'], s['exposure_usd'], s['cost_usd']), ('hold', ['u'], 0.10, 0.0))
        self.assertTrue(ev.score_case(case('t'), {'status': 'failed', 'error_code': 'PROVIDER_TIMEOUT'}, [], 0.0).uncertain)

    def test_criteria_pass_fail_and_missing(self):
        s = ev.summarize(clean_pair(), None, expected_cases=2)
        self.assertEqual(s['verdict'], 'criteria_missing')                                            # 기준이 없으면 합격/불합격을 만들지 않는다
        self.assertEqual(ev.summarize(clean_pair(), {'min_recall': 1.1}, expected_cases=2)['verdict'], 'fail')
        self.assertEqual(ev.summarize(clean_pair(), {'max_cost_per_case_usd': 0.01}, expected_cases=2)['verdict'], 'fail')
        weak = [scored(case(), [], cost=0.0)]
        self.assertEqual(ev.summarize(weak, {'min_precision': 0.5}, expected_cases=1)['verdict'], 'fail')   # 판정된 지적이 없으면 기준을 채우지 못한 것
        self.assertEqual(ev.summarize(weak, {'min_recall': 0.1}, expected_cases=1)['verdict'], 'fail')

    def test_findings_on_negative_controls_are_reported_as_false_positive_candidates(self):
        control = scored(case('neg', truths=[], negative_control=True), [finding(path='x.py', verdict='false_positive'), finding(path='y.py', verdict='false_positive')])
        self.assertTrue(control.negative_control)
        summary = ev.summarize([clean_pair()[0], control], expected_cases=2)
        self.assertEqual((summary['negative_control_findings'], summary['false_positives']), (2, 2))
        self.assertIn('결함 없는 대조 사례에서 나온 지적 2건', ev.render_report('tuning', 'live', [clean_pair()[0], control], summary))

    def test_cost_criterion_uses_exposure_not_only_settled_cost(self):
        s = [scored(case(), [finding(start=10, verdict='valid')], cost=0.0, exposure=0.9)]
        self.assertEqual(ev.summarize(s, {'max_cost_per_case_usd': 0.5}, expected_cases=1)['verdict'], 'fail')

    def test_report_does_not_claim_correctness_and_lists_hold_reasons(self):
        s = [scored(case(), [finding(start=10, verdict='unverified')])]
        summary = ev.summarize(s, expected_cases=1)
        text = ev.render_report('tuning', 'live', s, summary)
        self.assertIn('구조 검증 통과는 내용이 옳다는 뜻이 아닙니다', text)
        self.assertIn('보류 사유', text)
        self.assertIn('**hold**', text)
        for bad in ('오류 없음', '문제 없음', '정확합니다'):
            self.assertNotIn(bad, text)


class FakeResponse:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        return self._body


class FakeHttp:
    """GitViz API를 흉내 낸다. 실제 API처럼 분석 범위는 **결과 API**에만 있고 작업 응답에는 입력 단계의 값만 있다."""

    def __init__(self, estimate='0.10', reviewable=True, spent=('0.00', '0.07'), job=None, remaining='20.0', findings=None, server=None, fail=None, manifest_hash='c' * 64, credential_version=1, fail_after_job_post=False, fail_all=False):
        self.calls, self.estimate, self.reviewable, self.spent, self.remaining = [], estimate, reviewable, list(spent), remaining
        self.server = server if server is not None else {'inputs': {}, 'jobs': {}, 'created': 0}      # 같은 Idempotency-Key는 같은 자원을 돌려준다(실제 서버 규칙)
        self.fail = fail                                                                           # 경로를 받아 True면 연결이 끊긴 것처럼 예외를 낸다
        self.manifest_hash, self.credential_version, self.fail_after_job_post, self.fail_all = manifest_hash, credential_version, fail_after_job_post, fail_all
        self.server.setdefault('bodies', {})
        self.cookies = type('Cookies', (), {'set': staticmethod(lambda *a, **k: None)})()
        self.job = job or {'status': 'succeeded', 'result_id': 'r1', 'error_code': None, 'coverage': {**COMPLETE, 'analysis_coverage': 'none'}}   # 입력 단계 coverage
        self.findings = findings if findings is not None else [finding(start=10, verdict='valid')]

    def get(self, url, **kw):
        return FakeResponse(200, {'token': 'csrf'})

    def request(self, method, url, json=None, headers=None, timeout=None):
        path = url.split('://', 1)[1].split('/', 1)[1]
        self.calls.append((method, '/' + path))
        if self.fail_all:
            raise ConnectionError('연결이 끊겼습니다')
        if path == 'api/ai/v1/usage':
            return FakeResponse(200, {'user_spent': self.spent.pop(0) if len(self.spent) > 1 else self.spent[0], 'user_remaining': self.remaining})
        key = (headers or {}).get('Idempotency-Key')
        if path == 'api/ai/v1/inputs' and method == 'POST':
            self.server['inputs'].setdefault(key, 'i1')
            return FakeResponse(202, {'input_id': self.server['inputs'][key]})
        if path == 'api/ai/v1/inputs/i1':
            return FakeResponse(200, {'status': 'ready', 'reviewable': self.reviewable, 'manifest_hash': self.manifest_hash, 'policy_version': 'p',
                                      'token_estimate': {'estimated_max_cost': self.estimate}, 'transfer': {'provider': 'anthropic', 'profile_id': 'x', 'credential_version': self.credential_version}})
        if path == 'api/ai/v1/jobs' and method == 'POST':
            if key in self.server['jobs'] and self.server['bodies'][key] != json:                           # 같은 key에 다른 요청 본문: 실제 서버는 409
                return FakeResponse(409, {'error': {'code': 'IDEMPOTENCY_CONFLICT'}})
            if key not in self.server['jobs']:
                self.server['created'] += 1
                self.server['jobs'][key] = f'j{self.server["created"]}'                                 # 새 key일 때만 새 작업이 생긴다
                self.server['bodies'][key] = json
            if self.fail_after_job_post:                                                               # 서버는 접수했지만 응답이 유실됐다
                raise ConnectionError('응답이 유실됐습니다')
            return FakeResponse(202, {'job_id': self.server['jobs'][key]})
        if path.startswith('api/ai/v1/jobs/') and method == 'GET':
            if self.fail and self.fail(path):
                raise ConnectionError('연결이 끊겼습니다')
            return FakeResponse(200, self.job)
        if path == 'api/ai/v1/results/r1':
            return FakeResponse(200, {'coverage': COMPLETE})
        if path.startswith('api/ai/v1/results/r1/findings'):
            return FakeResponse(200, {'findings': self.findings})
        raise AssertionError(path)


UNCERTAIN_JOB = {'status': 'failed', 'result_id': None, 'error_code': 'OUTCOME_UNCERTAIN', 'outcome_uncertain': True, 'coverage': {**COMPLETE, 'analysis_coverage': 'none'}}


class RunnerTests(unittest.TestCase):
    def runner(self, http, budget):
        return ev.Runner(http, 'https://gitviz.example', budget=budget, sleep=lambda s: None, timeout=5)

    def test_runs_a_case_and_reads_the_analysis_scope_from_the_result_api(self):
        http = FakeHttp()
        score = self.runner(http, 1.0).run_case(case(), 5)
        self.assertEqual((score.status, score.matched_truths, score.findings), ('succeeded', 1, 1))
        self.assertAlmostEqual(score.cost, 0.07)
        self.assertEqual((score.coverage['analysis_coverage'], score.coverage_source), ('complete', 'result'))   # 작업 응답의 입력 단계 값(none)이 아니다
        self.assertIn(('GET', '/api/ai/v1/results/r1'), http.calls)
        self.assertEqual((score.job_id, score.result_id), ('j1', 'r1'))

    def test_failed_job_reports_the_input_stage_coverage_as_such_not_as_analysis(self):
        http = FakeHttp(job={'status': 'failed', 'error_code': 'OUTPUT_VALIDATION_FAILED', 'coverage': {**COMPLETE, 'analysis_coverage': 'none'}})
        score = self.runner(http, 1.0).run_case(case(), 5)
        self.assertEqual(score.coverage_source, 'input')
        summary = ev.summarize([score], expected_cases=1)
        self.assertEqual(summary['analysis_coverage'], {'complete': 0, 'partial': 0, 'none': 0})      # 분석 범위로 집계하지 않는다
        self.assertEqual(summary['verdict'], 'hold')

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

    def test_an_uncertain_call_keeps_its_upper_bound_in_the_budget(self):
        runner = self.runner(FakeHttp(estimate='0.10', spent=('0.00',), job=UNCERTAIN_JOB), 0.15)
        first = runner.run_case(case('u1'), 5)
        self.assertEqual((first.uncertain, first.cost, first.exposure), (True, 0.0, 0.10))        # 정산액은 0이지만 비용이 나갔을 수 있다
        self.assertAlmostEqual(runner.held, 0.10)
        second_http = FakeHttp(estimate='0.10', spent=('0.00',), job=UNCERTAIN_JOB)
        runner.http = second_http
        with self.assertRaises(ev.BudgetExceeded):                                               # 예산 $0.15에서 $0.10 호출 두 개가 접수되면 안 된다
            runner.run_case(case('u2'), 5)
        self.assertNotIn(('POST', '/api/ai/v1/jobs'), second_http.calls)

    def test_accounts_remaining_monthly_limit_is_also_respected(self):
        http = FakeHttp(estimate='0.50', remaining='0.20')
        with self.assertRaises(ev.BudgetExceeded):
            self.runner(http, 5.0).run_case(case(), 5)
        self.assertNotIn(('POST', '/api/ai/v1/jobs'), http.calls)

    def test_a_job_that_never_finishes_is_treated_as_uncertain(self):
        http = FakeHttp(job={'status': 'running', 'coverage': COMPLETE})
        runner = ev.Runner(http, 'https://gitviz.example', budget=1.0, sleep=lambda s: None, timeout=0.05)
        score = runner.run_case(case(), 5)
        self.assertEqual((score.status, score.uncertain, score.exposure), ('timeout', True, 0.10))
        self.assertAlmostEqual(runner.held, 0.10)

    def test_not_reviewable_input_is_reported_not_run(self):
        http = FakeHttp(reviewable=False)
        score = self.runner(http, 1.0).run_case(case(), 5)
        self.assertEqual((score.status, 'reviewable' in score.note), ('not_started', True))
        self.assertNotIn(('POST', '/api/ai/v1/jobs'), http.calls)

    def test_rescore_rereads_human_verdicts_without_calling_the_provider(self):
        first = FakeHttp(findings=[finding(start=11, verdict='unverified')])
        score = self.runner(first, 1.0).run_case(case(), 5)
        self.assertEqual((score.matched_truths, len(score.pending)), (0, 1))
        report = {'set': 'tuning', 'summary': {'expected_cases': 1}, 'cases': [score.__dict__]}
        judged = FakeHttp(findings=[finding(start=11, verdict='valid')])                         # 사람이 화면에서 valid로 판정
        again = ev.rescore(report, [case()], judged, 'https://gitviz.example')
        self.assertEqual((again[0].matched_truths, len(again[0].pending), again[0].cost, again[0].coverage_source), (1, 0, score.cost, 'result'))
        self.assertTrue(all(c[0] == 'GET' for c in judged.calls))                               # 읽기만 한다: 접수·전송 없음
        self.assertEqual(ev.summarize(again, {'min_recall': 0.5}, expected_cases=1)['verdict'], 'pass')


class Recovery(unittest.TestCase):
    """중단된 실행: 시작 기록은 접수 전에 원자적으로 남고, 이어가기는 접수된 작업을 같은 key로 복구해 작업이 늘지 않는다."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()
        self.state = os.path.join(self.tmp, 'ai-eval-final-state.json')

    def runner(self, http, journal, budget=1.0, **kw):
        return ev.Runner(http, 'https://gitviz.example', budget=budget, sleep=lambda s: None, timeout=5, journal=journal, **kw)

    def test_journal_is_created_atomically_and_only_once(self):
        import threading
        first = ev.Journal.create(self.state, run_id='r1', set='final', budget=1.0, expected=1)
        self.assertTrue(os.path.exists(self.state))
        with self.assertRaises(FileExistsError):
            ev.Journal.create(self.state, run_id='r2', set='final', budget=1.0, expected=1)
        self.assertEqual(ev.Journal.load(self.state).data['run_id'], 'r1')
        other = os.path.join(self.tmp, 'race.json')
        winners = []

        def create(i):
            try:
                ev.Journal.create(other, run_id=f'r{i}', set='final', budget=1.0, expected=1)
                winners.append(i)
            except FileExistsError:
                pass
        threads = [threading.Thread(target=create, args=(i,)) for i in range(8)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(len(winners), 1)                                                       # 동시에 시작해도 하나만
        first.update('c1', status='started')
        self.assertFalse(os.path.exists(self.state + '.tmp'))                                  # 부분 기록이 남지 않는다
        self.assertEqual(ev.Journal.load(self.state).data['cases']['c1']['status'], 'started')

    def test_request_keys_are_deterministic_per_run_case_and_step(self):
        j1 = ev.Journal.create(self.state, run_id='run-a', set='final', budget=1.0, expected=1)
        runner = self.runner(FakeHttp(), j1)
        self.assertEqual(runner.key('c1', 'job'), runner.key('c1', 'job'))
        self.assertEqual(len({runner.key('c1', 'job'), runner.key('c1', 'input'), runner.key('c2', 'job')}), 3)
        j2 = ev.Journal.create(os.path.join(self.tmp, 'other.json'), run_id='run-b', set='final', budget=1.0, expected=1)
        self.assertNotEqual(self.runner(FakeHttp(), j2).key('c1', 'job'), runner.key('c1', 'job'))
        plain = ev.Runner(FakeHttp(), 'https://x', budget=1.0)
        self.assertNotEqual(plain.key('c1', 'job'), plain.key('c1', 'job'))                      # journal이 없으면 매번 새 key

    def test_connection_loss_after_acceptance_is_recovered_without_a_second_job(self):
        server = {'inputs': {}, 'jobs': {}, 'created': 0}
        journal = ev.Journal.create(self.state, run_id='run-1', set='final', budget=1.0, expected=1)
        lost = FakeHttp(server=server, fail=lambda path: True)                                   # 작업을 접수한 뒤 조회 연결이 끊긴다
        scores, stopped = ev.run_set(self.runner(lost, journal), [case('c1')], 5)
        self.assertEqual(scores, [])
        self.assertIn('--resume', stopped)
        self.assertEqual(server['created'], 1)
        state = ev.Journal.load(self.state).data
        self.assertEqual((state['cases']['c1']['status'], state['cases']['c1']['job_id'], state['complete']), ('interrupted', 'j1', False))
        self.assertAlmostEqual(state['held'], 0.10)                                              # 접수됐을 수 있는 작업의 상한은 예산에 남는다
        # 같은 명령을 다시 실행하지 않고 이어간다: 같은 key로 기존 작업을 복구한다
        resumed_journal = ev.Journal.load(self.state)
        runner = self.runner(FakeHttp(server=server), resumed_journal)
        self.assertAlmostEqual(runner.held, 0.10)
        scores, stopped = ev.run_set(runner, [case('c1')], 5)
        self.assertEqual((stopped, [s.status for s in scores], scores[0].job_id), (None, ['succeeded'], 'j1'))
        self.assertEqual(server['created'], 1)                                                   # 작업이 하나 더 생기지 않았다
        self.assertAlmostEqual(runner.held, 0.0)                                                 # 결과가 확인돼 잡아 둔 상한이 풀렸다
        final = ev.Journal.load(self.state).data
        self.assertEqual((final['complete'], final['cases']['c1']['status']), (True, 'done'))

    def test_recovery_is_not_blocked_by_the_budget_but_new_cases_still_are(self):
        server = {'inputs': {}, 'jobs': {}, 'created': 0}
        journal = ev.Journal.create(self.state, run_id='run-2', set='final', budget=0.15, expected=2)
        ev.run_set(self.runner(FakeHttp(server=server, fail=lambda p: True), journal, budget=0.15), [case('c1'), case('c2')], 5)
        runner = self.runner(FakeHttp(server=server, estimate='0.10'), ev.Journal.load(self.state), budget=0.15)
        scores, stopped = ev.run_set(runner, [case('c1'), case('c2')], 5)
        self.assertEqual(server['created'], 2)                           # c1은 복구(새 작업 아님), c2는 새로 접수
        self.assertEqual([s.case_id for s in scores], ['c1', 'c2'])
        self.assertEqual(stopped, None)

    def test_finished_cases_are_not_run_again_on_resume(self):
        server = {'inputs': {}, 'jobs': {}, 'created': 0}
        journal = ev.Journal.create(self.state, run_id='run-3', set='final', budget=1.0, expected=1)
        first = self.runner(FakeHttp(server=server), journal)
        done = first.run_case(case('c1'), 5)
        again_http = FakeHttp(server=server)
        again = self.runner(again_http, ev.Journal.load(self.state)).run_case(case('c1'), 5)
        self.assertEqual((again.status, again.job_id, again.cost), (done.status, done.job_id, done.cost))
        self.assertEqual(again_http.calls, [])                                                   # 호출 없음
        self.assertEqual(server['created'], 1)

    def test_unexpected_error_before_any_submission_holds_nothing(self):
        journal = ev.Journal.create(self.state, run_id='run-4', set='final', budget=1.0, expected=1)

        class Boom(FakeHttp):
            def request(self, method, url, **kw):
                if url.endswith('/api/ai/v1/inputs') and method == 'POST':
                    raise ConnectionError('입력 접수 전에 끊김')
                return super().request(method, url, **kw)
        runner = self.runner(Boom(), journal)
        scores, stopped = ev.run_set(runner, [case('c1')], 5)
        self.assertEqual((scores, runner.held), ([], 0.0))                                       # 작업이 접수됐을 가능성이 없으면 상한을 잡지 않는다
        self.assertIn('--resume', stopped)

    def new_server(self):
        return {'inputs': {}, 'jobs': {}, 'created': 0}

    def test_second_interruption_during_recovery_keeps_the_held_upper_bound(self):
        server = self.new_server()
        journal = ev.Journal.create(self.state, run_id='run-p1', set='final', budget=1.0, expected=1)
        ev.run_set(self.runner(FakeHttp(server=server, fail=lambda p: True), journal), [case('c1')], 5)      # 접수 뒤 끊김: 상한 $0.10
        self.assertAlmostEqual(ev.Journal.load(self.state).data['held'], 0.10)
        # 복구 중에 다시 모든 요청이 끊긴다
        again_journal = ev.Journal.load(self.state)
        runner = self.runner(FakeHttp(server=server, fail_all=True), again_journal)
        scores, stopped = ev.run_set(runner, [case('c1')], 5)
        saved = ev.Journal.load(self.state).data
        self.assertEqual((scores, saved['cases']['c1']['status'], saved['complete']), ([], 'interrupted', False))
        self.assertAlmostEqual(saved['held'], 0.10)                                                          # $0.10 → $0가 되지 않는다
        self.assertAlmostEqual(saved['cases']['c1']['held'], 0.10)
        self.assertAlmostEqual(runner.held, 0.10)                                                            # 중복해서 더하지도 않는다
        self.assertEqual(saved['cases']['c1']['job_id'], 'j1')                                               # 접수 상태 유지
        # 세 번째 시도에서 복구되면 그때 상한이 풀린다
        final = self.runner(FakeHttp(server=server), ev.Journal.load(self.state))
        scores, stopped = ev.run_set(final, [case('c1')], 5)
        self.assertEqual((stopped, scores[0].status, server['created']), (None, 'succeeded', 1))
        self.assertAlmostEqual(final.held, 0.0)

    def test_recovery_checks_the_existing_job_and_never_resends_with_a_new_consent(self):
        server = self.new_server()
        journal = ev.Journal.create(self.state, run_id='run-p2', set='final', budget=1.0, expected=1)
        ev.run_set(self.runner(FakeHttp(server=server, fail=lambda p: True), journal), [case('c1')], 5)
        # 중단 사이 key·모델이 바뀌어 새 미리보기의 동의(manifest·key 버전)가 달라졌다
        changed = FakeHttp(server=server, manifest_hash='d' * 64, credential_version=2)
        runner = self.runner(changed, ev.Journal.load(self.state))
        scores, stopped = ev.run_set(runner, [case('c1')], 5)
        self.assertEqual((stopped, [s.status for s in scores], scores[0].job_id), (None, ['succeeded'], 'j1'))
        self.assertEqual(server['created'], 1)
        self.assertNotIn(('POST', '/api/ai/v1/jobs'), changed.calls)                                         # 저장된 job_id로 조회만 했다(409를 만들지 않는다)
        self.assertNotIn(('POST', '/api/ai/v1/inputs'), changed.calls)
        self.assertTrue(ev.Journal.load(self.state).data['complete'])

    def test_when_acceptance_is_unknown_the_original_request_body_is_resent_with_the_same_key(self):
        server = self.new_server()
        journal = ev.Journal.create(self.state, run_id='run-p3', set='final', budget=1.0, expected=1)
        lost = FakeHttp(server=server, fail_after_job_post=True)                                               # 서버는 접수했지만 응답을 못 받았다
        scores, stopped = ev.run_set(self.runner(lost, journal), [case('c1')], 5)
        self.assertEqual((scores, server['created']), ([], 1))
        saved = ev.Journal.load(self.state).data['cases']['c1']
        self.assertEqual((saved['status'], saved.get('job_id'), saved['job_body']['consent']['manifest_hash']), ('interrupted', None, 'c' * 64))
        changed = FakeHttp(server=server, manifest_hash='d' * 64, credential_version=2)                       # 이어가는 사이 동의가 달라졌다
        scores, stopped = ev.run_set(self.runner(changed, ev.Journal.load(self.state)), [case('c1')], 5)
        self.assertEqual((stopped, [s.status for s in scores], server['created']), (None, ['succeeded'], 1))   # 최초 본문 그대로 재전송 → 같은 작업
        self.assertNotIn(('GET', '/api/ai/v1/inputs/i1'), changed.calls)                                       # 새 미리보기를 쓰지 않는다
        posts = [c for c in changed.calls if c == ('POST', '/api/ai/v1/jobs')]
        self.assertEqual(len(posts), 1)

    def test_unrecoverable_job_is_not_recorded_as_complete_and_keeps_its_hold(self):
        server = self.new_server()
        journal = ev.Journal.create(self.state, run_id='run-p4', set='final', budget=1.0, expected=1)
        ev.run_set(self.runner(FakeHttp(server=server, fail_after_job_post=True), journal), [case('c1')], 5)   # 접수 여부 불명(job_id 없음)
        deny = FakeHttp(server=server)                                                                          # 재전송이 거절되는 경우(서버가 접수 기록을 잃은 것처럼)
        original = deny.request

        def reject(method, url, **kw):
            if url.endswith('/api/ai/v1/jobs') and method == 'POST':
                deny.calls.append((method, '/api/ai/v1/jobs'))
                return FakeResponse(409, {'error': {'code': 'IDEMPOTENCY_CONFLICT'}})
            return original(method, url, **kw)
        deny.request = reject
        runner = self.runner(deny, ev.Journal.load(self.state))
        scores, stopped = ev.run_set(runner, [case('c1')], 5)
        saved = ev.Journal.load(self.state).data
        self.assertEqual((scores, saved['complete'], saved['cases']['c1']['status']), ([], False, 'interrupted'))      # 완료로 기록하지 않는다
        self.assertAlmostEqual(saved['held'], 0.10)                                                             # 상한 유지
        self.assertIn('확인', stopped)

    def test_a_job_that_vanished_is_unresolved_not_complete(self):
        server = self.new_server()
        journal = ev.Journal.create(self.state, run_id='run-p5', set='final', budget=1.0, expected=1)
        ev.run_set(self.runner(FakeHttp(server=server, fail=lambda p: True), journal), [case('c1')], 5)

        class Gone(FakeHttp):
            def request(self, method, url, **kw):
                if '/api/ai/v1/jobs/' in url and method == 'GET':
                    return FakeResponse(404, {'error': {'code': 'RESOURCE_NOT_FOUND'}})
                return super().request(method, url, **kw)
        runner = self.runner(Gone(server=server), ev.Journal.load(self.state))
        scores, stopped = ev.run_set(runner, [case('c1')], 5)
        saved = ev.Journal.load(self.state).data
        self.assertEqual((scores, saved['complete'], saved['cases']['c1']['status']), ([], False, 'interrupted'))
        self.assertAlmostEqual(saved['held'], 0.10)

    def test_totals_are_derived_from_case_records_so_a_crash_right_after_done_loses_no_cost(self):
        """사례 완료와 비용 합계가 따로 저장되면 그 사이 종료 시 합계가 이전 값으로 남는다. 합계는 사례 기록에서 계산한다."""
        class Dies(ev.Journal):
            def save(self):
                super().save()
                if any(c.get('status') == 'done' for c in self.data['cases'].values()):
                    raise KeyboardInterrupt('done을 저장한 직후 프로세스 종료')

        server = self.new_server()
        data = ev.Journal.create(self.state, run_id='run-crash', set='final', budget=0.15, expected=2).data
        journal = Dies(self.state, data)
        runner = self.runner(FakeHttp(server=server, estimate='0.10', spent=('0.00',), job=UNCERTAIN_JOB), journal, budget=0.15)
        with self.assertRaises(KeyboardInterrupt):
            runner.run_case(case('u1'), 5)                                                       # 불명 호출($0.10)을 완료로 저장한 직후 종료
        saved = ev.Journal.load(self.state)
        self.assertEqual(saved.data['cases']['u1']['status'], 'done')
        self.assertAlmostEqual(saved.data['held'], 0.10)                                          # 합계가 완료 기록과 함께 저장돼 있다
        resumed = self.runner(FakeHttp(server=server, estimate='0.10'), saved, budget=0.15)
        self.assertAlmostEqual(resumed.committed, 0.10)
        second = FakeHttp(server=server, estimate='0.10')
        resumed.http = second
        with self.assertRaises(ev.BudgetExceeded):                                                # 예산 $0.15에서 $0.10 호출이 두 번 접수되지 않는다
            resumed.run_case(case('u2'), 5)
        self.assertNotIn(('POST', '/api/ai/v1/jobs'), second.calls)

    def test_stale_stored_totals_are_ignored_and_recomputed_from_the_cases(self):
        server = self.new_server()
        journal = ev.Journal.create(self.state, run_id='run-stale', set='final', budget=1.0, expected=2)
        runner = self.runner(FakeHttp(server=server), journal)
        done = runner.run_case(case('c1'), 5)
        self.assertAlmostEqual(runner.spent, 0.07)
        stored = json.load(open(self.state, encoding='utf-8'))
        stored['spent'], stored['held'] = 0.0, 0.0                                                # 합계만 이전 값으로 남은 상황
        json.dump(stored, open(self.state, 'w', encoding='utf-8'))
        again = self.runner(FakeHttp(server=server), ev.Journal.load(self.state))
        self.assertAlmostEqual(again.spent, 0.07)                                                 # 완료 사례의 정산액이 되살아난다
        self.assertEqual(again.run_case(case('c1'), 5).cost, done.cost)

    def test_each_case_record_holds_both_the_completion_and_its_cost_in_one_write(self):
        saves = []

        class Spy(ev.Journal):
            def save(self):
                super().save()
                saves.append(json.loads(json.dumps(self.data)))
        server = self.new_server()
        data = ev.Journal.create(self.state, run_id='run-one', set='final', budget=1.0, expected=1).data
        self.runner(FakeHttp(server=server), Spy(self.state, data)).run_case(case('c1'), 5)
        done_saves = [d for d in saves if d['cases']['c1']['status'] == 'done']
        self.assertEqual(len(done_saves), 1)
        self.assertAlmostEqual(done_saves[0]['cases']['c1']['settled'], 0.07)
        self.assertAlmostEqual(done_saves[0]['spent'], 0.07)                                      # 완료가 저장된 그 시점의 합계가 이미 맞다
        self.assertEqual(saves[-1]['cases']['c1']['status'], 'done')                              # 완료 저장이 마지막 쓰기다(그 뒤에 합계만 따로 쓰지 않는다)

    def args(self, extra=()):
        return ['--manifest', self.manifest_path, '--live', '--set', 'final', '--budget', '5', '--base-url', 'https://x', '--cookie', 'a=b',
                '--repository-id', '1', '--out', self.tmp, *extra]

    def test_interrupted_final_run_cannot_be_started_again_but_can_be_resumed(self):
        import contextlib
        import io
        import tempfile
        m = manifest(final=[case('f1', 'c')])
        f = tempfile.NamedTemporaryFile('w', suffix='.json', delete=False, encoding='utf-8')
        json.dump(m, f)
        f.close()
        self.addCleanup(os.unlink, f.name)
        self.manifest_path = f.name
        server = {'inputs': {}, 'jobs': {}, 'created': 0}
        flaky = FakeHttp(server=server, fail=lambda path: True)
        with patch('requests.Session', return_value=flaky), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(ev.main(self.args()), 3)                                            # 접수 뒤 조회 연결이 끊겨 비정상 중단
        self.assertTrue(os.path.exists(os.path.join(self.tmp, 'ai-eval-final-state.json')))      # 시작 기록은 중단돼도 남는다
        self.assertFalse(os.path.exists(os.path.join(self.tmp, 'FINAL_RUN_DONE.json')))          # 완료 표시는 아직 없다
        self.assertEqual(server['created'], 1)
        again = FakeHttp(server=server)
        with patch('requests.Session', return_value=again), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(ev.main(self.args()), 2)                                            # 같은 명령은 다시 실행되지 않는다
            self.assertEqual(ev.main(self.args(['--fresh'])), 2)                                 # 최종 세트는 새로 시작할 수 없다
        self.assertEqual((again.calls, server['created']), ([], 1))
        with patch('requests.Session', return_value=FakeHttp(server=server)), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(ev.main(self.args(['--resume'])), 0)                                # 이어가기: 기존 작업 복구
        self.assertEqual(server['created'], 1)
        self.assertTrue(os.path.exists(os.path.join(self.tmp, 'FINAL_RUN_DONE.json')))
        with patch('requests.Session', return_value=FakeHttp(server=server)) as session, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(ev.main(self.args(['--resume'])), 2)                                # 완료 뒤에는 이어갈 것도 없다
            self.assertEqual(ev.main(self.args()), 2)
        self.assertEqual(server['created'], 1)

    def test_resume_needs_a_previous_run_and_tuning_can_start_fresh(self):
        import contextlib
        import io
        import tempfile
        f = tempfile.NamedTemporaryFile('w', suffix='.json', delete=False, encoding='utf-8')
        json.dump(manifest(tuning=[case('t1', 'b')]), f)
        f.close()
        self.addCleanup(os.unlink, f.name)
        base = ['--manifest', f.name, '--live', '--set', 'tuning', '--budget', '5', '--base-url', 'https://x', '--cookie', 'a=b', '--repository-id', '1', '--out', self.tmp]
        server = {'inputs': {}, 'jobs': {}, 'created': 0}
        with patch('requests.Session', return_value=FakeHttp(server=server)), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(ev.main([*base, '--resume']), 2)                                    # 이어갈 기록이 없다
            self.assertEqual(ev.main(base), 0)
            self.assertEqual(ev.main(base), 2)                                                   # 기록이 있으면 같은 명령은 거부
            self.assertEqual(ev.main([*base, '--fresh']), 0)                                     # 조정 세트는 새로 시작할 수 있다(이전 기록은 보관)
        self.assertTrue([n for n in os.listdir(self.tmp) if n.endswith('.old')])


class Cli(unittest.TestCase):
    def test_live_needs_explicit_budget_and_never_runs_by_default(self):
        with patch('requests.Session') as session:
            self.assertEqual(ev.main(['--validate-manifest']), 0)
            self.assertEqual(ev.main([]), 0)                                                     # --live 없이는 호출하지 않는다
            self.assertEqual(ev.main(['--live']), 2)
            self.assertEqual(ev.main(['--live', '--budget', '0', '--base-url', 'x', '--cookie', 'a=b', '--repository-id', '1']), 2)
            self.assertEqual(ev.main(['--live', '--budget', '5']), 2)
            self.assertEqual(ev.main(['--rescore', 'x.json']), 2)
            session.assert_not_called()

    def test_check_release_reports_open_items_with_a_failing_exit_code(self):
        self.assertEqual(ev.main(['--check-release']), 1)

    def manifest_file(self, data):
        import tempfile
        f = tempfile.NamedTemporaryFile('w', suffix='.json', delete=False, encoding='utf-8')
        json.dump(data, f)
        f.close()
        self.addCleanup(os.unlink, f.name)
        return f.name

    def test_invalid_manifest_blocks_a_live_run(self):
        path = self.manifest_file(manifest(tuning=[case('t', 'b', head_sha='x')]))
        self.assertEqual(ev.main(['--manifest', path, '--live', '--budget', '5', '--base-url', 'x', '--cookie', 'a=b', '--repository-id', '1']), 2)

    def test_final_set_runs_once(self):
        import tempfile
        path = self.manifest_file(manifest(final_run_done=True))
        args = ['--manifest', path, '--live', '--set', 'final', '--budget', '5', '--base-url', 'x', '--cookie', 'a=b', '--repository-id', '1']
        self.assertEqual(ev.main(args), 2)
        marker_dir = tempfile.mkdtemp()                                                          # 표시 파일만 있어도 다시 실행하지 않는다
        open(os.path.join(marker_dir, 'FINAL_RUN_DONE.json'), 'w').write('{}')
        self.assertEqual(ev.main(['--manifest', self.manifest_file(manifest()), '--live', '--set', 'final', '--budget', '5', '--base-url', 'x', '--cookie', 'a=b',
                                  '--repository-id', '1', '--out', marker_dir]), 2)


if __name__ == '__main__':
    unittest.main(verbosity=2)
