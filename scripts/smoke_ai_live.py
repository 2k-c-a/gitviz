"""실제 연동 소량 smoke 테스트(실제 모델 평가 전에 한 번). 일반 CI에서는 돌리지 않는다.

    # 제공자: 평가자 본인의 API key를 환경변수로만 준다(명령줄 인자·로그·화면에 남기지 않는다)
    GITVIZ_SMOKE_API_KEY=sk-ant-… python scripts/smoke_ai_live.py provider --live --budget 0.50
    # GitHub: 공개 PR 하나로 목록·파일·고정 SHA·고정 코드 수집 경로를 실제 GitHub로 확인(읽기 전용, 토큰은 선택)
    GITHUB_TOKEN=… python scripts/smoke_ai_live.py github --live --repo owner/name --pr 123 --path src/app.py

`--live` 없이 실행하면 무엇을 할지 계획만 보여 주고 아무것도 호출하지 않는다.

provider 단계가 확인하는 것(코드는 이 파일의 작은 합성 diff뿐이며 외부 자료가 아니다):
  1) key 확인(models 조회, 코드 미전송·무과금) 2) count_tokens가 실제 출력 schema를 받아 토큰 수를 돌려주는가(로컬 추정과 비교)
  3) 설명·리뷰 각 1회 생성: 구조화 출력 schema를 제공자가 수용하는가(거부·잘림·거절 여부) 4) 응답 usage와 profile 가격으로 계산한 비용
  5) 응답이 우리 검증기(파일·줄·인용)를 통과하는가 6) 지연 시간·request-id.
예산(`--budget`)이 두 번의 최대 비용 상한을 덮지 못하면 호출하기 전에 멈춘다. 결과는 보고서로 출력하며 key는 어디에도 쓰지 않는다.
"""
import argparse
import json
import os
import sys
import time
from decimal import Decimal
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SYNTHETIC_PATH = 'src/average.py'
SYNTHETIC_LINES = [(' ', 1, 1, 'def average(values):'), ('-', 2, None, '    return sum(values) / len(values)'),
                   ('+', None, 2, '    total = sum(values)'), ('+', None, 3, '    return total / len(values)')]


def synthetic_sent():
    from app.schemas.ai import ChangeType, DiffLine, DiffLineKind, PRTransferMetadata
    from app.services.ai import prompts
    kinds = {' ': DiffLineKind.context, '-': DiffLineKind.removed, '+': DiffLineKind.added}
    lines = [DiffLine(kind=kinds[m], left_line=left, right_line=right, text=text) for m, left, right, text in SYNTHETIC_LINES]
    file = prompts.SentFile(handle='F1', file_id=UUID(int=1), path=SYNTHETIC_PATH, old_path=None, change_type=ChangeType.modified,
                            side_availability='BOTH', language='python', lines=lines)
    return prompts.SentInput(files=[file], metadata=PRTransferMetadata(title='Refactor average', body='Splits the sum into a variable.'))


