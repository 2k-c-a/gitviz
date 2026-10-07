"""AI 리뷰 평가 도구. fake provider 검증(verify_ai_mvp_integration.py)과 **실제 모델 평가는 완전히 분리**한다.

    python scripts/evaluate_ai_review.py --validate-manifest                      # 평가 세트 검증만(네트워크 없음)
    python scripts/evaluate_ai_review.py --live --set tuning --budget 5 --base-url https://… --cookie "gitviz_session=…"

- 실제 호출은 `--live`와 `--budget`(USD)을 **둘 다** 줘야 하고, 일반 CI에서는 실행하지 않는다. 호출 전 미리보기의 예상 비용 상한이
  남은 예산을 넘으면 그 사례부터 중단한다. 평가 계정은 평가자 본인의 API key를 미리 등록해 둔 사용자다(이 도구는 key를 다루지 않는다).
- 공개 저장소이거나 사용 허락을 기록한 자료만 쓴다(`manifest`의 visibility/permission 검사). 조정(tuning) 세트와 최종(final) 세트는
  겹치면 안 된다. 최종 세트는 프롬프트·검증기를 조정한 뒤 한 번만 돌려 보고한다.
- 정답(truths)은 사람이 붙인다. 모델이 낸 지적 중 정답과 겹치지 않은 것은 자동으로 오탐이라고 하지 않고 **사람이 판정할 목록**으로
  내보낸다(기존 피드백 verdict가 있으면 그것을 쓴다). 합의한 통과 기준(criteria)이 비어 있으면 합격/불합격을 계산하지 않는다.
- 보고서는 결과·비용·coverage·유효 지적·오탐·보류(불확실)·미판정을 따로 센다. 구조 검증 통과는 정확도가 아니다.
"""
import argparse
import json
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / 'scripts' / 'fixtures' / 'ai' / 'evaluation_manifest.json'
SHA_RE = re.compile(r'^[0-9a-f]{40}$')
LINE_TOLERANCE = 2          # 정답 줄 범위와 모델 지적 줄 범위가 이만큼 어긋나도 같은 위치로 본다


# ── 평가 세트 ──────────────────────────────────────────────────────────────

def load_manifest(path: Path = MANIFEST) -> dict:
    return json.loads(Path(path).read_text(encoding='utf-8'))


def validate_manifest(manifest: dict) -> list[str]:
    """평가 세트의 문제 목록. 비어 있어야 실행할 수 있다."""
    problems = []
    sets = manifest.get('sets') or {}
    if set(sets) != {'tuning', 'final'}:
        problems.append("sets에는 'tuning'과 'final'이 모두 있어야 합니다(조정·최종 분리).")
    seen_ids, seen_heads = {}, {}
    for name, cases in sets.items():
        for case in cases:
            cid = case.get('id') or '(id 없음)'
            where = f'{name}/{cid}'
            if cid in seen_ids:
                problems.append(f'{where}: id가 {seen_ids[cid]}와 겹칩니다(세트 분리 위반).')
            seen_ids[cid] = name
            head = (case.get('repo_full_name'), case.get('head_sha'))
            if head in seen_heads and seen_heads[head] != name:
                problems.append(f'{where}: 같은 저장소·head SHA가 {seen_heads[head]} 세트에도 있습니다(세트 분리 위반).')
            seen_heads[head] = name
            for field_name in ('repo_full_name', 'pr_number', 'selected_paths', 'labeled_by'):
                if not case.get(field_name):
                    problems.append(f'{where}: {field_name}가 없습니다.')
            for sha in ('base_sha', 'head_sha'):
                if not SHA_RE.match(case.get(sha) or ''):
                    problems.append(f'{where}: {sha}는 고정된 40자 소문자 hex여야 합니다.')
            if case.get('visibility') != 'public' and not case.get('permission_note'):
                problems.append(f'{where}: 비공개 자료는 사용 허락(permission_note)을 기록해야 합니다. 공개 또는 허락된 자료만 씁니다.')
            if case.get('visibility') == 'public' and not case.get('license'):
                problems.append(f'{where}: 공개 자료의 license를 기록하세요.')
            if case.get('synthetic') and name == 'final':
                problems.append(f'{where}: 합성 자료는 최종 세트에 쓸 수 없습니다(하네스 점검용).')
            truths = case.get('truths')
            if truths is None or (not truths and not case.get('negative_control')):
                problems.append(f'{where}: 사람 정답(truths)이 없습니다. 결함이 없는 사례라면 negative_control=true로 표시하세요.')
            for t in truths or []:
                if not (t.get('path') and t.get('side') in ('LEFT', 'RIGHT') and isinstance(t.get('start_line'), int) and isinstance(t.get('end_line'), int)
                        and 1 <= t['start_line'] <= t['end_line']):
                    problems.append(f'{where}: truths 항목이 올바르지 않습니다: {t}')
    return problems


