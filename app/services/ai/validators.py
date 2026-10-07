"""모델 출력 검증: JSON 구조 + 실제로 보낸 파일·side·줄 범위·근거.

통과는 '구조와 참조가 맞다'는 뜻일 뿐 내용이 옳다는 뜻이 아니다. 결과에는 항상 human_validation=unverified가 붙고
자동으로 valid가 되지 않는다. 하나라도 어긋나면 결과 전체를 정상으로 확정하지 않는다(부분 채택 없음).

검증 기준은 프롬프트에 실제로 넣은 자료(SentInput)다. 모델이 지어낸 파일·줄 번호는 인정하지 않고 위치는
전송한 줄 번호 안에서만 인정하며, 인용(quote)이 그 줄 범위에 실제로 있는지 확인한다.
"""
from dataclasses import dataclass, field
from uuid import UUID

from pydantic import ValidationError

from app.schemas.ai import (CodeLocation, Finding, HumanValidation, ModelFinding, ReviewOutput, StructureValidation,
                            SummaryOutput, canonical_hash)
from app.services.ai.prompts import SentFile, SentInput
from app.services.ai.providers.base import MAX_RAW_OUTPUT_BYTES, ProviderResponse, parse_json_object

MAX_ISSUES = 50
MAX_RANGE_LINES = 2000


@dataclass(frozen=True)
class Issue:
    code: str
    path: str
    message: str


@dataclass
class ValidatedResult:
    kind: str
    ok: bool
    structure_validation: StructureValidation
    human_validation: HumanValidation = HumanValidation.unverified
    issues: list[Issue] = field(default_factory=list, repr=False)
    payload: dict | None = field(default=None, repr=False)
    payload_hash: str | None = None
    findings: list[Finding] = field(default_factory=list, repr=False)
    summary: SummaryOutput | None = field(default=None, repr=False)


def _fail(kind: str, issues: list[Issue]) -> ValidatedResult:
    return ValidatedResult(kind=kind, ok=False, structure_validation=StructureValidation.failed,
                           issues=issues[:MAX_ISSUES])


def _schema_issues(error: ValidationError) -> list[Issue]:
    """pydantic 오류에서 위치와 종류만 꺼낸다. 모델이 쓴 값은 오류 메시지에 되돌려 담지 않는다."""
    issues = []
    for err in error.errors():
        path = '.'.join(str(p) for p in err['loc']) or 'output'
        issues.append(Issue(f"schema_{err['type']}", path, '출력 구조가 schema와 맞지 않습니다.'))
    return issues


def _normalize(kind: str, data: dict) -> dict:
    """빈 문자열로 '없음'을 표현하게 한 선택 필드를 None으로 바꾼다(구조적 출력 schema에 null이 없다)."""
    data = dict(data)
    if kind == 'summary' and data.get('author_intent') == '':
        data['author_intent'] = None
    if kind == 'review' and isinstance(data.get('findings'), list):
        fixed = []
        for item in data['findings']:
            if isinstance(item, dict) and item.get('suggestion') == '':
                item = {**item, 'suggestion': None}
            fixed.append(item)
        data['findings'] = fixed
    return data


def _parse(kind: str, output) -> tuple[dict | None, list[Issue]]:
    if isinstance(output, ProviderResponse):
        if not output.usable:
            return None, [Issue('provider_output_unusable', 'output',
                                f'제공자 응답을 쓸 수 없습니다({output.finish}{"/" + output.parse_error if output.parse_error else ""}).')]
        output = output.parsed
    if isinstance(output, (str, bytes)):
        text = output.decode('utf-8', 'replace') if isinstance(output, bytes) else output
        parsed, error = parse_json_object(text)
        if parsed is None:
            return None, [Issue(error, 'output', '출력이 JSON 객체 하나가 아닙니다.')]
        output = parsed
    if not isinstance(output, dict):
        return None, [Issue('not_json_object', 'output', '출력이 JSON 객체가 아닙니다.')]
    return output, []


