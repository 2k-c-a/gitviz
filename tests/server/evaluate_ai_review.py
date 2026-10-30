"""AI 리뷰 평가 도구. fake provider 검증(verify_ai_mvp_integration.py)과 **실제 모델 평가는 완전히 분리**한다.

    python tests/server/evaluate_ai_review.py --validate-manifest                      # 평가 세트 검증만(네트워크 없음)
    python tests/server/evaluate_ai_review.py --live --set tuning --budget 5 --base-url https://… --cookie "gitviz_session=…"

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
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / 'tests' / 'server' / 'fixtures' / 'ai' / 'evaluation_manifest.json'
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
    """줄 위치가 겹치는가. 이것은 **같은 결함이라는 증거가 아니라 매칭 후보**일 뿐이다(사람이 확인해야 한다)."""
    loc = finding['location']
    return (truth['path'] == loc['path'] and truth['side'] == loc['side']
            and loc['start_line'] <= truth['end_line'] + tolerance and truth['start_line'] <= loc['end_line'] + tolerance)


UNCERTAIN_CODES = frozenset({'OUTCOME_UNCERTAIN', 'PROVIDER_TIMEOUT'})


@dataclass
class CaseScore:
    case_id: str
    status: str
    error_code: str | None = None
    cost: float = 0.0                  # 정산된 비용
    exposure: float = 0.0              # 정산됐거나, 호출 결과를 몰라 최대 이만큼 나갔을 수 있는 비용(예산 계산에 쓴다)
    uncertain: bool = False            # 제공자가 처리했는지 모르는 호출
    coverage: dict = field(default_factory=dict)
    coverage_source: str = 'none'      # 'result'(결과 API) | 'input'(실행 전 입력의 값: 분석 범위가 아니다) | 'none'
    findings: int = 0
    truths: int = 0
    matched_truths: int = 0            # 찾은 알려진 결함: 위치가 겹치고 **사람이 유효(valid)로 확인한** 것만(재현율의 분자)
    candidate_truths: int = 0          # 위치가 겹치는 지적이 하나라도 있는 정답(확인 전 후보 포함)
    pending: list = field(default_factory=list)     # 정답 후보와 겹치지만 사람이 아직 확인하지 않은 지적(유효/오탐 판정 필요)
    unmatched: list = field(default_factory=list)   # 어떤 정답과도 겹치지 않은 지적(사람이 판정)
    verdicts: dict = field(default_factory=lambda: {'valid': 0, 'false_positive': 0, 'uncertain': 0, 'unverified': 0})
    note: str = ''
    job_id: str | None = None
    result_id: str | None = None
    negative_control: bool = False     # 결함이 없는 대조 사례(여기서 나온 지적은 모두 오탐 후보)


def _finding_row(finding: dict, verdict: str) -> dict:
    loc = finding['location']
    return {'path': loc['path'], 'lines': [loc['start_line'], loc['end_line']], 'side': loc['side'], 'title': finding['title'], 'human_validation': verdict}


def score_case(case: dict, job: dict, findings: list[dict], cost: float, *, coverage: dict | None = None, coverage_source: str = 'none',
               exposure: float | None = None) -> CaseScore:
    """한 사례를 채점한다. 위치 일치는 후보일 뿐이라 사람이 valid로 판정한 지적만 '찾은 결함'으로 센다:
    오탐(false_positive)으로 판정된 지적은 결함 발견이 아니고, 미판정·불확실은 확인 대기(pending)로 남는다."""
    truths = case.get('truths') or []
    error_code = job.get('error_code')
    uncertain = bool(job.get('outcome_uncertain')) or error_code in UNCERTAIN_CODES
    score = CaseScore(case_id=case['id'], status=job.get('status', 'unknown'), error_code=error_code, cost=cost,
                      exposure=cost if exposure is None else exposure, uncertain=uncertain, coverage=dict(coverage or {}),
                      coverage_source=coverage_source if coverage else 'none', findings=len(findings), truths=len(truths),
                      negative_control=bool(case.get('negative_control')))
    confirmed, candidates = set(), set()
    for item in findings:
        finding = item['finding']
        hits = [i for i, t in enumerate(truths) if overlaps(t, finding)]
        verdict = finding.get('human_validation') or 'unverified'
        verdict = verdict if verdict in score.verdicts else 'unverified'
        score.verdicts[verdict] += 1
        if not hits:
            score.unmatched.append(_finding_row(finding, verdict))
            continue
        candidates.update(hits)
        if verdict == 'valid':
            confirmed.update(hits)
        elif verdict != 'false_positive':
            score.pending.append(_finding_row(finding, verdict))
    score.matched_truths = len(confirmed)
    score.candidate_truths = len(candidates)
    return score


def summarize(scores: list[CaseScore], criteria: dict | None = None, *, expected_cases: int | None = None, stopped: str | None = None) -> dict:
    """집계와 최종 판정. 판정은 pass / fail / hold / criteria_missing:
    - hold: 평가가 끝나지 않았거나(실패·미시작 사례, 예산·불명 호출로 중단, 사례 누락) 사람 판정이 남았거나(확인 대기·미판정 지적)
      분석 범위가 완전하지 않으면 합격/불합격을 내지 않고 보류한다. 결함 없는 대조 사례가 실패하거나 미판정 지적을 냈을 때도 같다.
    - criteria_missing: 합의한 기준이 없다.
    - pass/fail: 평가가 끝나고 모든 지적이 판정된 뒤에만 기준과 비교한다."""
    total_truths = sum(s.truths for s in scores)
    found = sum(s.matched_truths for s in scores)
    verdicts = {k: sum(s.verdicts[k] for s in scores) for k in ('valid', 'false_positive', 'uncertain', 'unverified')}
    judged = verdicts['valid'] + verdicts['false_positive']
    pending = sum(len(s.pending) for s in scores)
    unjudged = verdicts['unverified'] + verdicts['uncertain']
    summary = {
        'cases': len(scores), 'expected_cases': expected_cases if expected_cases is not None else len(scores),
        'succeeded': sum(1 for s in scores if s.status == 'succeeded'), 'failed': [s.case_id for s in scores if s.status != 'succeeded'],
        'cost_usd': round(sum(s.cost for s in scores), 6), 'exposure_usd': round(sum(s.exposure for s in scores), 6),
        'uncertain_calls': [s.case_id for s in scores if s.uncertain], 'findings': sum(s.findings for s in scores),
        'known_defects': total_truths, 'known_defects_found': found, 'known_defect_candidates': sum(s.candidate_truths for s in scores),
        'recall_on_known_defects': (found / total_truths) if total_truths else None,
        'human_verdicts': verdicts, 'false_positives': verdicts['false_positive'],
        'precision_among_human_judged': (verdicts['valid'] / judged) if judged else None,
        'pending_confirmation': pending, 'unjudged_findings': unjudged,
        'unmatched_findings_to_review': sum(len(s.unmatched) for s in scores),
        'negative_control_findings': sum(s.findings for s in scores if s.negative_control),      # 결함 없는 사례에서 나온 지적 수(모두 오탐 후보)
        'analysis_coverage': {k: sum(1 for s in scores if s.status == 'succeeded' and s.coverage.get('analysis_coverage') == k and s.coverage_source == 'result')
                              for k in ('complete', 'partial', 'none')},
    }
    reasons = []
    if stopped:
        summary['stopped_early'] = stopped
        reasons.append(f'평가가 중단됐습니다: {stopped}')
    if len(scores) < summary['expected_cases']:
        reasons.append(f'사례 {summary["expected_cases"]}개 중 {len(scores)}개만 실행됐습니다.')
    for s in scores:
        if s.status != 'succeeded':
            reasons.append(f'{s.case_id}: 실행이 끝나지 않았습니다({s.status}{"/" + s.error_code if s.error_code else ""}).')
        elif s.coverage_source != 'result' or s.coverage.get('analysis_coverage') != 'complete':
            reasons.append(f'{s.case_id}: 분석 범위가 완전하지 않거나 확인하지 못했습니다.')
    if summary['uncertain_calls']:
        reasons.append(f'결과를 알 수 없는 호출이 있습니다({", ".join(summary["uncertain_calls"])}): 제공자 콘솔에서 비용·처리 여부를 확인하세요.')
    if pending:
        reasons.append(f'정답과 위치가 겹치지만 사람이 확인하지 않은 지적이 {pending}건 있습니다(같은 결함이면 valid, 아니면 false_positive로 판정).')
    if unjudged:
        reasons.append(f'사람이 판정하지 않은 지적(미판정·불확실)이 {unjudged}건 있습니다.')
    summary['hold_reasons'] = reasons
    crit = criteria or {}
    if reasons:
        summary['verdict'] = 'hold'
        summary['verdict_note'] = '평가가 끝나지 않았거나 사람 판정이 남아 합격/불합격을 내지 않고 보류합니다.'
    elif all(crit.get(k) is None for k in ('min_recall', 'min_precision', 'max_cost_per_case_usd')):
        summary['verdict'] = 'criteria_missing'
        summary['verdict_note'] = '합의한 통과 기준이 없어 합격/불합격을 계산하지 않았습니다(criteria를 먼저 합의하세요).'
    else:
        checks = []
        if crit.get('min_recall') is not None:
            checks.append(summary['recall_on_known_defects'] is not None and summary['recall_on_known_defects'] >= crit['min_recall'])
        if crit.get('min_precision') is not None:
            checks.append(summary['precision_among_human_judged'] is not None and summary['precision_among_human_judged'] >= crit['min_precision'])
        if crit.get('max_cost_per_case_usd') is not None:
            checks.append(bool(scores) and summary['exposure_usd'] / len(scores) <= crit['max_cost_per_case_usd'])
        summary['verdict'] = 'pass' if all(checks) else 'fail'
        summary['verdict_note'] = ''
    return summary


def render_report(set_name: str, mode: str, scores: list[CaseScore], summary: dict) -> str:
    rec = summary['recall_on_known_defects']
    prec = summary['precision_among_human_judged']
    lines = [f'# AI 리뷰 평가 보고 — {set_name} 세트 ({mode})', '',
             f'- 판정: **{summary["verdict"]}** {summary.get("verdict_note", "")}',
             f'- 사례 {summary["cases"]}/{summary["expected_cases"]}개 실행, 성공 {summary["succeeded"]}개, 정산 비용 ${summary["cost_usd"]} (불명 호출 상한 포함 ${summary["exposure_usd"]})',
             f'- 알려진 결함 {summary["known_defects"]}개 중 사람이 확인한 발견 {summary["known_defects_found"]}개'
             f' (재현율 {rec if rec is not None else "—"}), 확인 전 후보 포함 {summary["known_defect_candidates"]}개',
             f'- 지적 {summary["findings"]}건: 사람 판정 {summary["human_verdicts"]} / 오탐 {summary["false_positives"]}건 / 확인 대기 {summary["pending_confirmation"]}건 /'
             f' 미판정·불확실 {summary["unjudged_findings"]}건 / 정답과 겹치지 않아 사람이 봐야 할 지적 {summary["unmatched_findings_to_review"]}건 /'
             f' 결함 없는 대조 사례에서 나온 지적 {summary["negative_control_findings"]}건',
             f'- 판정된 지적 중 유효 비율: {prec if prec is not None else "판정 없음"}',
             f'- 분석 범위(결과 API 기준): {summary["analysis_coverage"]}']
    if summary['hold_reasons']:
        lines += ['', '## 보류 사유', *[f'- {r}' for r in summary['hold_reasons']]]
    lines += ['', '| 사례 | 상태 | 지적 | 정답 | 확인된 정답 | 확인 대기 | 비용 | 분석 범위 |', '|---|---|---|---|---|---|---|---|']
    for s in scores:
        cov = s.coverage.get('analysis_coverage', '—') if s.coverage_source == 'result' else '—'
        lines.append(f'| {s.case_id} | {s.status}{" / " + s.error_code if s.error_code else ""}{" (불명)" if s.uncertain else ""} | {s.findings} | {s.truths} | '
                     f'{s.matched_truths} | {len(s.pending)} | ${s.cost:.4f} | {cov} |')
    lines += ['', '위치가 겹치는 것은 후보일 뿐이며, 사람이 valid로 판정한 지적만 결함 발견으로 셉니다. 구조 검증 통과는 내용이 옳다는 뜻이 아닙니다.']
    return '\n'.join(lines)


# ── 실행(HTTP) ─────────────────────────────────────────────────────────────

class BudgetExceeded(Exception):
    pass


class RecoveryFailed(Exception):
    """접수됐을 수 있는 작업을 복구하지 못했다. 완료로 기록하지 않고 상한을 유지한 채 멈춘다."""


class Journal:
    """실행 상태 기록(파일). 최종 세트의 "한 번만 실행"과 중단 뒤 복구의 근거다.

    - **시작 전에 원자적으로 만든다**(`O_CREAT|O_EXCL`): 같은 명령을 다시 실행하거나 동시에 두 번 실행해도 두 번째는 만들지 못한다.
      정상 종료 뒤에 남기는 표시가 아니므로, 접수 뒤 연결이 끊겨 비정상 종료해도 기록이 남는다.
    - 사례마다 진행 단계(started → submitting → accepted → done / interrupted)와 예상 상한·정산 기준(usage_before)을 접수 **전에** 저장한다.
    - 갱신은 임시 파일에 쓰고 `os.replace`로 바꿔 부분 기록이 남지 않게 한다."""

    def __init__(self, path, data):
        self.path, self.data = Path(path), data

    @classmethod
    def create(cls, path, **header):
        data = {**header, 'started_at': time.strftime('%Y-%m-%dT%H:%M:%S'), 'cases': {}, 'spent': 0.0, 'held': 0.0, 'complete': False, 'stopped': None}
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)          # 이미 있으면 FileExistsError
        try:
            os.write(fd, json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8'))
            os.fsync(fd)
        finally:
            os.close(fd)
        return cls(path, data)

    @classmethod
    def load(cls, path):
        journal = cls(path, json.loads(Path(path).read_text(encoding='utf-8')))
        journal.derive()                                                  # 저장된 합계는 믿지 않고 사례 기록에서 다시 계산한다
        return journal

    def derive(self):
        """비용 합계는 사례 기록(settled·held)에서 계산한 값이다. 사례 완료와 비용 합계가 따로 저장되면 그 사이에 종료됐을 때 합계가 이전 값으로
        남아 예산을 넘겨 접수하게 되므로, 합계는 별도로 쓰지 않고 항상 사례 기록에서 만든다(사례 한 건의 저장이 곧 합계의 저장이다)."""
        cases = self.data['cases'].values()
        self.data['spent'] = round(sum(float(c.get('settled') or 0.0) for c in cases), 6)
        self.data['held'] = round(sum(float(c.get('held') or 0.0) for c in cases), 6)

    def save(self):
        self.derive()
        tmp = self.path.with_name(self.path.name + '.tmp')
        with open(tmp, 'w', encoding='utf-8') as f:
            f.write(json.dumps(self.data, ensure_ascii=False, indent=2))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)

    def case(self, case_id: str) -> dict:
        return self.data['cases'].setdefault(case_id, {'status': 'pending'})

    def update(self, case_id: str, **fields):
        self.case(case_id).update(fields)
        self.save()


IN_FLIGHT = ('submitting', 'accepted', 'interrupted')      # 서버가 작업을 이미 접수했을 수 있는 단계


class Runner:
    """배포된(또는 통합 검증용) GitViz에 HTTP로 접속해 사례를 실행한다. http는 쿠키·Origin이 설정된 requests.Session 같은 객체다.

    예산: 정산된 비용(spent)과 **결과를 모르는 호출의 상한(held)**을 모두 반영한다. 불명 호출은 정산액이 늘지 않아도 비용이 이미 나갔을 수 있으므로
    그 호출의 예상 상한을 예산에서 빼지 않고 계속 잡아 둔다. 서버가 알려 주는 남은 월 한도(user_remaining)도 함께 확인한다.

    journal이 있으면 접수 요청의 Idempotency-Key를 (실행 id, 사례, 단계)로 정해 **결정적**으로 만들고 진행 단계를 접수 전에 기록한다. 중단된 실행을
    다시 이어가면 같은 key로 같은 요청을 보내 서버가 이미 접수한 입력·작업을 그대로 돌려주므로 작업이 하나 더 생기지 않는다."""

    def __init__(self, http, base_url: str, *, budget: float, csrf_token: str | None = None, poll_seconds: float = 3.0, timeout: float = 600.0,
                 sleep=time.sleep, journal: 'Journal | None' = None):
        self.http, self.base, self.budget, self.spent, self.held = http, base_url.rstrip('/'), float(budget), 0.0, 0.0
        self.csrf, self.poll, self.timeout, self.sleep, self.journal = csrf_token, poll_seconds, timeout, sleep, journal
        if journal is not None:
            journal.derive()
            self.spent, self.held = float(journal.data['spent']), float(journal.data['held'])

    @property
    def committed(self) -> float:
        return self.spent + self.held

    def key(self, case_id: str, step: str) -> str:
        import hashlib
        import uuid
        if self.journal is None:
            return uuid.uuid4().hex
        return hashlib.sha256(f'{self.journal.data["run_id"]}|{case_id}|{step}'.encode()).hexdigest()[:48]

    def call(self, method, path, body=None, key=None):
        headers = {'Origin': self.base}
        if method != 'GET':
            if not self.csrf:
                self.csrf = self.http.get(f'{self.base}/api/auth/csrf', timeout=15).json()['token']
            headers['X-CSRF-Token'] = self.csrf
        if key:
            headers['Idempotency-Key'] = key
        return self.http.request(method, f'{self.base}{path}', json=body, headers=headers, timeout=60)

    def _usage(self) -> dict:
        return self.call('GET', '/api/ai/v1/usage').json()

    def _get_job(self, job_id: str, *, recovering: bool) -> dict:
        resp = self.call('GET', f'/api/ai/v1/jobs/{job_id}')
        if resp.status_code != 200:
            try:
                code = (resp.json().get('error') or {}).get('code')
            except ValueError:
                code = None
            raise RecoveryFailed(f'작업 {job_id}를 조회하지 못했습니다(HTTP {resp.status_code} {code}). 접수된 작업이 있을 수 있어 상한을 예산에 유지합니다.')
        return resp.json()

    def run_case(self, case: dict, repository_id: int, kind: str = 'review') -> CaseScore:
        j, cid = self.journal, case['id']
        rec = j.case(cid) if j else {}
        if rec.get('status') == 'done':
            return CaseScore(**rec['score'])                             # 이미 끝난 사례는 다시 실행하지 않는다
        in_flight = rec.get('status') in IN_FLIGHT
        # 복구 중에는 처리 결과를 확인할 때까지 이전에 잡아 둔 상한과 접수 상태를 그대로 둔다(중간에 다시 끊겨도 잃지 않는다).
        prev_held = float(rec.get('held') or 0.0) if in_flight else 0.0
        estimate = float(rec.get('estimate') or 0.0) if in_flight else 0.0
        usage_before = rec['usage_before'] if in_flight and 'usage_before' in rec else float(self._usage()['user_spent'])
        if j and not in_flight:
            j.update(cid, status='started', usage_before=usage_before)
        job_id = rec.get('job_id') if in_flight else None
        if job_id is None:
            if in_flight and rec.get('job_body'):
                body = rec['job_body']                                   # 접수 여부가 불명확하면 **최초 요청 본문 그대로** 같은 key로 다시 보낸다
            else:
                created = self.call('POST', '/api/ai/v1/inputs', {'repository_id': repository_id, 'pr_number': case['pr_number'], 'expected_base_sha': case['base_sha'],
                                                                   'expected_head_sha': case['head_sha'], 'selected_paths': case['selected_paths']}, self.key(cid, 'input'))
                if created.status_code not in (200, 202):
                    return CaseScore(cid, 'not_started', created.json().get('error', {}).get('code'), note='입력을 만들지 못했습니다')
                input_id = created.json()['input_id']
                preview = self._wait(lambda: (lambda p: p if p['status'] != 'building' else None)(self.call('GET', f'/api/ai/v1/inputs/{input_id}').json()))
                if preview['status'] != 'ready' or not preview.get('reviewable'):
                    return CaseScore(cid, 'not_started', preview['status'], note='실행할 수 있는 입력이 아닙니다(reviewable=false 포함)')
                estimate = float((preview.get('token_estimate') or {}).get('estimated_max_cost') or 0)
                remaining = self.budget - self.committed
                if estimate > remaining:
                    raise BudgetExceeded(f'{cid}: 예상 비용 상한 ${estimate:.4f}가 남은 평가 예산 ${remaining:.4f}'
                                         f'(정산 ${self.spent:.4f} + 결과를 모르는 호출의 상한 ${self.held:.4f} 반영)을 넘습니다.')
                server_remaining = float(self._usage().get('user_remaining') or 0)
                if estimate > server_remaining:
                    raise BudgetExceeded(f'{cid}: 예상 비용 상한 ${estimate:.4f}가 계정의 남은 월 한도 ${server_remaining:.4f}를 넘습니다.')
                t = preview['transfer']
                body = {'kind': kind, 'consent': {'input_id': input_id, 'manifest_hash': preview['manifest_hash'], 'provider': t['provider'], 'profile_id': t['profile_id'],
                                                  'credential_version': t['credential_version'], 'policy_version': preview['policy_version']}, 'force_regenerate': False}
                if t.get('profile_fingerprint'):
                    body['consent']['profile_fingerprint'] = t['profile_fingerprint']
                if j:                                                    # 접수 요청을 보내기 **전에** 요청 본문과 함께 기록한다
                    j.update(cid, status='submitting', estimate=estimate, input_id=input_id, job_body=body)
            job = self.call('POST', '/api/ai/v1/jobs', body, self.key(cid, 'job'))
            if job.status_code not in (200, 202):
                if in_flight:                                            # 복구 중 접수 응답이 거절이면 상태를 알 수 없다: 완료로 기록하지 않고 상한을 유지한 채 멈춘다
                    raise RecoveryFailed(f'{cid}: 이전에 접수했을 수 있는 작업을 같은 요청으로 복구하지 못했습니다(HTTP {job.status_code} '
                                         f'{(job.json().get("error") or {}).get("code")}). 제공자 콘솔과 GitViz에서 작업이 있는지 확인하세요.')
                if j:
                    j.update(cid, status='pending')
                return CaseScore(cid, 'not_started', job.json().get('error', {}).get('code'), note='작업을 접수하지 못했습니다')
            job_id = job.json()['job_id']
            if j:
                j.update(cid, status='accepted', job_id=job_id)
        try:
            done = self._wait(lambda: (lambda x: x if x['status'] in ('succeeded', 'failed', 'cancelled') else None)(self._get_job(job_id, recovering=in_flight)))
        except TimeoutError:
            self.held = max(0.0, self.held - prev_held) + estimate     # 끝나지 않은 호출: 결과·비용을 모른다
            score = CaseScore(cid, 'timeout', 'OUTCOME_UNKNOWN', exposure=estimate, uncertain=True, note='시간 안에 끝나지 않았습니다')
            score.job_id = job_id
            self._finish(cid, score, held=estimate, settled=0.0)
            return score
        findings, coverage, source = [], dict(done.get('coverage') or {}), ('input' if done.get('coverage') else 'none')
        if done['status'] == 'succeeded':
            result = self.call('GET', f'/api/ai/v1/results/{done["result_id"]}').json()          # 분석 범위는 결과 API가 기준이다
            coverage, source = dict(result.get('coverage') or {}), ('result' if result.get('coverage') else 'none')
            if kind == 'review':
                findings = self.call('GET', f'/api/ai/v1/results/{done["result_id"]}/findings?limit=50').json()['findings']
        settled = max(0.0, float(self._usage()['user_spent']) - usage_before)
        uncertain = bool(done.get('outcome_uncertain')) or done.get('error_code') in UNCERTAIN_CODES
        exposure = max(settled, estimate) if uncertain else settled
        self.spent += settled
        held = max(0.0, exposure - settled) if uncertain else 0.0
        self.held = max(0.0, self.held - prev_held) + held             # 확인된 결과로 이전 상한을 바꾼다
        score = score_case(case, done, findings, settled, coverage=coverage, coverage_source=source, exposure=exposure)
        score.job_id, score.result_id = job_id, done.get('result_id')
        self._finish(cid, score, held=held, settled=settled)
        return score

    def _finish(self, case_id, score, *, held=0.0, settled=0.0):
        if self.journal is not None:
            # 완료 상태·점수·정산액·상한을 **한 번의 원자적 저장**으로 기록한다(합계는 이 기록에서 계산되므로 따로 어긋날 수 없다).
            self.journal.update(case_id, status='done', score=score.__dict__, held=held, settled=settled)

    def _wait(self, condition):
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            value = condition()
            if value:
                return value
            self.sleep(self.poll)
        raise TimeoutError('시간 안에 끝나지 않았습니다.')

    def interrupted(self, case_id: str, error: str) -> float:
        """실행 도중 예외(연결 끊김 등)로 중단됐다. 접수했을 수 있는 작업의 상한을 예산에 잡고 기록한다(--resume으로 복구).
        복구 중 다시 끊겨도 이미 잡아 둔 상한과 접수 상태는 그대로 유지하고 중복해서 더하지 않는다."""
        j = self.journal
        rec = j.case(case_id) if j else {}
        in_flight = rec.get('status') in IN_FLIGHT
        target = float(rec.get('estimate') or 0.0) if in_flight else 0.0
        self.held += max(0.0, target - float(rec.get('held') or 0.0))
        if j:
            j.update(case_id, status='interrupted' if in_flight else rec.get('status', 'pending'), held=target if in_flight else float(rec.get('held') or 0.0), error=error)
        return target


def run_set(runner: Runner, cases: list[dict], repository_id: int, *, continue_after_uncertain: bool = False) -> tuple[list[CaseScore], str | None]:
    """사례를 차례로 실행한다. 예산·불명 호출·예외로 멈추면 (지금까지의 점수, 중단 사유)를 돌려준다. 이미 끝난 사례는 다시 실행하지 않는다."""
    scores, stopped = [], None
    for case in cases:
        try:
            score = runner.run_case(case, repository_id)
        except BudgetExceeded as exc:
            stopped = str(exc)
            break
        except RecoveryFailed as exc:                                # 복구 불가: 완료로 기록하지 않고 상한을 유지한 채 멈춘다(확인 후 다시 --resume)
            runner.interrupted(case['id'], 'RecoveryFailed')
            stopped = str(exc)
            break
        except Exception as exc:                                     # 연결 끊김 등: 접수됐을 수 있으므로 상한을 잡고 기록한 뒤 멈춘다
            runner.interrupted(case['id'], type(exc).__name__)
            stopped = (f'{case["id"]}: 실행 도중 오류({type(exc).__name__})로 중단했습니다. 이미 접수된 작업이 있을 수 있으니 같은 명령을 다시 실행하지 말고 '
                       f'--resume으로 이어가세요(저장된 작업 id로 조회하고, 접수 여부가 불명확할 때만 최초 요청 그대로 같은 key로 다시 보냅니다).')
            break
        scores.append(score)
        if score.uncertain and not continue_after_uncertain:
            stopped = f'{score.case_id}: 호출 결과를 알 수 없어 평가를 중단했습니다(제공자 콘솔에서 확인하세요).'
            break
    if runner.journal is not None:
        runner.journal.data['stopped'] = stopped
        runner.journal.data['complete'] = stopped is None and len(scores) == len(cases)
        runner.journal.save()
    return scores, stopped


def rescore(report: dict, cases: list[dict], http, base_url: str) -> list[CaseScore]:
    """저장된 실행 보고서를 사람이 화면에서 판정(피드백)한 뒤의 상태로 다시 채점한다. 제공자를 호출하지 않으므로 비용이 들지 않는다.
    정산 비용·불명 상한·분석 범위는 실행 때 기록한 값을 그대로 쓰고, 지적과 사람 판정만 다시 읽는다."""
    by_id = {c['id']: c for c in cases}
    out = []
    for old in report['cases']:
        case = by_id.get(old['case_id'])
        if case is None:
            raise ValueError(f'평가 세트에 없는 사례입니다: {old["case_id"]}')
        findings = []
        if old.get('status') == 'succeeded' and old.get('result_id'):
            resp = http.request('GET', f'{base_url.rstrip("/")}/api/ai/v1/results/{old["result_id"]}/findings?limit=50', headers={'Origin': base_url.rstrip('/')}, timeout=60)
            findings = resp.json()['findings']
        score = score_case(case, {'status': old.get('status'), 'error_code': old.get('error_code'), 'outcome_uncertain': old.get('uncertain')}, findings,
                           old.get('cost', 0.0), coverage=old.get('coverage'), coverage_source=old.get('coverage_source', 'none'), exposure=old.get('exposure'))
        score.job_id, score.result_id, score.note = old.get('job_id'), old.get('result_id'), old.get('note', '')
        out.append(score)
    return out


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
    ap.add_argument('--continue-after-uncertain', action='store_true', help='결과를 알 수 없는 호출이 생겨도 계속한다(기본은 중단, 그 호출의 상한은 예산에 계속 반영)')
    ap.add_argument('--resume', action='store_true', help='중단된 실행을 이어간다(접수된 작업은 같은 요청 key로 복구하고 새로 만들지 않는다)')
    ap.add_argument('--fresh', action='store_true', help='조정 세트만: 이전 실행 기록을 보관하고 새로 시작한다(최종 세트는 불가)')
    ap.add_argument('--rescore', help='이전 실행 보고서(.json)를 사람이 판정한 뒤의 상태로 다시 채점한다(제공자 호출·비용 없음)')
    ap.add_argument('--out', default=str(ROOT / 'reports'))
    args = ap.parse_args(argv)
    manifest = load_manifest(Path(args.manifest))
    problems = validate_manifest(manifest)
    if args.check_release:
        blockers = release_blockers(manifest)
        print(*(['출시 전 확인이 필요한 항목:'] + [f'- {b}' for b in blockers] if blockers else ['남은 항목 없음']), sep=chr(10))
        return 1 if blockers or problems else 0
    if args.rescore:
        if problems:
            print('평가 세트에 문제가 있어 채점하지 않습니다:', *problems, sep=chr(10) + '- ')
            return 2
        if not (args.base_url and args.cookie):
            print('--rescore에는 --base-url과 --cookie가 필요합니다.')
            return 2
        import requests
        report = json.loads(Path(args.rescore).read_text(encoding='utf-8'))
        http = requests.Session()
        name, _, value = args.cookie.partition('=')
        http.cookies.set(name, value)
        scores = rescore(report, cases_of(manifest, report['set']), http, args.base_url)
        summary = summarize(scores, manifest.get('criteria'), expected_cases=report['summary'].get('expected_cases'), stopped=report['summary'].get('stopped_early'))
        print(render_report(report['set'], 'live(재채점)', scores, summary))
        return 0
    if args.validate_manifest or not args.live:
        for p in problems:
            print('문제:', p)
        print(f'평가 세트 검증: {"통과" if not problems else f"{len(problems)}건의 문제"}')
        if not args.live:
            print('실제 호출은 하지 않았습니다(--live와 --budget 필요).')
        return 1 if problems else 0
    if problems:
        print('평가 세트에 문제가 있어 실행하지 않습니다:', *problems, sep=chr(10) + '- ')
        return 2
    if not (args.budget and args.budget > 0 and args.base_url and args.cookie and args.repository_id):
        print('--live에는 --budget(>0), --base-url, --cookie, --repository-id가 모두 필요합니다.')
        return 2
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    marker = out / 'FINAL_RUN_DONE.json'
    state_path = out / f'ai-eval-{args.set}-state.json'
    if args.set == 'final' and (manifest.get('final_run_done') or marker.exists()):
        print('최종 세트는 이미 실행을 마쳤습니다. 조정 후 한 번만 실행해 보고하세요(재판정은 --rescore).')
        return 2
    cases = cases_of(manifest, args.set)
    if args.resume:
        if not state_path.exists():
            print('이어갈 실행 기록이 없습니다.')
            return 2
        journal = Journal.load(state_path)
        if journal.data.get('set') != args.set or journal.data.get('complete'):
            print('이미 완료됐거나 다른 세트의 실행 기록입니다. 결과는 --rescore로 확인하세요.')
            return 2
    else:
        if args.fresh and args.set == 'tuning' and state_path.exists():
            state_path.rename(state_path.with_name(f'{state_path.name}.{time.strftime("%Y%m%d-%H%M%S")}.old'))
        try:
            import uuid
            journal = Journal.create(state_path, run_id=uuid.uuid4().hex, set=args.set, budget=args.budget, expected=len(cases))   # 시작 전에 원자적으로 남긴다
        except FileExistsError:
            print('이 세트의 실행 기록이 이미 있습니다(중단됐을 수 있습니다). 같은 명령을 다시 실행하지 않습니다: --resume으로 이어가면 접수된 작업을 같은 key로 복구합니다'
                  + (' (조정 세트는 --fresh로 새로 시작할 수 있습니다).' if args.set == 'tuning' else '(최종 세트는 새로 시작할 수 없습니다).'))
            return 2
    import requests
    http = requests.Session()
    name, _, value = args.cookie.partition('=')
    http.cookies.set(name, value)
    runner = Runner(http, args.base_url, budget=args.budget, journal=journal)
    scores, stopped = run_set(runner, cases, args.repository_id, continue_after_uncertain=args.continue_after_uncertain)
    summary = summarize(scores, manifest.get('criteria'), expected_cases=len(cases), stopped=stopped)
    stamp = time.strftime('%Y%m%d-%H%M%S')
    (out / f'ai-eval-{args.set}-{stamp}.json').write_text(json.dumps({'mode': 'live', 'set': args.set, 'run_id': journal.data['run_id'], 'summary': summary,
                                                                      'cases': [s.__dict__ for s in scores]}, ensure_ascii=False, indent=2), encoding='utf-8')
    report = render_report(args.set, 'live', scores, summary)
    (out / f'ai-eval-{args.set}-{stamp}.md').write_text(report, encoding='utf-8')
    if args.set == 'final' and journal.data.get('complete'):
        marker.write_text(json.dumps({'ran_at': stamp, 'cases': len(scores), 'run_id': journal.data['run_id']}), encoding='utf-8')
    print(report)
    return 0 if journal.data.get('complete') else 3          # 3: 중단(--resume으로 이어갈 수 있다)


if __name__ == '__main__':
    sys.exit(main())
