"""AI 인사이트: 자동 분석 리포트와 대화형 분석.

두 워크플로는 같은 읽기 전용 도구(insight_tools)와 같은 근거 검증을 쓰고, 실행 흐름만 다르다.

- 자동 분석: 범주(3.1~3.6)마다 **서버가 정한** 도구 묶음으로 근거를 모은 뒤 AI가 해석만 한다(범주당 1회, 검증 실패 시 1회 보완).
- 대화형 분석: AI가 JSON으로 "부를 도구와 인자"를 답하면 서버가 허용 목록의 도구만 실행해 결과를 돌려준다
  (최대 4라운드, 라운드당 4개). 모델에게 공급자의 tool 기능은 주지 않는다(기존 공급자 계층 원칙 유지).
- 근거 검증: AI가 인용한 수치·파일·커밋·기여자는 이번 실행의 도구 결과(Ledger)에 실제로 있어야 한다. 없으면 제거하거나
  그 인사이트·답변을 받지 않는다. 생성 시각·스냅샷·계산 버전·도구/프롬프트 버전을 결과에 함께 남긴다.
- 권한: 실행 전과 AI 호출 직전마다 저장소 접근·전송 정책(공개 저장소만 외부 전송)을 다시 확인한다. 저장소 원본은 읽기만 한다.
- 소스 코드: 실행을 만들 때 사용자가 켠 경우(include_code)에만 서버 clone의 기준 커밋 코드를 읽는 도구를 연다.
  코드 인용(relatedCode)은 이번 실행에서 실제로 읽은 줄 범위 안이어야 한다.
- key: 사용자의 API key는 앱이 보관하고 실행 요청마다 헤더로 보낸다. 서버는 그 실행의 메모리에서만 쓰고 저장하지 않는다
  (AD-32 ⑧). 그래서 실행은 Celery가 아니라 요청을 받은 API 프로세스의 백그라운드 스레드에서 한다.
"""
import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text

from app.collectors.git.clone_manager import repository_path
from app.schemas.ai import ErrorCode
from app.services.ai import prompts, token_budget
from app.services.ai.access import AIAction, AIError, authorize_ai
from app.services.ai.content_policy import POLICY_VERSION
from app.services.ai.insight_code import CodeReader
from app.services.ai.insight_tools import (TOOL_VERSION, TOOLS, InsightData, Ledger, ToolCall, run_tool,
                                           tool_catalog)
from app.services.ai.providers import registry
from app.services.ai.providers.base import GenerationRequest, ProviderError, TokenLimitExceeded
from app.services.analysis_snapshots import load_snapshot

logger = logging.getLogger('gitviz.ai.insights')

REPORT_SCHEMA_VERSION = 'insight-report-v1'
ANSWER_SCHEMA_VERSION = 'insight-answer-v1'
CATEGORIES = (
    ('overview', '프로젝트 종합 분석'),
    ('maintenance_risk', '기술 부채 및 유지보수 위험'),
    ('collaboration', '개발자 협업 분석'),
    ('change_impact', '코드 변경 영향도'),
    ('anomaly', '개발 이상 징후'),
    ('evolution', '프로젝트 진화'),
)
SEVERITIES = ('info', 'low', 'medium', 'high', 'critical')
CONFIDENCES = ('low', 'medium', 'high')
MAX_CHAT_ROUNDS = 4
MAX_CALLS_PER_ROUND = 4
MAX_CHAT_AI_CALLS = 7
MAX_QUESTION_CHARS = 1000
HISTORY_TURNS = 6
TOOL_RESULT_CHARS = 12_000
STALE_AFTER = timedelta(minutes=30)
ACTIVE = ('queued', 'running')
_FINISH_ERRORS = {'truncated': 'OUTPUT_TRUNCATED', 'refused': 'PROVIDER_REFUSED', 'empty': 'OUTPUT_INVALID',
                  'incomplete': 'OUTPUT_INVALID'}
_TEXT_LIMITS = {'title': 120, 'summary': 600, 'interpretation': 1000, 'recommendation': 600}
_CODE_EXT = ('py|js|mjs|cjs|ts|tsx|jsx|java|kt|kts|go|rs|rb|php|cs|cpp|cc|c|h|hpp|swift|scala|vue|svelte|html|css|scss|'
             'json|yml|yaml|md|sql|sh|ps1|toml|xml|gradle|ini|cfg|txt|lock')
_SHA_TOKEN = re.compile(r'(?<![0-9A-Za-z])[0-9a-f]{7,40}(?![0-9A-Za-z])')
_PATH_TOKEN = re.compile(r'(?<![A-Za-z0-9_.@/-])[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+')
_FILE_TOKEN = re.compile(r'(?<![A-Za-z0-9_./-])[A-Za-z0-9_-][A-Za-z0-9_.-]*\.(?:' + _CODE_EXT + r')(?![A-Za-z0-9_])')