def _handles(sent: SentInput, ids: list[str], path: str, issues: list[Issue]) -> list[SentFile] | None:
    known = sent.by_handle()
    files, seen = [], set()
    for handle in ids:
        if handle in seen:
            issues.append(Issue('duplicate_evidence', path, '같은 근거가 중복됩니다.'))
            return None
        seen.add(handle)
        if handle not in known:
            issues.append(Issue('unknown_evidence', path, '전송하지 않은 근거(파일)를 가리킵니다.'))
            return None
        files.append(known[handle])
    return files


def _resolve_file(sent: SentInput, path: str, side: str) -> SentFile | None:
    for f in sent.files:
        if f.path == path:
            return f
    for f in sent.files:                                   # 이름이 바뀐 파일의 LEFT는 옛 경로로 부를 수 있다
        if f.old_path and f.old_path == path and side == 'LEFT':
            return f
    return None


def _norm(text: str) -> str:
    return ' '.join(text.split())


def _check_finding(index: int, finding: ModelFinding, sent: SentInput, issues: list[Issue]):
    base = f'findings.{index}'
    evidence = _handles(sent, finding.evidence_ids, f'{base}.evidence_ids', issues)
    loc = finding.location
    side = loc.side
    file = _resolve_file(sent, loc.path, side)
    if file is None:
        issues.append(Issue('unknown_file', f'{base}.location.path', '전송하지 않은 파일을 가리킵니다.'))
        return None
    if evidence is not None and file.handle not in finding.evidence_ids:
        issues.append(Issue('evidence_mismatch', f'{base}.evidence_ids', '위치한 파일이 근거에 포함돼 있지 않습니다.'))
        return None
    if file.side_availability not in ('BOTH', side):
        issues.append(Issue('invalid_side', f'{base}.location.side', '이 파일에는 해당 side가 없습니다.'))
        return None
    if loc.end_line < loc.start_line:
        issues.append(Issue('invalid_range', f'{base}.location', '줄 범위가 거꾸로입니다.'))
        return None
    if loc.end_line - loc.start_line >= MAX_RANGE_LINES:
        issues.append(Issue('range_too_large', f'{base}.location', '줄 범위가 너무 큽니다.'))
        return None
    visible = file.text_for(side)
    lines = list(range(loc.start_line, loc.end_line + 1))
    if not all(n in visible for n in lines):
        issues.append(Issue('line_not_sent', f'{base}.location', '전송하지 않은 줄을 포함합니다.'))
        return None
    quote = _norm(loc.quote)
    if len(quote) < 3 or quote not in _norm(' '.join(visible[n] for n in lines)):
        issues.append(Issue('quote_not_in_range', f'{base}.location.quote', '인용이 해당 줄 범위의 코드에 없습니다.'))
        return None
    return file, evidence


def _validate_review(data: dict, sent: SentInput, revision_id: UUID) -> ValidatedResult:
    try:
        output = ReviewOutput.model_validate(_normalize('review', data))
    except ValidationError as exc:
        return _fail('review', _schema_issues(exc))
    issues: list[Issue] = []
    checked = []
    seen_keys = set()
    for index, finding in enumerate(output.findings):
        result = _check_finding(index, finding, sent, issues)
        if result is None:
            continue
        file, evidence = result
        key = (file.handle, finding.location.side, finding.location.start_line, finding.location.end_line,
               finding.title.casefold())
        if key in seen_keys:
            issues.append(Issue('duplicate_finding', f'findings.{index}', '같은 위치의 같은 지적이 중복됩니다.'))
            continue
        seen_keys.add(key)
        checked.append((index, finding, file, evidence))
    if issues:
        return _fail('review', issues)
    # priority는 검토 순서다. 모델이 준 순서(동률이면 출력 순서)를 1..N으로 정리한다.
    ordered = sorted(checked, key=lambda item: (item[1].priority, item[0]))
    findings, stored = [], []
    for rank, (_, finding, file, evidence) in enumerate(ordered, start=1):
        loc = finding.location
        side = loc.side
        path = file.old_path if side == 'LEFT' and file.old_path else file.path
        evidence_ids = [str(f.file_id) for f in evidence]
        findings.append(Finding(
            ordinal=rank, title=finding.title, category=finding.category, priority=rank, condition=finding.condition,
            impact=finding.impact, evidence_ids=evidence_ids, verification=finding.verification,
            suggestion=finding.suggestion, limitations=[],
            location=CodeLocation(path=path, side=side, start_line=loc.start_line, end_line=loc.end_line,
                                  revision_id=revision_id)))
        stored.append({**findings[-1].model_dump(mode='json'), 'file_id': str(file.file_id), 'quote': loc.quote,
                       'model_limitations': finding.limitations})
    payload = {'kind': 'review', 'schema_version': output.schema_version, 'findings': stored}
    return ValidatedResult(kind='review', ok=True, structure_validation=StructureValidation.passed, payload=payload,
                           payload_hash=canonical_hash(payload), findings=findings)