def release_blockers(manifest: dict) -> list[str]:
    """MVP 출시 전에 사람이 채워야 하는 항목. 하나라도 남아 있으면 실제 모델 평가가 끝난 것이 아니다."""
    out = []
    final = cases_of(manifest, 'final')
    if not [c for c in final if not c.get('synthetic')]:
        out.append('final 세트에 실제(비합성) 사례가 없습니다. 공개 저장소의 고정 SHA와 사람 정답이 필요합니다.')
    elif not any(c.get('negative_control') for c in final):
        out.append('final 세트에 결함이 없는 negative_control 사례가 없습니다.')
    crit = manifest.get('criteria') or {}
    if all(crit.get(k) is None for k in ('min_recall', 'min_precision', 'max_cost_per_case_usd')):
        out.append('통과 기준(criteria)이 합의되지 않았습니다.')
    if manifest.get('status') == 'needs_human_curation':
        out.append("manifest status가 'needs_human_curation'입니다.")
    if not manifest.get('final_run_done'):
        out.append('final 세트의 실제 모델 평가가 아직 실행되지 않았습니다.')
    return out


def cases_of(manifest: dict, set_name: str) -> list[dict]:
    return list((manifest.get('sets') or {}).get(set_name) or [])


# ── 채점 ───────────────────────────────────────────────────────────────────

def overlaps(truth: dict, finding: dict, tolerance: int = LINE_TOLERANCE) -> bool:
    loc = finding['location']
    return (truth['path'] == loc['path'] and truth['side'] == loc['side']
            and loc['start_line'] <= truth['end_line'] + tolerance and truth['start_line'] <= loc['end_line'] + tolerance)


@dataclass
class CaseScore:
    case_id: str
    status: str
    error_code: str | None = None
    cost: float = 0.0
    coverage: dict = field(default_factory=dict)
    findings: int = 0
    truths: int = 0
    matched_truths: int = 0            # 모델이 찾은 알려진 결함(재현율의 분자)
    matched_findings: int = 0          # 정답과 겹친 지적
    unmatched: list = field(default_factory=list)         # 정답과 겹치지 않은 지적(사람이 판정)
    verdicts: dict = field(default_factory=lambda: {'valid': 0, 'false_positive': 0, 'uncertain': 0, 'unverified': 0})
    note: str = ''


def score_case(case: dict, job: dict, findings: list[dict], cost: float) -> CaseScore:
    truths = case.get('truths') or []
    score = CaseScore(case_id=case['id'], status=job.get('status', 'unknown'), error_code=job.get('error_code'), cost=cost,
                      coverage=job.get('coverage') or {}, findings=len(findings), truths=len(truths))
    matched = set()
    for item in findings:
        finding = item['finding']
        hits = [i for i, t in enumerate(truths) if overlaps(t, finding)]
        verdict = finding.get('human_validation') or 'unverified'
        score.verdicts[verdict if verdict in score.verdicts else 'unverified'] += 1
        if hits:
            score.matched_findings += 1
            matched.update(hits)
        else:
            score.unmatched.append({'path': finding['location']['path'], 'lines': [finding['location']['start_line'], finding['location']['end_line']],
                                    'side': finding['location']['side'], 'title': finding['title'], 'human_validation': verdict})
    score.matched_truths = len(matched)
    return score