class InsightFailed(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _action(profile_id: str) -> AIAction:
    return AIAction.local_generate if registry.get_profile(profile_id).execution_location == 'local' else AIAction.generate


# ── 근거 검증 ────────────────────────────────────────────────────────────

def unverified_mentions(texts, ledger: Ledger) -> list[str]:
    """본문에 쓰였지만 이번 도구 결과에 없는 커밋 SHA·파일 경로."""
    found = []
    for value in texts:
        if not isinstance(value, str):
            continue
        for token in _SHA_TOKEN.findall(value):
            if re.search(r'[a-f]', token) and re.search(r'\d', token) and ledger.match_sha(token) is None \
                    and not ledger.in_code(token):
                found.append(token)
        for token in _PATH_TOKEN.findall(value):
            token = token.rstrip('.')
            last = token.rsplit('/', 1)[-1]
            checked = token.count('/') >= 2 or re.search(r'\.[A-Za-z]{1,6}$', last)
            if checked and not ledger.known_path(token) and not ledger.in_code(token):
                found.append(token)
        for token in _FILE_TOKEN.findall(value):
            if '/' in token or ledger.known_path(token) or ledger.in_code(token):
                continue
            if not any(p == token or p.endswith('/' + token) for p in ledger.paths):
                found.append(token)
    return sorted(set(found))


def _clean_text(value, limit: int) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ''


def _clean_list(values, limit=20) -> list[str]:
    return [v.strip()[:300] for v in (values or []) if isinstance(v, str) and v.strip()][:limit]


@dataclass
class Checked:
    value: dict | None
    issues: list = field(default_factory=list)


def check_references(item: dict, ledger: Ledger, *, require_evidence: bool) -> tuple[dict, list[str], list[str]]:
    """근거·관련 항목을 도구 결과로 확인한다. (정리된 값, 받지 않을 이유, 제거한 항목)."""
    issues, removed = [], []
    evidence = []
    for e in item.get('evidence') or []:
        if not isinstance(e, dict):
            continue
        call_id, value = str(e.get('toolCallId') or '').strip(), _clean_text(e.get('value'), 200)
        if ledger.supports(call_id, value):
            call = ledger.calls[call_id]
            evidence.append({'toolCallId': call_id, 'tool': call.tool, 'label': _clean_text(e.get('label'), 200),
                             'value': value})
        else:
            removed.append(f'evidence:{call_id}={value}'[:120])
    files, commits, contributors = [], [], []
    for path in _clean_list(item.get('relatedFiles')):
        (files.append(path.strip('/')) if ledger.known_path(path) else removed.append(f'file:{path}'))
    for sha in _clean_list(item.get('relatedCommits')):
        full = ledger.match_sha(sha)
        (commits.append(full) if full else removed.append(f'commit:{sha}'))
    for name in _clean_list(item.get('relatedContributors')):
        known = ledger.known_contributor(name)
        (contributors.append(known) if known else removed.append(f'contributor:{name}'))
    code, seen = [], set()
    for ref in (item.get('relatedCode') or [])[:10]:
        if not isinstance(ref, dict):
            continue
        path = _clean_text(ref.get('path'), 300).strip('/')
        start, end = ref.get('startLine'), ref.get('endLine')
        if ledger.code_range(path, start, end):
            if (path, start, end) not in seen:
                seen.add((path, start, end))
                code.append({'path': path, 'startLine': start, 'endLine': end})
        else:
            removed.append(f'code:{path}:{start}-{end}'[:120])
    if require_evidence and not evidence:
        issues.append('검증된 근거(evidence)가 없습니다. 도구 결과에 있는 값을 그대로 인용하세요.')
    cleaned = {'evidence': evidence, 'relatedFiles': list(dict.fromkeys(files)),
               'relatedCommits': list(dict.fromkeys(commits)), 'relatedContributors': list(dict.fromkeys(contributors)),
               'relatedCode': code}
    return cleaned, issues, removed


def validate_insight(item, ledger: Ledger) -> Checked:
    if not isinstance(item, dict):
        return Checked(None, ['인사이트 형식이 올바르지 않습니다.'])
    texts = {k: _clean_text(item.get(k), limit) for k, limit in _TEXT_LIMITS.items()}
    issues = [f'{k}가 비어 있습니다.' for k in ('title', 'summary') if not texts[k]]
    severity, confidence = item.get('severity'), item.get('confidence')
    if severity not in SEVERITIES:
        issues.append('severity 값이 허용 목록에 없습니다.')
    if confidence not in CONFIDENCES:
        issues.append('confidence 값이 허용 목록에 없습니다.')
    references, problems, removed = check_references(item, ledger, require_evidence=True)
    issues += problems
    mentions = unverified_mentions(texts.values(), ledger)
    if mentions:
        issues.append('도구 결과에 없는 파일·커밋을 본문에 썼습니다: ' + ', '.join(mentions[:8]))
    if issues:
        return Checked(None, [f"[{texts['title'] or '제목 없음'}] {i}" for i in issues])
    if confidence == 'high' and len(references['evidence']) < 2:
        confidence = 'medium'                  # 근거 1개로는 높은 신뢰도를 주지 않는다
    return Checked({**texts, 'severity': severity, 'confidence': confidence, **references,
                    'validation': {'removedReferences': removed[:20]}})


def validate_answer(answer, ledger: Ledger) -> Checked:
    if not isinstance(answer, dict):
        return Checked(None, ['answer 형식이 올바르지 않습니다.'])
    body = _clean_text(answer.get('text'), 6000)
    if not body:
        return Checked(None, ['answer.text가 비어 있습니다.'])
    blocks = []
    for b in (answer.get('blocks') or [])[:6]:
        if not isinstance(b, dict) or b.get('type') not in ('table', 'list', 'stat'):
            continue
        block = {'type': b['type'], 'title': _clean_text(b.get('title'), 120)}
        if b['type'] == 'table':
            columns = _clean_list(b.get('columns'), 8)
            rows = [[_clean_text(c, 300) for c in row][:len(columns) or 8]
                    for row in (b.get('rows') or [])[:30] if isinstance(row, list)]
            if not columns or not rows:
                continue
            block.update(columns=columns, rows=rows)
        elif b['type'] == 'list':
            items = _clean_list(b.get('items'), 30)
            if not items:
                continue
            block['items'] = items
        else:
            value = _clean_text(b.get('value'), 80)
            if not value:
                continue
            block.update(value=value, unit=_clean_text(b.get('unit'), 20))
        blocks.append(block)
    succeeded = any(c.ok for c in ledger.calls.values())
    references, issues, removed = check_references(answer, ledger, require_evidence=succeeded)
    strings = [body] + [s for b in blocks for s in ([b['title']] + b.get('columns', []) + b.get('items', [])
                                                     + [c for row in b.get('rows', []) for c in row] + [b.get('value', '')])]
    mentions = unverified_mentions(strings, ledger)
    if mentions:
        issues.append('도구 결과에 없는 파일·커밋을 썼습니다: ' + ', '.join(mentions[:8]))
    if issues:
        return Checked(None, issues)
    return Checked({'text': body, 'blocks': blocks, **references,
                    'limitations': _clean_list(answer.get('limitations'), 10),
                    'validation': {'removedReferences': removed[:20]}})


# ── AI 호출 ──────────────────────────────────────────────────────────────

def _fit(result: dict, limit: int = TOOL_RESULT_CHARS) -> str:
    """도구 결과를 프롬프트 한도에 맞춘다. 가장 긴 목록부터 절반으로 줄이고 줄였다는 표시를 남긴다."""
    data = json.loads(json.dumps(result, ensure_ascii=False, default=str))
    serialized = json.dumps(data, ensure_ascii=False)
    for _ in range(12):
        if len(serialized) <= limit:
            return serialized
        lists = []

        def visit(node, parent, key):
            if isinstance(node, dict):
                for k, v in node.items():
                    visit(v, node, k)
            elif isinstance(node, list):
                if len(node) > 1:
                    lists.append((len(json.dumps(node, ensure_ascii=False)), parent, key))
                for v in node:
                    visit(v, None, None)
        visit(data, None, None)
        lists = [x for x in lists if x[1] is not None]
        if not lists:
            break
        _, parent, key = max(lists, key=lambda x: x[0])
        parent[key] = parent[key][:len(parent[key]) // 2]
        data['_truncated'] = True
        serialized = json.dumps(data, ensure_ascii=False)
    return serialized[:limit]


def _call_block(boundary: str, call: ToolCall) -> str:
    args = json.dumps(call.args, ensure_ascii=False)
    return f'<<<{boundary}:BEGIN call={call.call_id} tool={call.tool} args={args}>>>\n{_fit(call.result)}\n<<<{boundary}:END>>>'


@dataclass
class InsightRun:
    """한 번의 실행(리포트 하나 또는 질문 하나)에서 쓰는 provider·도구·비용."""
    provider: object
    data: InsightData
    owner_id: UUID
    repository_id: int
    profile_id: str
    session_factory: object
    ledger: Ledger = field(default_factory=Ledger)
    calls: int = 0
    cost: Decimal = Decimal(0)
    cost_known: bool = True
    next_call: int = 1
    db: object = None                                  # 도구 조회용 세션(실행이 끝나면 닫는다)
    code_requested: bool = False                       # 사용자가 이 실행에서 코드 열람을 켰는가

    def tool(self, name: str, args: dict) -> ToolCall:
        call_id = f'T{self.next_call}'
        self.next_call += 1
        clean = {k: v for k, v in (args or {}).items() if v not in ('', None, 0)}
        call = ToolCall(call_id, name, clean, run_tool(self.data, name, clean))
        self.ledger.add(call)
        return call

    def authorize(self) -> None:
        with self.session_factory() as db:
            try:
                authorize_ai(db, self.owner_id, self.repository_id, _action(self.profile_id), POLICY_VERSION,
                             insight=True)
            except HTTPException:
                raise InsightFailed('REAUTH_REQUIRED') from None
            except AIError as exc:
                raise InsightFailed(exc.code.value) from None

    def generate(self, kind: str, system: str, user: str) -> dict:
        self.authorize()                                    # 외부(또는 로컬 모델)로 보내기 직전마다 다시 확인한다
        request = GenerationRequest(kind=kind, system=system, user=user)
        try:
            token_budget.check_input_limit(self.provider.profile, self.provider.estimate_tokens(request))
        except TokenLimitExceeded:
            raise InsightFailed('INPUT_TOO_LARGE') from None
        self.calls += 1
        try:
            response = self.provider.generate(request)
        except ProviderError as err:
            raise InsightFailed(err.failure.error_code) from None
        except Exception as exc:
            logger.error('insight provider call crashed error=%s', type(exc).__name__)
            raise InsightFailed('PROVIDER_ERROR') from None
        if response.cost is None:
            self.cost_known = False
        else:
            self.cost += Decimal(str(response.cost))
        if response.finish != 'complete':
            raise InsightFailed(_FINISH_ERRORS.get(response.finish, 'OUTPUT_INVALID'))
        parsed = response.parsed
        expected = self.provider.profile.schema_version(kind)
        if not isinstance(parsed, dict) or parsed.get('schema_version') != expected:
            return {}
        return parsed


def _basis(session: InsightRun, kind: str) -> dict:
    data, profile = session.data, session.provider.profile
    snap = data.snapshot
    code = data.code
    return {'snapshotId': snap.id, 'targetSha': snap.target_sha, 'windowStart': data.period['start'],
            'windowEnd': data.period['end'], 'formulaVersion': snap.formula_version,
            'payloadSha256': snap.payload_sha256, 'toolVersion': TOOL_VERSION,
            'promptVersion': profile.prompt_version(kind), 'provider': profile.provider, 'model': profile.model,
            'profileId': profile.id,
            'code': {'requested': session.code_requested, 'available': code is not None,
                     'charsSent': code.chars_used if code else 0, 'filesRead': sorted(code.files_read) if code else []}}


# ── 자동 분석 ────────────────────────────────────────────────────────────

def _top_paths(data: InsightData, count: int) -> list[str]:
    coupled = {p['file_a_id'] for p in (data.payload.get('coupling') or {}).get('pairs') or []} | \
              {p['file_b_id'] for p in (data.payload.get('coupling') or {}).get('pairs') or []}
    files = [f for f in (data.payload.get('risk-scores') or {}).get('files') or []
             if f.get('maintenance_priority') is not None and f['file_id'] in coupled]
    files.sort(key=lambda f: (-f['maintenance_priority'], f['path']))
    return [f['path'] for f in files[:count]]


def _top_contributors(data: InsightData, count: int) -> list[str]:
    rows = (data.payload.get('top-contributors') or {}).get('contributors') or []
    return [data.contributor_name(c['id']) for c in rows[:count]]


def _stem(path: str) -> str:
    name = path.rsplit('/', 1)[-1]
    return name.rsplit('.', 1)[0] if '.' in name[1:] else name


def code_plan(category: str, data: InsightData) -> list[tuple[str, dict]]:
    """코드 열람을 켠 실행에서 범주별로 더 읽는 코드. 기준 커밋에 없는 파일은 건너뛴다."""
    if data.code is None:
        return []
    try:
        tree = data.code.tree()
    except (OSError, RuntimeError):
        return []
    if category == 'overview':
        readme = next((p for p in ('README.md', 'README.rst', 'README.txt', 'README', 'readme.md') if p in tree), None)
        return [('listDirectory', {})] + ([('readFile', {'path': readme, 'endLine': 80})] if readme else [])
    if category == 'maintenance_risk':
        files = sorted((f for f in (data.payload.get('risk-scores') or {}).get('files') or []
                        if f.get('maintenance_priority') is not None and f['path'] in tree),
                       key=lambda f: (-f['maintenance_priority'], f['path']))
        return [('readFile', {'path': f['path'], 'endLine': 150}) for f in files[:2]]
    if category == 'change_impact':
        plan = []
        for pair in ((data.payload.get('coupling') or {}).get('pairs') or [])[:6]:
            a, b = pair.get('file_a_path'), pair.get('file_b_path')
            if a in tree and b in tree and len(_stem(b)) >= 3:
                plan.append(('searchCode', {'query': _stem(b), 'directory': a}))   # 공동 변경 파일이 코드에서 서로를 참조하는가
            if len(plan) == 3:
                break
        return plan
    return []


def evidence_plan(category: str, data: InsightData) -> list[tuple[str, dict]]:
    """범주별로 서버가 정한 근거 수집 계획(코드 단계 포함). AI는 이 단계를 바꿀 수 없다."""
    return metric_plan(category, data) + code_plan(category, data)


def metric_plan(category: str, data: InsightData) -> list[tuple[str, dict]]:
    """범주별로 서버가 정한 지표 조회 계획."""
    if category == 'overview':
        return [('getProjectOverview', {}), ('getCommitTimeline', {}), ('getCommitTypeStatistics', {}),
                ('getDirectoryStatistics', {'depth': 1, 'limit': 10}), ('getContributorStatistics', {'limit': 10})]
    if category == 'maintenance_risk':
        return [('getFileChangeFrequency', {'sortBy': 'maintenance_priority', 'limit': 15}),
                ('getFileChangeFrequency', {'sortBy': 'bugfix_count', 'limit': 10}),
                ('getDirectoryStatistics', {'depth': 2, 'limit': 10}), ('getOwnershipConcentration', {'limit': 10}),
                ('getTopCoupledPairs', {'limit': 10})]
    if category == 'collaboration':
        plan = [('getContributorStatistics', {'limit': 15}), ('getTopCollaborationPairs', {'limit': 15}),
                ('getOwnershipConcentration', {'limit': 10})]
        return plan + [('getDeveloperActivity', {'contributor': name, 'limit': 5}) for name in _top_contributors(data, 3)]
    if category == 'change_impact':
        plan = []
        for path in _top_paths(data, 3):
            plan += [('getCoChangedFiles', {'path': path, 'limit': 10}), ('getFileChangeHistory', {'path': path, 'limit': 5})]
        return plan or [('getTopCoupledPairs', {'limit': 10})]
    if category == 'anomaly':
        return [('compareDevelopmentPeriods', {'recentMonths': 1, 'baselineMonths': 3, 'limit': 10}),
                ('compareDevelopmentPeriods', {'recentMonths': 3, 'baselineMonths': 3, 'limit': 10}),
                ('getCommitTimeline', {})]
    if category == 'evolution':
        return [('getProjectEvolution', {'limit': 10}), ('getCommitTypeStatistics', {}),
                ('getDirectoryStatistics', {'depth': 1, 'limit': 10})]
    raise ValueError(category)


def _report_prompt(category: str, label: str, data: InsightData, calls: list[ToolCall], feedback: list[str]) -> tuple[str, str]:
    corpus = [_fit(c.result) for c in calls]
    boundary = prompts.new_boundary(corpus + feedback)
    system = prompts.system_template('insight_report', 'insight_report-v1').replace(prompts.PLACEHOLDER, boundary)
    parts = [f'Category: {category} ({label})',
             f"Analysis period: {data.period['start']} to {data.period['end']} (snapshot {data.snapshot.id})",
             _code_status(data),
             'Tool results (call ids are used in evidence.toolCallId):']
    parts += [_call_block(boundary, c) for c in calls]
    if feedback:
        parts.append('Your previous answer was rejected by GitViz validation. Fix these problems and answer again:')
        parts += [f'- {f}' for f in feedback[:15]]
    parts.append('Respond with the JSON object only.')
    return system, '\n'.join(parts)


def _report_category(session: InsightRun, category: str, label: str, progress) -> dict:
    progress({'stage': 'category', 'category': category, 'label': label, 'status': 'running'})
    calls = []
    for name, args in evidence_plan(category, session.data):
        call = session.tool(name, args)
        calls.append(call)
        progress({'stage': 'tool', 'category': category, 'callId': call.call_id, 'tool': name, 'args': call.args,
                  'status': 'done' if call.ok else 'error'})
    feedback, accepted, rejected, limitations = [], [], 0, []
    for attempt in range(2):
        progress({'stage': 'ai', 'category': category, 'status': 'running', 'attempt': attempt + 1})
        system, user = _report_prompt(category, label, session.data, calls, feedback)
        parsed = session.generate('insight_report', system, user)
        items = parsed.get('insights') if isinstance(parsed.get('insights'), list) else None
        if items is None:
            feedback = ['응답이 요구한 JSON 형식(schema_version: insight_report-output-v1)이 아닙니다.']
            continue
        checked = [validate_insight(i, session.ledger) for i in items[:5]]
        accepted = [c.value for c in checked if c.value]
        rejected = sum(1 for c in checked if not c.value)
        limitations = _clean_list(parsed.get('limitations'), 10)
        feedback = [issue for c in checked for issue in c.issues]
        if not feedback:
            break
    if parsed.get('insights') is None:
        limitations.append('AI 응답이 형식 검증을 통과하지 못해 이 범주의 인사이트를 만들지 못했습니다.')
    if rejected:
        limitations.append(f'근거 검증을 통과하지 못한 인사이트 {rejected}개는 제외했습니다.')
    stamp = _now().isoformat()
    insights = [{'id': uuid.uuid4().hex[:12], 'category': category, **item, 'generatedAt': stamp,
                 'analysisPeriod': {'start': session.data.period['start'], 'end': session.data.period['end']},
                 'links': session.data.link_ids(item['relatedFiles'] + [c['path'] for c in item['relatedCode']],
                                                   item['relatedContributors'])}
                for item in accepted]
    progress({'stage': 'category', 'category': category, 'label': label, 'status': 'done', 'insights': len(insights)})
    return {'key': category, 'label': label, 'insights': insights, 'limitations': limitations,
            'rejectedCount': rejected,
            'toolCalls': [{'callId': c.call_id, 'tool': c.tool, 'args': c.args, 'ok': c.ok} for c in calls]}


def build_report(session: InsightRun, progress) -> dict:
    categories = [_report_category(session, key, label, progress) for key, label in CATEGORIES]
    order = {s: i for i, s in enumerate(reversed(SEVERITIES))}
    every = [i for c in categories for i in c['insights']]
    highlights = sorted(every, key=lambda i: (order[i['severity']], -CONFIDENCES.index(i['confidence'])))[:5]
    return {'schemaVersion': REPORT_SCHEMA_VERSION, 'generatedAt': _now().isoformat(),
            'analysisPeriod': {'start': session.data.period['start'], 'end': session.data.period['end']},
            'basis': _basis(session, 'insight_report'), 'categories': categories,
            'highlights': [i['id'] for i in highlights],
            'notes': ['근거(evidence)는 GitViz가 계산한 값이고, 해석(interpretation)과 추천은 AI의 의견입니다.',
                      '공동 변경은 코드 의존성이, 유지보수 우선순위는 결함 확률이, BF80은 실제 지식 인원이 아닙니다.']}


# ── 대화형 분석 ──────────────────────────────────────────────────────────

def _code_status(data: InsightData, requested: bool = True) -> str:
    if data.code is not None:
        return (f'Source code access: ON. Code tools read files at the snapshot commit {data.code.sha[:12]}. '
                'Cite code you rely on in relatedCode with the exact line range you were shown.')
    if requested:
        return ('Source code access: requested but UNAVAILABLE (the server has no readable copy of this commit). '
                'Say so if the question needs code.')
    return 'Source code access: OFF. You cannot read source code; questions about code contents cannot be answered.'


def _chat_prompt(session: InsightRun, history: list[dict], question: str, round_no: int, last: bool,
                 feedback: list[str]) -> tuple[str, str]:
    calls = list(session.ledger.calls.values())
    history_text = '\n'.join(f"[{h['role']}] {h['text']}" for h in history) or '(none)'
    corpus = [history_text, question] + [_fit(c.result) for c in calls] + feedback
    boundary = prompts.new_boundary(corpus)
    system = prompts.system_template('insight_chat', 'insight_chat-v1').replace(prompts.PLACEHOLDER, boundary)
    parts = ['Available tools (read-only, this repository and snapshot only):',
             tool_catalog(include_code=session.data.code is not None), _code_status(session.data, session.code_requested),
             f"Analysis period: {session.data.period['start']} to {session.data.period['end']} "
             f'(snapshot {session.data.snapshot.id}).',
             f'Round {round_no} of at most {MAX_CHAT_ROUNDS + 1}.'
             + (' This is the last round: reply with action "answer" now using the results you have.' if last else ''),
             f'<<<{boundary}:BEGIN history>>>\n{history_text}\n<<<{boundary}:END>>>',
             f'<<<{boundary}:BEGIN question>>>\n{question}\n<<<{boundary}:END>>>']
    if calls:
        parts.append('Tool results so far:')
        parts += [_call_block(boundary, c) for c in calls]
    if feedback:
        parts.append('Your previous answer was rejected by GitViz validation. Fix these problems:')
        parts += [f'- {f}' for f in feedback[:15]]
    parts.append('Respond with the JSON object only.')
    return system, '\n'.join(parts)


def answer_question(session: InsightRun, history: list[dict], question: str, progress) -> dict:
    feedback: list[str] = []
    round_no, tool_rounds, repaired = 0, 0, False
    seen = set()
    while session.calls < MAX_CHAT_AI_CALLS:
        round_no += 1
        last = tool_rounds >= MAX_CHAT_ROUNDS or session.calls >= MAX_CHAT_AI_CALLS - 1
        progress({'stage': 'ai', 'status': 'running', 'round': round_no})
        system, user = _chat_prompt(session, history, question, round_no, last, feedback)
        parsed = session.generate('insight_chat', system, user)
        action = parsed.get('action')
        if action == 'tools' and not last and isinstance(parsed.get('calls'), list) and parsed['calls']:
            tool_rounds += 1
            feedback = []
            for request in parsed['calls'][:MAX_CALLS_PER_ROUND]:
                if not isinstance(request, dict):
                    continue
                name = str(request.get('tool') or '')
                args = request.get('args') if isinstance(request.get('args'), dict) else {}
                key = (name, json.dumps({k: v for k, v in args.items() if v not in ('', None, 0)}, sort_keys=True))
                if key in seen:
                    continue
                seen.add(key)
                reason = _clean_text(request.get('reason'), 200)
                progress({'stage': 'tool', 'status': 'running', 'tool': name if name in TOOLS else 'unknown', 'reason': reason})
                call = session.tool(name, args)
                progress({'stage': 'tool', 'status': 'done' if call.ok else 'error', 'callId': call.call_id,
                          'tool': call.tool if call.tool in TOOLS else 'unknown', 'args': call.args, 'reason': reason})
            continue
        if action != 'answer':
            if repaired:
                raise InsightFailed('OUTPUT_INVALID')
            repaired, feedback = True, ['응답이 요구한 JSON 형식(schema_version: insight_chat-output-v1)이 아닙니다.']
            continue
        progress({'stage': 'validate', 'status': 'running'})
        checked = validate_answer(parsed.get('answer'), session.ledger)
        if checked.value:
            return checked.value
        if repaired:
            raise InsightFailed('OUTPUT_UNVERIFIED')
        repaired, feedback = True, checked.issues
    raise InsightFailed('TOO_MANY_STEPS')


# ── 실행·저장 (worker) ───────────────────────────────────────────────────

def _progress_writer(session_factory, table: str, row_id: UUID):
    events: list[dict] = []

    def write(event: dict) -> None:
        events.append({'at': _now().isoformat(), **event})
        with session_factory() as db:
            db.execute(text(f'UPDATE {table} SET progress = CAST(:p AS jsonb) WHERE id = :id'),
                       {'p': json.dumps(events[-60:], ensure_ascii=False, default=str), 'id': row_id})
            db.commit()
    return write


def code_repository_path(repository_id: int):
    """수집 worker가 유지하는 clone 경로(시험에서 바꿀 수 있도록 분리)."""
    try:
        return repository_path(repository_id)
    except ValueError:
        return None


def _open(session_factory, owner_id, repository_id, snapshot_id, profile_id, provider_factory,
          include_code: bool = False) -> InsightRun:
    with session_factory() as db:
        try:
            authorize_ai(db, owner_id, repository_id, _action(profile_id), POLICY_VERSION, insight=True)
        except AIError as exc:
            raise InsightFailed(exc.code.value) from None
        except HTTPException:
            raise InsightFailed('REAUTH_REQUIRED') from None
        if provider_factory is None:                         # key는 요청과 함께 온 메모리에만 있다(저장하지 않음)
            raise InsightFailed('CREDENTIAL_UNAVAILABLE')
        try:
            provider = provider_factory(db, owner_id, profile_id)
        except (registry.UnknownProfile, ValueError):
            raise InsightFailed('CREDENTIAL_UNAVAILABLE') from None
    db = session_factory()
    try:
        snapshot = load_snapshot(db, repository_id, snapshot_id)
    except HTTPException:
        db.close()
        raise InsightFailed('SNAPSHOT_UNAVAILABLE') from None
    code = CodeReader.open(code_repository_path(repository_id), snapshot.target_sha) if include_code else None
    return InsightRun(provider=provider, data=InsightData(db, snapshot, code), owner_id=owner_id,
                      repository_id=repository_id, profile_id=profile_id, session_factory=session_factory, db=db,
                      code_requested=include_code)


def _close(session: InsightRun | None) -> None:
    if session is not None and session.db is not None:
        session.db.close()


def _cost(session: InsightRun):
    return session.cost if session.cost_known and session.calls else None


def run_report(report_id: UUID, session_factory, provider_factory=None) -> str:
    with session_factory() as db:
        row = db.execute(text("""UPDATE ai_insight_reports SET status = 'running', started_at = now()
                                 WHERE id = :id AND status = 'queued' AND deleted_at IS NULL
                                 RETURNING owner_user_id, repository_id, snapshot_id, provider_profile, include_code"""),
                         {'id': report_id}).one_or_none()
        db.commit()
    if row is None:
        return 'skipped'
    progress = _progress_writer(session_factory, 'ai_insight_reports', report_id)
    session = None
    try:
        session = _open(session_factory, row.owner_user_id, row.repository_id, row.snapshot_id, row.provider_profile,
                        provider_factory, row.include_code)
        result = build_report(session, progress)
        session.authorize()                                  # 실행 중 접근 권한을 잃었다면 결과를 남기지 않는다
        with session_factory() as db:
            db.execute(text("""UPDATE ai_insight_reports SET status = 'succeeded', result = CAST(:r AS jsonb),
                               finished_at = now(), call_count = :n, cost = :c WHERE id = :id AND status = 'running'"""),
                       {'r': json.dumps(result, ensure_ascii=False, default=str), 'n': session.calls,
                        'c': _cost(session), 'id': report_id})
            db.commit()
        return 'succeeded'
    except InsightFailed as exc:
        _fail(session_factory, 'ai_insight_reports', report_id, exc.code, session)
        return exc.code
    except Exception as exc:                                 # 원문 예외는 저장·노출하지 않는다
        logger.error('insight report failed id=%s error=%s', report_id, type(exc).__name__)
        _fail(session_factory, 'ai_insight_reports', report_id, 'INTERNAL_ERROR', session)
        return 'INTERNAL_ERROR'
    finally:
        _close(session)


def run_answer(message_id: UUID, session_factory, provider_factory=None) -> str:
    with session_factory() as db:
        row = db.execute(text("""
            UPDATE ai_insight_messages m SET status = 'running'
            FROM ai_insight_conversations c
            WHERE m.id = :id AND m.status = 'queued' AND m.role = 'assistant' AND c.id = m.conversation_id
              AND c.deleted_at IS NULL
            RETURNING m.conversation_id, m.seq, c.owner_user_id, c.repository_id, c.snapshot_id, c.provider_profile,
                      c.include_code"""),
                         {'id': message_id}).one_or_none()
        db.commit()
        if row is None:
            return 'skipped'
        turns = db.execute(text("""SELECT role, content, status FROM ai_insight_messages
                                   WHERE conversation_id = :c AND seq < :s ORDER BY seq"""),
                           {'c': row.conversation_id, 's': row.seq}).all()
    question = (turns[-1].content or {}).get('text', '') if turns and turns[-1].role == 'user' else ''
    history = [{'role': t.role, 'text': _clean_text((t.content or {}).get('text'), 1500)}
               for t in turns[:-1] if t.status == 'succeeded' and (t.content or {}).get('text')][-HISTORY_TURNS:]
    progress = _progress_writer(session_factory, 'ai_insight_messages', message_id)
    session = None
    try:
        if not question:
            raise InsightFailed('INVALID_STATE')
        session = _open(session_factory, row.owner_user_id, row.repository_id, row.snapshot_id, row.provider_profile,
                        provider_factory, row.include_code)
        answer = answer_question(session, history, question, progress)
        session.authorize()
        profile = session.provider.profile
        content = {'schemaVersion': ANSWER_SCHEMA_VERSION, **answer,
                   'evidence': answer['evidence'],
                   'links': session.data.link_ids(answer['relatedFiles'] + [c['path'] for c in answer['relatedCode']],
                                                 answer['relatedContributors']),
                   'toolCalls': [{'callId': c.call_id, 'tool': c.tool, 'args': c.args, 'ok': c.ok}
                                 for c in session.ledger.calls.values()],
                   'analysisPeriod': {'start': session.data.period['start'], 'end': session.data.period['end']},
                   'basis': _basis(session, 'insight_chat'), 'generatedAt': _now().isoformat()}
        with session_factory() as db:
            db.execute(text("""UPDATE ai_insight_messages SET status = 'succeeded', content = CAST(:c AS jsonb),
                               finished_at = now(), call_count = :n, cost = :cost, provider = :p, model = :m,
                               prompt_version = :pv, tool_version = :tv WHERE id = :id AND status = 'running'"""),
                       {'c': json.dumps(content, ensure_ascii=False, default=str), 'n': session.calls,
                        'cost': _cost(session), 'p': profile.provider, 'm': profile.model,
                        'pv': profile.prompt_version('insight_chat'), 'tv': TOOL_VERSION, 'id': message_id})
            db.execute(text('UPDATE ai_insight_conversations SET updated_at = now() WHERE id = :c'),
                       {'c': row.conversation_id})
            db.commit()
        return 'succeeded'
    except InsightFailed as exc:
        _fail(session_factory, 'ai_insight_messages', message_id, exc.code, session)
        return exc.code
    except Exception as exc:
        logger.error('insight answer failed id=%s error=%s', message_id, type(exc).__name__)
        _fail(session_factory, 'ai_insight_messages', message_id, 'INTERNAL_ERROR', session)
        return 'INTERNAL_ERROR'
    finally:
        _close(session)


def _fail(session_factory, table: str, row_id: UUID, code: str, session: InsightRun | None) -> None:
    with session_factory() as db:
        db.execute(text(f"""UPDATE {table} SET status = 'failed', error_code = :code, finished_at = now(),
                            call_count = :n, cost = :c WHERE id = :id AND status IN ('queued', 'running')"""),
                   {'code': code[:64], 'n': session.calls if session else 0,
                    'c': _cost(session) if session else None, 'id': row_id})
        db.commit()


def provider_factory(api_key: str | None):
    """요청에 실려 온 사용자 key로 provider를 만드는 함수. key는 이 함수의 클로저(메모리)에만 있고 DB·큐·로그에 남지 않는다."""
    def build(_db, _owner_id, profile_id: str):
        if not registry.is_allowed(profile_id):
            raise ValueError('profile_not_allowed')
        profile = registry.get_profile(profile_id)
        if profile.requires_api_key and not api_key:
            raise ValueError('no_key')
        return registry.create_provider(profile, api_key=api_key if profile.requires_api_key else None)
    return build


# ── 접수·조회 (API) ──────────────────────────────────────────────────────

def resolve_profile(profile_id: str, provider: str | None = None):
    """허용된 모델인지, 요청이 밝힌 제공자와 같은지 확인한다(앱이 엉뚱한 제공자의 key를 붙이지 않게)."""
    try:
        profile = registry.get_profile(profile_id)
    except registry.UnknownProfile:
        raise AIError(ErrorCode.INVALID_SELECTION, '지원하지 않는 모델입니다.') from None
    if not registry.is_allowed(profile_id):
        raise AIError(ErrorCode.INVALID_SELECTION, '이 서버에서 허용하지 않은 모델입니다.')
    if provider is not None and provider != profile.provider:
        raise AIError(ErrorCode.INVALID_SELECTION, '모델과 제공자가 맞지 않습니다.')
    return profile


def models() -> list[dict]:
    """AI 분석에 고를 수 있는 모델. key는 앱이 보관하므로 서버는 key 등록 여부를 모른다."""
    return [{k: m[k] for k in ('profile_id', 'provider', 'display_name', 'execution_location', 'requires_api_key',
                               'currency', 'input_price_per_mtok', 'output_price_per_mtok')}
            for p in registry.supported_options() for m in p['models'] if m['allowed']]


def _require_profile(db, owner_id: UUID, repository_id: int, profile_id: str, provider: str | None = None):
    profile = resolve_profile(profile_id, provider)
    authorize_ai(db, owner_id, repository_id, _action(profile_id), POLICY_VERSION, insight=True)
    return profile


def _snapshot_id(db, repository_id: int, snapshot_id: int | None) -> int:
    try:
        return load_snapshot(db, repository_id, snapshot_id).id
    except HTTPException:
        raise AIError(ErrorCode.RESOURCE_NOT_FOUND, '저장된 분석 결과가 없습니다. 먼저 분석을 실행해 주세요.') from None


def _expire_stale(db, table: str, where: str, params: dict) -> None:
    """worker가 끝내지 못한 실행은 일정 시간이 지나면 실패로 표시한다(자동으로 다시 호출하지 않는다)."""
    db.execute(text(f"""UPDATE {table} SET status = 'failed', error_code = 'RUN_TIMEOUT', finished_at = now()
                        WHERE status IN ('queued', 'running') AND created_at < :cutoff AND {where}"""),
               {**params, 'cutoff': _now() - STALE_AFTER})
    db.commit()


def create_report(db, owner_id: UUID, repository_id: int, snapshot_id: int | None, profile_id: str,
                  include_code: bool = False, provider: str | None = None) -> UUID:
    profile = _require_profile(db, owner_id, repository_id, profile_id, provider)
    snapshot_id = _snapshot_id(db, repository_id, snapshot_id)
    _expire_stale(db, 'ai_insight_reports', 'owner_user_id = :o', {'o': owner_id})
    active = db.execute(text("""SELECT 1 FROM ai_insight_reports WHERE owner_user_id = :o AND repository_id = :r
                                AND status IN ('queued', 'running') AND deleted_at IS NULL LIMIT 1"""),
                        {'o': owner_id, 'r': repository_id}).first()
    if active:
        raise AIError(ErrorCode.INVALID_STATE, '이 저장소의 AI 자동 분석이 이미 진행 중입니다.')
    report_id = db.execute(text("""
        INSERT INTO ai_insight_reports (owner_user_id, repository_id, snapshot_id, provider_profile, provider, model,
                                        prompt_version, tool_version, policy_version, include_code)
        VALUES (:o, :r, :s, :p, :prov, :m, :pv, :tv, :pol, :code) RETURNING id"""),
                           {'o': owner_id, 'r': repository_id, 's': snapshot_id, 'p': profile.id, 'code': bool(include_code),
                            'prov': profile.provider, 'm': profile.model, 'pv': profile.prompt_version('insight_report'),
                            'tv': TOOL_VERSION, 'pol': POLICY_VERSION}).scalar_one()
    db.commit()
    return report_id


def _report_view(row, *, full: bool) -> dict:
    view = {'id': str(row.id), 'repositoryId': row.repository_id, 'snapshotId': row.snapshot_id, 'status': row.status,
            'providerProfile': row.provider_profile, 'provider': row.provider, 'model': row.model,
            'errorCode': row.error_code, 'callCount': row.call_count, 'includeCode': row.include_code,
            'cost': str(row.cost) if row.cost is not None else None,
            'createdAt': row.created_at.isoformat(), 'finishedAt': row.finished_at.isoformat() if row.finished_at else None,
            'progress': row.progress if full or row.status in ACTIVE else []}
    if full:
        view['result'] = row.result
    return view


_REPORT_COLUMNS = """id, repository_id, snapshot_id, status, provider_profile, provider, model, error_code, call_count,
                     cost, created_at, finished_at, progress, include_code"""


def list_reports(db, owner_id: UUID, repository_id: int, limit: int = 20) -> list[dict]:
    _expire_stale(db, 'ai_insight_reports', 'owner_user_id = :o', {'o': owner_id})
    rows = db.execute(text(f"""SELECT {_REPORT_COLUMNS} FROM ai_insight_reports
                               WHERE owner_user_id = :o AND repository_id = :r AND deleted_at IS NULL
                               ORDER BY created_at DESC LIMIT :l"""),
                      {'o': owner_id, 'r': repository_id, 'l': limit}).all()
    return [_report_view(r, full=False) for r in rows]


def get_report(db, owner_id: UUID, report_id: UUID) -> dict:
    _expire_stale(db, 'ai_insight_reports', 'id = :i', {'i': report_id})
    row = db.execute(text(f"""SELECT {_REPORT_COLUMNS}, result, owner_user_id FROM ai_insight_reports
                              WHERE id = :i AND deleted_at IS NULL"""), {'i': report_id}).one_or_none()
    if row is None or row.owner_user_id != owner_id:
        raise AIError(ErrorCode.RESOURCE_NOT_FOUND, '자료를 찾을 수 없습니다.')
    return _report_view(row, full=True)


def report_repository(db, owner_id: UUID, report_id: UUID) -> int:
    row = db.execute(text('SELECT repository_id, owner_user_id FROM ai_insight_reports WHERE id = :i AND deleted_at IS NULL'),
                     {'i': report_id}).one_or_none()
    if row is None or row.owner_user_id != owner_id:
        raise AIError(ErrorCode.RESOURCE_NOT_FOUND, '자료를 찾을 수 없습니다.')
    return row.repository_id


def create_conversation(db, owner_id: UUID, repository_id: int, snapshot_id: int | None, profile_id: str,
                        include_code: bool = False) -> UUID:
    profile = _require_profile(db, owner_id, repository_id, profile_id)
    snapshot_id = _snapshot_id(db, repository_id, snapshot_id)
    conversation_id = db.execute(text("""
        INSERT INTO ai_insight_conversations (owner_user_id, repository_id, snapshot_id, provider_profile, include_code)
        VALUES (:o, :r, :s, :p, :code) RETURNING id"""), {'o': owner_id, 'r': repository_id, 's': snapshot_id,
                                                         'p': profile.id, 'code': bool(include_code)}).scalar_one()
    db.commit()
    return conversation_id


def _conversation(db, owner_id: UUID, conversation_id: UUID):
    row = db.execute(text("""SELECT id, owner_user_id, repository_id, snapshot_id, provider_profile, title, created_at,
                                    updated_at, include_code FROM ai_insight_conversations
                             WHERE id = :i AND deleted_at IS NULL"""),
                     {'i': conversation_id}).one_or_none()
    if row is None or row.owner_user_id != owner_id:
        raise AIError(ErrorCode.RESOURCE_NOT_FOUND, '대화를 찾을 수 없습니다.')
    return row


def conversation_repository(db, owner_id: UUID, conversation_id: UUID) -> int:
    return _conversation(db, owner_id, conversation_id).repository_id


def conversation_profile(db, owner_id: UUID, conversation_id: UUID):
    """질문을 받기 전에 대화의 모델을 확인한다(요청에 붙은 key가 그 모델의 제공자용인지 보기 위해)."""
    conversation = _conversation(db, owner_id, conversation_id)
    if not registry.is_allowed(conversation.provider_profile):
        raise AIError(ErrorCode.INVALID_SELECTION, '이 대화의 모델은 더 이상 허용되지 않습니다. 새 대화를 시작해 주세요.')
    return registry.get_profile(conversation.provider_profile)


def ask(db, owner_id: UUID, conversation_id: UUID, question: str) -> UUID:
    conversation = _conversation(db, owner_id, conversation_id)
    question = (question or '').strip()
    if not question or len(question) > MAX_QUESTION_CHARS:
        raise AIError(ErrorCode.INVALID_SELECTION, f'질문은 1~{MAX_QUESTION_CHARS}자로 입력해 주세요.')
    authorize_ai(db, owner_id, conversation.repository_id, _action(conversation.provider_profile), POLICY_VERSION,
                 insight=True)
    if not registry.is_allowed(conversation.provider_profile):
        raise AIError(ErrorCode.INVALID_SELECTION, '이 대화의 모델은 더 이상 허용되지 않습니다. 새 대화를 시작해 주세요.')
    _expire_stale(db, 'ai_insight_messages', 'conversation_id = :c', {'c': conversation_id})
    locked = db.execute(text('SELECT id FROM ai_insight_conversations WHERE id = :c FOR UPDATE'),
                        {'c': conversation_id}).scalar_one()
    busy = db.execute(text("""SELECT 1 FROM ai_insight_messages WHERE conversation_id = :c
                              AND status IN ('queued', 'running') LIMIT 1"""), {'c': locked}).first()
    if busy:
        raise AIError(ErrorCode.INVALID_STATE, '이전 질문의 답변을 만드는 중입니다.')
    seq = db.execute(text('SELECT COALESCE(MAX(seq), 0) FROM ai_insight_messages WHERE conversation_id = :c'),
                     {'c': conversation_id}).scalar()
    db.execute(text("""INSERT INTO ai_insight_messages (conversation_id, seq, role, content, status, finished_at)
                       VALUES (:c, :s, 'user', CAST(:content AS jsonb), 'succeeded', now())"""),
               {'c': conversation_id, 's': seq + 1, 'content': json.dumps({'text': question}, ensure_ascii=False)})
    message_id = db.execute(text("""INSERT INTO ai_insight_messages (conversation_id, seq, role, status)
                                    VALUES (:c, :s, 'assistant', 'queued') RETURNING id"""),
                            {'c': conversation_id, 's': seq + 2}).scalar_one()
    if not conversation.title:
        db.execute(text('UPDATE ai_insight_conversations SET title = :t WHERE id = :c'),
                   {'t': question[:80], 'c': conversation_id})
    db.execute(text('UPDATE ai_insight_conversations SET updated_at = now() WHERE id = :c'), {'c': conversation_id})
    db.commit()
    return message_id


def list_conversations(db, owner_id: UUID, repository_id: int, limit: int = 30) -> list[dict]:
    rows = db.execute(text("""SELECT id, snapshot_id, provider_profile, title, created_at, updated_at, include_code
                              FROM ai_insight_conversations WHERE owner_user_id = :o AND repository_id = :r
                              AND deleted_at IS NULL ORDER BY updated_at DESC LIMIT :l"""),
                      {'o': owner_id, 'r': repository_id, 'l': limit}).all()
    return [{'id': str(r.id), 'snapshotId': r.snapshot_id, 'providerProfile': r.provider_profile,
             'includeCode': r.include_code, 'title': r.title or '새 대화', 'createdAt': r.created_at.isoformat(), 'updatedAt': r.updated_at.isoformat()}
            for r in rows]


def get_conversation(db, owner_id: UUID, conversation_id: UUID) -> dict:
    row = _conversation(db, owner_id, conversation_id)
    _expire_stale(db, 'ai_insight_messages', 'conversation_id = :c', {'c': conversation_id})
    messages = db.execute(text("""SELECT id, seq, role, content, status, progress, error_code, created_at, finished_at
                                  FROM ai_insight_messages WHERE conversation_id = :c ORDER BY seq"""),
                          {'c': conversation_id}).all()
    return {'id': str(row.id), 'repositoryId': row.repository_id, 'snapshotId': row.snapshot_id,
            'providerProfile': row.provider_profile, 'includeCode': row.include_code, 'title': row.title or '새 대화',
            'createdAt': row.created_at.isoformat(), 'updatedAt': row.updated_at.isoformat(),
            'messages': [{'id': str(m.id), 'seq': m.seq, 'role': m.role, 'status': m.status, 'content': m.content,
                          'progress': m.progress if m.role == 'assistant' else [], 'errorCode': m.error_code,
                          'createdAt': m.created_at.isoformat(),
                          'finishedAt': m.finished_at.isoformat() if m.finished_at else None} for m in messages]}


def delete_conversation(db, owner_id: UUID, conversation_id: UUID) -> None:
    _conversation(db, owner_id, conversation_id)
    db.execute(text('UPDATE ai_insight_conversations SET deleted_at = now() WHERE id = :c'), {'c': conversation_id})
    db.execute(text("""UPDATE ai_insight_messages SET status = 'failed', error_code = 'CANCELLED', finished_at = now()
                       WHERE conversation_id = :c AND status IN ('queued', 'running')"""), {'c': conversation_id})
    db.commit()


def mark_enqueue_failed(db, table: str, row_id: UUID) -> None:
    """실행을 시작하지 못한 행을 실패로 표시한다."""
    db.execute(text(f"""UPDATE {table} SET status = 'failed', error_code = 'QUEUE_UNAVAILABLE', finished_at = now()
                        WHERE id = :id AND status = 'queued'"""), {'id': row_id})
    db.commit()