def _validate_summary(data: dict, sent: SentInput) -> ValidatedResult:
    try:
        output = SummaryOutput.model_validate(_normalize('summary', data))
    except ValidationError as exc:
        return _fail('summary', _schema_issues(exc))
    issues: list[Issue] = []
    top = _handles(sent, output.evidence_ids, 'evidence_ids', issues)
    if output.author_intent and not sent.has_description:
        issues.append(Issue('author_intent_without_description', 'author_intent',
                            'PR 설명이 없는데 작성자의 의도가 적혀 있습니다.'))
    changes, seen_paths = [], set()
    for index, change in enumerate(output.file_changes):
        base = f'file_changes.{index}'
        file = _resolve_file(sent, change.path, 'RIGHT') or _resolve_file(sent, change.path, 'LEFT')
        if file is None:
            issues.append(Issue('unknown_file', f'{base}.path', '전송하지 않은 파일을 가리킵니다.'))
            continue
        if file.handle in seen_paths:
            issues.append(Issue('duplicate_file_change', f'{base}.path', '같은 파일이 중복됩니다.'))
            continue
        seen_paths.add(file.handle)
        evidence = _handles(sent, change.evidence_ids, f'{base}.evidence_ids', issues)
        if evidence is not None and file.handle not in change.evidence_ids:
            issues.append(Issue('evidence_mismatch', f'{base}.evidence_ids', '설명한 파일이 근거에 포함돼 있지 않습니다.'))
            continue
        if evidence is not None:
            changes.append({'path': file.path, 'file_id': str(file.file_id), 'summary': change.summary,
                            'evidence_ids': [str(f.file_id) for f in evidence]})
    if issues or top is None:
        return _fail('summary', issues)
    payload = {'kind': 'summary', 'schema_version': output.schema_version, 'overview': output.overview,
               'author_intent': output.author_intent, 'file_changes': changes, 'unknowns': list(output.unknowns),
               'evidence_ids': [str(f.file_id) for f in top]}
    return ValidatedResult(kind='summary', ok=True, structure_validation=StructureValidation.passed, payload=payload,
                           payload_hash=canonical_hash(payload), summary=output)


def validate_output(kind: str, output, sent: SentInput, *, revision_id: UUID) -> ValidatedResult:
    """output은 파싱된 dict, JSON 문자열 또는 ProviderResponse. sent는 실제로 보낸 자료(manifest의 읽은 파일).

    구조·참조가 모두 맞으면 ok=True이며 payload/findings를 돌려준다. 그래도 human_validation은 unverified다.
    """
    if kind not in ('summary', 'review'):
        raise ValueError('kind는 summary 또는 review여야 합니다.')
    data, issues = _parse(kind, output)
    if data is None:
        return _fail(kind, issues)
    return _validate_review(data, sent, revision_id) if kind == 'review' else _validate_summary(data, sent)