def provider_smoke(api_key: str | None, profile_id: str, budget: Decimal, *, provider_factory=None, official_only: bool = True) -> dict:
    """provider 단계. 보고용 dict를 돌려준다(key는 담지 않는다). report['ok']가 False면 실패.

    official_only=True(기본, 실제 smoke)이면 AI_TEST_PROVIDER_BASE_URL이 설정돼 있을 때 key를 보내기 전에 거부한다: 실제 smoke는 공식 주소만 쓴다.
    False는 로컬 가짜 제공자로 이 도구를 시험하는 테스트 전용이다(주소는 어댑터가 로컬만 허용한다)."""
    from app.services.ai import prompts, token_budget, validators
    from app.services.ai.providers import registry
    from app.services.ai.providers.base import GenerationRequest, ProviderError, TokenLimitExceeded
    from app.core.ai_settings import get_ai_settings
    profile = registry.get_profile(profile_id)
    if official_only and get_ai_settings().test_provider_base_url:
        return {'profile': profile.id, 'model': profile.model, 'ok': False, 'cost_usd': None, 'cost_known': False, 'steps': [
            {'step': '제공자 주소', 'ok': False, 'detail': 'AI_TEST_PROVIDER_BASE_URL이 설정돼 있습니다. 실제 smoke는 공식 주소만 사용하므로 key를 보내지 않았습니다(환경변수를 비우세요)'}]}
    provider = (provider_factory or registry.create_provider)(profile, api_key=api_key)
    report = {'profile': profile.id, 'model': profile.model, 'steps': [], 'ok': False, 'cost_usd': None, 'cost_known': False}
    sent = synthetic_sent()
    requests = {kind: prompts.render_request(kind, sent, prompt_version=profile.prompt_version(kind)) for kind in ('summary', 'review')}

    def step(name, ok, detail=''):
        report['steps'].append({'step': name, 'ok': ok, 'detail': detail})
        return ok

    try:
        provider.verify_credential()
        step('key 확인(models 조회)' if profile.requires_api_key else '설치 모델·digest 확인', True,
             '제공자가 key와 모델 접근을 확인했습니다' if profile.requires_api_key else '설치된 로컬 모델 digest를 확인했습니다')
    except ProviderError as err:
        step('key 확인(models 조회)', False, f'{err.failure.kind} (HTTP {err.failure.http_status})')
        return report
    counts = {}
    for kind, rendered in requests.items():
        req = GenerationRequest(kind=kind, system=rendered.system, user=rendered.user)
        try:
            counted = provider.count_tokens(req)
            local = provider.estimate_tokens(req)
            counts[kind] = (req, counted)
            token_budget.check_input_limit(profile, counted)
            report.setdefault('local_estimates', {})[kind] = local
            ok = local >= counted                                 # 로컬 추정은 실제 토큰 수 이상이어야 한다(예약·한도 검사가 보수적이려면)
            step(f'count_tokens({kind})', ok,
                 (f'Ollama 사전 tokenizer 없음: 보수적 입력 상한 {counted}토큰' if not profile.requires_api_key
                  else f'제공자 {counted}토큰 / 로컬 추정 {local}토큰')
                 + ('' if ok else ' — 로컬 추정이 실제보다 작아 예약·한도 검사가 보수적이지 않습니다. 실패로 처리합니다'))
            if not ok:
                report['local_estimate_underestimates'] = True
                return report
        except (ProviderError, TokenLimitExceeded) as err:
            step(f'count_tokens({kind})', False, getattr(getattr(err, 'failure', None), 'kind', type(err).__name__))
            return report
    needed = sum((token_budget.max_cost(profile, counted) for _, counted in counts.values()), Decimal(0))
    report['max_cost_upper_bound_usd'] = str(needed)
    if needed > budget:
        step('예산 확인', False, f'두 번의 최대 비용 상한 ${needed}가 예산 ${budget}을 넘어 호출하지 않았습니다')
        return report
    step('예산 확인', True, f'최대 비용 상한 ${needed} ≤ 예산 ${budget}')
    spent = Decimal(0)
    for kind, (req, counted) in counts.items():
        started = time.monotonic()
        try:
            response = provider.generate(req)
        except ProviderError as err:
            f = err.failure
            step(f'생성({kind})', False, f'{f.kind} (HTTP {f.http_status}, 처리 여부 불명={f.outcome_uncertain}) — request-id {f.request_id}')
            report['uncertain_cost_possible'] = bool(f.outcome_uncertain)
            return report
        latency = time.monotonic() - started
        cost = response.cost
        u = response.usage
        usable = response.finish == 'complete' and response.usable
        step(f'생성({kind}) 응답', usable,
             f'finish={response.finish}, 파싱={"됨" if response.parsed is not None else response.parse_error}, 지연 {latency:.1f}초, request-id {response.request_id}, '
             f'usage 입력 {u.billed_input_tokens if u else "없음"}/출력 {u.output_tokens if u else "없음"}, 비용 {cost if cost is not None else "usage 없음(알 수 없음)"}')
        if not usable:
            if response.finish == 'refused':
                report['refusal_category'] = response.refusal_category
            return report
        if u is None or cost is None:                              # usage가 없으면 비용을 확인할 수 없다: 0으로 보고하지 않고 실패
            step(f'비용 확인({kind})', False, '응답에 usage가 없어 비용을 확인할 수 없습니다(0이 아니라 미확인). 제공자 콘솔의 사용 내역으로 직접 확인하세요')
            report['cost_unverified'] = True
            return report
        local = report['local_estimates'][kind]
        if u.billed_input_tokens > local:
            step(f'토큰 추정 확인({kind})', False, f'응답 usage의 입력 토큰 {u.billed_input_tokens}이 로컬 추정 {local}보다 큽니다(보수적이지 않음)')
            report['local_estimate_underestimates'] = True
            return report
        spent += cost
        validated = validators.validate_output(kind, response, sent, revision_id=UUID(int=2), schema_version=profile.schema_version(kind))
        step(f'출력 검증({kind})', validated.ok, '파일·줄·인용 참조가 맞음(내용의 정확성은 확인하지 않음)' if validated.ok else '문제: ' + ', '.join(sorted({i.code for i in validated.issues})))
        if not validated.ok:
            return report
    report['cost_usd'], report['cost_known'] = str(spent), True
    report['ok'] = all(s['ok'] for s in report['steps'])
    return report