def summarize(scores: list[CaseScore], criteria: dict | None = None) -> dict:
    total_truths = sum(s.truths for s in scores)
    found = sum(s.matched_truths for s in scores)
    findings = sum(s.findings for s in scores)
    verdicts = {k: sum(s.verdicts[k] for s in scores) for k in ('valid', 'false_positive', 'uncertain', 'unverified')}
    judged = verdicts['valid'] + verdicts['false_positive']
    summary = {
        'cases': len(scores), 'succeeded': sum(1 for s in scores if s.status == 'succeeded'), 'failed': [s.case_id for s in scores if s.status != 'succeeded'],
        'cost_usd': round(sum(s.cost for s in scores), 6), 'findings': findings,
        'known_defects': total_truths, 'known_defects_found': found,
        'recall_on_known_defects': (found / total_truths) if total_truths else None,
        'human_verdicts': verdicts,
        'precision_among_human_judged': (verdicts['valid'] / judged) if judged else None,       # 판정된 것만 센다(미판정은 분모에서 제외하되 따로 보고)
        'unjudged_findings': verdicts['unverified'] + verdicts['uncertain'],
        'unmatched_findings_to_review': sum(len(s.unmatched) for s in scores),
        'analysis_coverage': {k: sum(1 for s in scores if s.coverage.get('analysis_coverage') == k) for k in ('complete', 'partial', 'none')},
    }
    crit = criteria or {}
    if all(crit.get(k) is None for k in ('min_recall', 'min_precision', 'max_cost_per_case_usd')):
        summary['verdict'] = '합의한 통과 기준이 없어 합격/불합격을 계산하지 않았습니다(criteria를 먼저 합의하세요).'
    else:
        checks = []
        if crit.get('min_recall') is not None:
            checks.append(summary['recall_on_known_defects'] is not None and summary['recall_on_known_defects'] >= crit['min_recall'])
        if crit.get('min_precision') is not None:
            checks.append(summary['precision_among_human_judged'] is not None and summary['precision_among_human_judged'] >= crit['min_precision'])
        if crit.get('max_cost_per_case_usd') is not None:
            checks.append(not scores or summary['cost_usd'] / len(scores) <= crit['max_cost_per_case_usd'])
        summary['verdict'] = 'pass' if all(checks) else 'fail'
    return summary


def render_report(set_name: str, mode: str, scores: list[CaseScore], summary: dict) -> str:
    lines = [f'# AI 리뷰 평가 보고 — {set_name} 세트 ({mode})', '',
             f'- 사례 {summary["cases"]}개, 성공 {summary["succeeded"]}개, 비용 ${summary["cost_usd"]}', '',
             f'- 알려진 결함 {summary["known_defects"]}개 중 {summary["known_defects_found"]}개를 찾음'
             f' (재현율 {summary["recall_on_known_defects"] if summary["recall_on_known_defects"] is not None else "—"})',
             f'- 지적 {summary["findings"]}건: 사람 판정 {summary["human_verdicts"]} / 미판정·보류 {summary["unjudged_findings"]}건 /'
             f' 정답과 겹치지 않아 사람이 봐야 할 지적 {summary["unmatched_findings_to_review"]}건',
             f'- 판정된 지적 중 유효 비율: {summary["precision_among_human_judged"] if summary["precision_among_human_judged"] is not None else "판정 없음"}',
             f'- 분석 범위: {summary["analysis_coverage"]}', f'- 판정: {summary["verdict"]}', '',
             '| 사례 | 상태 | 지적 | 정답 | 찾은 정답 | 비용 | 분석 범위 |', '|---|---|---|---|---|---|---|']
    for s in scores:
        lines.append(f'| {s.case_id} | {s.status}{" / " + s.error_code if s.error_code else ""} | {s.findings} | {s.truths} | {s.matched_truths} | '
                     f'${s.cost:.4f} | {s.coverage.get("analysis_coverage", "—")} |')
    lines += ['', '구조 검증 통과는 내용이 옳다는 뜻이 아닙니다. 위 수치는 사람이 붙인 정답과 판정에만 근거합니다.']
    return '\n'.join(lines)


# ── 실행(HTTP) ─────────────────────────────────────────────────────────────

class BudgetExceeded(Exception):
    pass


class Runner:
    """배포된(또는 통합 검증용) GitViz에 HTTP로 접속해 사례를 실행한다. http는 쿠키·Origin이 설정된 requests.Session 같은 객체다."""

    def __init__(self, http, base_url: str, *, budget: float, csrf_token: str | None = None, poll_seconds: float = 3.0, timeout: float = 600.0,
                 sleep=time.sleep):
        self.http, self.base, self.budget, self.spent = http, base_url.rstrip('/'), float(budget), 0.0
        self.csrf, self.poll, self.timeout, self.sleep = csrf_token, poll_seconds, timeout, sleep

    def call(self, method, path, body=None, key=None):
        headers = {'Origin': self.base}
        if method != 'GET':
            if not self.csrf:
                self.csrf = self.http.get(f'{self.base}/api/auth/csrf', timeout=15).json()['token']
            headers['X-CSRF-Token'] = self.csrf
        if key:
            headers['Idempotency-Key'] = key
        return self.http.request(method, f'{self.base}{path}', json=body, headers=headers, timeout=60)

    def run_case(self, case: dict, repository_id: int, kind: str = 'review') -> CaseScore:
        import uuid
        usage_before = float(self.call('GET', '/api/ai/v1/usage').json()['user_spent'])
        created = self.call('POST', '/api/ai/v1/inputs', {'repository_id': repository_id, 'pr_number': case['pr_number'], 'expected_base_sha': case['base_sha'],
                                                           'expected_head_sha': case['head_sha'], 'selected_paths': case['selected_paths']}, uuid.uuid4().hex)
        if created.status_code not in (200, 202):
            return CaseScore(case['id'], 'not_started', created.json().get('error', {}).get('code'), note='입력을 만들지 못했습니다')
        input_id = created.json()['input_id']
        preview = self._wait(lambda: (lambda p: p if p['status'] != 'building' else None)(self.call('GET', f'/api/ai/v1/inputs/{input_id}').json()))
        if preview['status'] != 'ready' or not preview.get('reviewable'):
            return CaseScore(case['id'], 'not_started', preview['status'], note='실행할 수 있는 입력이 아닙니다(reviewable=false 포함)')
        estimate = float((preview.get('token_estimate') or {}).get('estimated_max_cost') or 0)
        if self.spent + estimate > self.budget:
            raise BudgetExceeded(f'{case["id"]}: 예상 비용 상한 ${estimate:.4f}가 남은 예산 ${self.budget - self.spent:.4f}을 넘습니다.')
        t = preview['transfer']
        consent = {'input_id': input_id, 'manifest_hash': preview['manifest_hash'], 'provider': t['provider'], 'profile_id': t['profile_id'],
                   'credential_version': t['credential_version'], 'policy_version': preview['policy_version']}
        job = self.call('POST', '/api/ai/v1/jobs', {'kind': kind, 'consent': consent, 'force_regenerate': False}, uuid.uuid4().hex)
        if job.status_code not in (200, 202):
            return CaseScore(case['id'], 'not_started', job.json().get('error', {}).get('code'), note='작업을 접수하지 못했습니다')
        job_id = job.json()['job_id']
        done = self._wait(lambda: (lambda j: j if j['status'] in ('succeeded', 'failed', 'cancelled') else None)(self.call('GET', f'/api/ai/v1/jobs/{job_id}').json()))
        findings = []
        if done['status'] == 'succeeded' and kind == 'review':
            findings = self.call('GET', f'/api/ai/v1/results/{done["result_id"]}/findings?limit=50').json()['findings']
        cost = max(0.0, float(self.call('GET', '/api/ai/v1/usage').json()['user_spent']) - usage_before)
        self.spent += cost
        return score_case(case, done, findings, cost)

    def _wait(self, condition):
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            value = condition()
            if value:
                return value
            self.sleep(self.poll)
        raise TimeoutError('시간 안에 끝나지 않았습니다.')


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--manifest', default=str(MANIFEST))
    ap.add_argument('--validate-manifest', action='store_true')
    ap.add_argument('--check-release', action='store_true', help='출시 전에 사람이 채워야 할 항목을 보고한다(남아 있으면 종료 코드 1)')
    ap.add_argument('--live', action='store_true', help='실제 AI 제공자를 호출한다(별도 예산 필요, 일반 CI에서는 쓰지 않는다)')
    ap.add_argument('--set', choices=('tuning', 'final'), default='tuning')
    ap.add_argument('--budget', type=float, help='이번 실행에 쓸 수 있는 최대 비용(USD)')
    ap.add_argument('--base-url')
    ap.add_argument('--cookie', help='평가자 세션 쿠키(gitviz_session=…). 화면에 출력하지 않는다')
    ap.add_argument('--repository-id', type=int, help='GitViz의 저장소 id(평가 대상 저장소를 미리 등록해 둔 것)')
    ap.add_argument('--out', default=str(ROOT / 'reports'))
    args = ap.parse_args(argv)
    manifest = load_manifest(Path(args.manifest))
    problems = validate_manifest(manifest)
    if args.check_release:
        blockers = release_blockers(manifest)
        print(*(['출시 전 확인이 필요한 항목:'] + [f'- {b}' for b in blockers] if blockers else ['남은 항목 없음']), sep=chr(10))
        return 1 if blockers or problems else 0
    if args.validate_manifest or not args.live:
        for p in problems:
            print('문제:', p)
        print(f'평가 세트 검증: {"통과" if not problems else f"{len(problems)}건의 문제"}')
        if not args.live:
            print('실제 호출은 하지 않았습니다(--live와 --budget 필요).')
        return 1 if problems else 0
    if problems:
        print('평가 세트에 문제가 있어 실행하지 않습니다:', *problems, sep='\n- ')
        return 2
    if not (args.budget and args.budget > 0 and args.base_url and args.cookie and args.repository_id):
        print('--live에는 --budget(>0), --base-url, --cookie, --repository-id가 모두 필요합니다.')
        return 2
    if args.set == 'final' and manifest.get('final_run_done'):
        print('최종 세트는 이미 실행됐습니다. 조정 후 한 번만 실행해 보고하세요.')
        return 2
    import requests
    http = requests.Session()
    name, _, value = args.cookie.partition('=')
    http.cookies.set(name, value)
    runner = Runner(http, args.base_url, budget=args.budget)
    scores, stopped = [], None
    for case in cases_of(manifest, args.set):
        try:
            scores.append(runner.run_case(case, args.repository_id))
        except BudgetExceeded as exc:
            stopped = str(exc)
            break
    summary = summarize(scores, manifest.get('criteria'))
    if stopped:
        summary['stopped_early'] = stopped
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime('%Y%m%d-%H%M%S')
    (out / f'ai-eval-{args.set}-{stamp}.json').write_text(json.dumps({'mode': 'live', 'set': args.set, 'summary': summary, 'cases': [s.__dict__ for s in scores]},
                                                                      ensure_ascii=False, indent=2), encoding='utf-8')
    report = render_report(args.set, 'live', scores, summary)
    (out / f'ai-eval-{args.set}-{stamp}.md').write_text(report, encoding='utf-8')
    print(report)
    return 0


if __name__ == '__main__':
    sys.exit(main())