def github_smoke(repo_full_name: str, pr_number: int, paths: list[str], token: str | None) -> dict:
    """GitHub 단계(읽기 전용): 실제 GitHub로 목록·파일·고정 SHA·고정 코드 수집을 확인한다."""
    from app.collectors.git import ai_diff_collector
    from app.collectors.github.ai_pr_collector import RepoRef, capture_revision, list_ai_prs, list_pr_files
    from app.collectors.github.github_client import GitHubClient
    report = {'repo': repo_full_name, 'pr': pr_number, 'steps': [], 'ok': False}

    def step(name, ok, detail=''):
        report['steps'].append({'step': name, 'ok': ok, 'detail': detail})
        return ok
    owner, name = repo_full_name.split('/', 1)
    client = GitHubClient(token=token, timeout=20, max_retries=2, max_wait_seconds=20)
    import requests
    meta = requests.get(f'https://api.github.com/repos/{repo_full_name}', timeout=20, headers={'Authorization': f'Bearer {token}'} if token else {})
    if meta.status_code != 200:
        step('저장소 조회', False, f'HTTP {meta.status_code}')
        return report
    info = meta.json()
    if info.get('private'):
        step('공개 저장소 확인', False, '비공개 저장소는 평가·smoke에 쓰지 않습니다')
        return report
    step('공개 저장소 확인', True, f'공개, license={(info.get("license") or {}).get("spdx_id")}')
    ref = RepoRef(repository_id=1, github_id=info['id'], owner=owner, name=name)
    prs = list_ai_prs(client, ref, 'closed', None, 5)
    step('PR 목록', True, f'{len(prs.items)}건(closed/merged 필터 후)')
    files = list_pr_files(client, ref, pr_number, None, 100)
    step('PR 파일 목록', True, f'{len(files.files)}개, 전체 {files.pr_total_files}, 목록 품질 {files.inventory_coverage.value}, base {files.base_sha[:7]} head {files.head_sha[:7]}')
    captured = capture_revision(client, ref, pr_number, files.base_sha, files.head_sha)
    step('고정 SHA 캡처', True, f'merge-base {captured.merge_base_sha[:7]}')
    chosen = paths or [f['path'] for f in files.files[:2]]
    with ai_diff_collector.revision_workspace(f'https://github.com/{repo_full_name}.git', captured.merge_base_sha, captured.head_sha, token=token,
                                              protocols='https') as work:
        collected = ai_diff_collector.collect_selected_code(work, captured.merge_base_sha, captured.head_sha, sorted(chosen), max_files=50, max_bytes=2_000_000,
                                                            protocols='https', pr_total_files=files.pr_total_files)
    read = [f for f in collected.files if f.payload is not None]
    step('고정 코드 수집', bool(read), f'선택 {len(chosen)}개 중 읽음 {len(read)}개, coverage {collected.coverage.selected_content_coverage.value}')
    report['ok'] = all(s['ok'] for s in report['steps'])
    return report


def render(report: dict, title: str) -> str:
    lines = [f'# {title}', '']
    for s in report['steps']:
        lines.append(f'- [{"OK" if s["ok"] else "실패"}] {s["step"]}: {s["detail"]}')
    if 'cost_known' in report:
        lines.append('- 비용: ' + (str(report['cost_usd']) if report['cost_known'] else '미확인(0이 아닙니다 — 제공자 콘솔로 확인하세요)'))
    for key in ('max_cost_upper_bound_usd', 'refusal_category', 'uncertain_cost_possible', 'local_estimate_underestimates', 'cost_unverified'):
        if report.get(key) not in (None, False):
            lines.append(f'- {key}: {report[key]}')
    lines += ['', f'결과: {"통과" if report["ok"] else "실패"}', '(구조 검증 통과는 내용이 옳다는 뜻이 아닙니다. 이 smoke는 연동·schema·비용 계산을 확인할 뿐 모델 정확도를 평가하지 않습니다.)']
    return '\n'.join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='stage', required=True)
    p = sub.add_parser('provider')
    p.add_argument('--live', action='store_true')
    p.add_argument('--budget', type=Decimal, help='이번 smoke에 쓸 최대 비용(USD)')
    p.add_argument('--profile', default='claude-opus-5-5-v1')
    g = sub.add_parser('github')
    g.add_argument('--live', action='store_true')
    g.add_argument('--repo')
    g.add_argument('--pr', type=int)
    g.add_argument('--path', action='append', default=[])
    args = ap.parse_args(argv)
    if args.stage == 'provider':
        if not args.live:
            print('계획: key 확인 → count_tokens(설명·리뷰) → 예산 확인 → 설명·리뷰 각 1회 생성 → 출력 검증. 실제 호출은 --live와 --budget이 필요합니다(호출하지 않았습니다).')
            return 0
        key = os.environ.get('GITVIZ_SMOKE_API_KEY', '')
        from app.services.ai.providers import registry
        try:
            profile = registry.get_profile(args.profile)
        except registry.UnknownProfile:
            print('지원하지 않는 profile입니다.')
            return 2
        if profile.execution_location == 'local' and not registry.is_allowed(profile.id):
            print('AI_OLLAMA_ENABLED=1과 로컬 모델 설정이 필요합니다.')
            return 2
        if (profile.requires_api_key and not key) or args.budget is None or not args.budget.is_finite() or args.budget < 0 or (profile.requires_api_key and args.budget == 0):
            print('GITVIZ_SMOKE_API_KEY 환경변수와 --budget(>0)이 필요합니다. key는 명령줄 인자로 받지 않습니다.')
            return 2
        report = provider_smoke(key or None, args.profile, args.budget)
        print(render(report, 'AI 제공자 smoke'))
        return 0 if report['ok'] else 1
    if not args.live:
        print('계획: 공개 저장소 확인 → PR 목록 → PR 파일 목록·SHA → 고정 SHA 캡처 → 고정 코드 수집(읽기 전용). 실제 호출은 --live가 필요합니다(호출하지 않았습니다).')
        return 0
    if not (args.repo and '/' in args.repo and args.pr):
        print('--repo owner/name 과 --pr 번호가 필요합니다.')
        return 2
    report = github_smoke(args.repo, args.pr, args.path, os.environ.get('GITHUB_TOKEN') or None)
    print(render(report, 'GitHub 수집 smoke'))
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
