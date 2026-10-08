"""프롬프트 로딩·렌더링과 출력 JSON schema.

- 프롬프트 파일은 버전이 고정된다. 내용을 바꾸면 PROMPT_DIGESTS가 달라지므로(테스트가 검사) 새 버전 파일을 만든다.
- 신뢰할 수 없는 자료(PR 설명·코드)는 요청마다 새로 만든 무작위 경계 토큰으로 감싼다. 코드가 경계를 흉내 내거나
  "이전 지시를 무시하라"고 써 있어도 시스템 프롬프트가 경계 밖의 지시만 따르게 정의한다. system 프롬프트에는 코드가 없다.
- 렌더링과 검증이 같은 SentInput을 쓰므로 '실제로 보낸 파일·줄'이 검증 기준이 된다.
"""
import hashlib
import json
import secrets
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Iterable
from uuid import UUID

from app.schemas.ai import (ChangeType, ContentState, DiffLine, DiffLineKind, InputManifest, PRTransferMetadata,
                            REVIEW_SCHEMA_VERSION, SUMMARY_SCHEMA_VERSION)
from app.services.ai.difftext import diff_lines
from app.schemas.ai_output_limits import REVIEW_LIMITS, SUMMARY_LIMITS

PROMPT_DIR = Path(__file__).parent
PLACEHOLDER = '{{BOUNDARY}}'
PROMPT_FILES = {'summary': 'summary_v1.txt', 'review': 'review_v1.txt'}
# 프롬프트 파일을 바꾸면 이 값과 profile의 prompt 버전을 함께 올려야 한다.
PROMPT_DIGESTS = {
    'review_v2.txt': '19b10205cfc0ba11978efe8629b161c191dc7a696321d7d520b6a48a3583b721',
    'summary_v2.txt': 'a57aaccd4e04a215abcff9156281b21ee8ab6085800668e0c48d1e667adb7b29',
    'summary_v1.txt': 'c28f0c4a3bf56bfd576c30480bc3f463e2d01e25a30e3808bdff1fbca6ee3311',
    'review_v1.txt': '08e36f662a9e0eb6ee5691b903aaee67ab225cc110566e37ddfe481eab038841',
}
RENDER_EXPANSION = 1.5          # unified diff 바이트 → 렌더링된 줄 번호 표기까지 포함한 보수적 배율


@lru_cache(maxsize=None)
def system_template(kind: str, version: str | None = None) -> str:
    version = version or f'{kind}-v1'
    if version not in (f'{kind}-v1', f'{kind}-v2'):
        raise ValueError('지원하지 않는 프롬프트 버전입니다.')
    text = (PROMPT_DIR / f'{version.replace("-", "_")}.txt').read_text(encoding='utf-8')
    if version.endswith('-v2'):
        limits = REVIEW_LIMITS if kind == 'review' else SUMMARY_LIMITS
        text += '\n# Field length limits (Unicode characters, not tokens)\n'
        text += '\n'.join(f'- {field}: at most {limit} characters.' for field, limit in limits.items())
    return text


def file_digest(filename: str) -> str:
    # 체크아웃 환경(CRLF/LF)과 무관하게 같은 값이 나오도록 개행을 정규화한다.
    text = (PROMPT_DIR / filename).read_bytes().decode('utf-8').replace('\r\n', '\n')
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def verify_prompt_digests() -> None:
    for filename, expected in PROMPT_DIGESTS.items():
        if file_digest(filename) != expected:
            raise RuntimeError(f'프롬프트 {filename}이 고정된 버전과 다릅니다. 새 버전 파일을 만드세요.')


@dataclass
class SentFile:
    handle: str
    file_id: UUID
    path: str
    old_path: str | None
    change_type: ChangeType
    side_availability: str
    language: str | None
    lines: list[DiffLine] = field(repr=False)
    left_text: dict[int, str] = field(init=False, repr=False)
    right_text: dict[int, str] = field(init=False, repr=False)

    def __post_init__(self):
        self.left_text = {l.left_line: l.text for l in self.lines if l.left_line is not None}
        self.right_text = {l.right_line: l.text for l in self.lines if l.right_line is not None}

    def text_for(self, side: str) -> dict[int, str]:
        return self.left_text if side == 'LEFT' else self.right_text


@dataclass
class SentInput:
    """모델에 실제로 보낸 자료: 파일(줄 번호 포함)과 PR 설명."""
    files: list[SentFile] = field(repr=False)
    metadata: PRTransferMetadata = field(repr=False)

    @property
    def has_description(self) -> bool:
        return bool(self.metadata.title.strip() or self.metadata.body.strip())

    def by_handle(self) -> dict[str, SentFile]:
        return {f.handle: f for f in self.files}


def build_sent_input(manifest: InputManifest, payloads: dict[str, bytes], file_ids: dict[str, UUID]) -> SentInput:
    """manifest에서 실제로 읽은 파일만 골라 SentInput을 만든다(제외된 파일은 보내지 않는다)."""
    files = []
    for entry in manifest.files:
        payload = payloads.get(entry.path)
        if entry.content_state is not ContentState.read or payload is None:
            continue
        side = ('LEFT' if entry.change_type is ChangeType.deleted else
                'RIGHT' if entry.change_type is ChangeType.added else 'BOTH')
        files.append(SentFile(handle=f'F{len(files) + 1}', file_id=file_ids[entry.path], path=entry.path,
                              old_path=entry.old_path, change_type=entry.change_type, side_availability=side,
                              language=entry.language, lines=diff_lines(payload.decode('utf-8'))))
    return SentInput(files=files, metadata=manifest.pr_metadata or PRTransferMetadata())


def new_boundary(texts: Iterable[str]) -> str:
    """보낼 자료 어디에도 나오지 않는 경계 토큰."""
    corpus = list(texts)
    for _ in range(8):
        token = 'B' + secrets.token_hex(12)
        if not any(token in text for text in corpus):
            return token
    raise RuntimeError('경계 토큰을 만들 수 없습니다.')


def _quoted(value: str | None) -> str:
    return json.dumps(value if value is not None else None, ensure_ascii=False)


def _line(l: DiffLine) -> str:
    mark = {DiffLineKind.context: '=', DiffLineKind.added: '+', DiffLineKind.removed: '-'}[l.kind]
    left = l.left_line if l.left_line is not None else '-'
    right = l.right_line if l.right_line is not None else '-'
    return f'{mark} L{left} R{right} | {l.text}'


def render_user_message(boundary: str, sent: SentInput) -> str:
    """PR 설명과 파일별 diff. 모든 자료는 경계 블록 안에 들어가고 머리줄 값은 JSON 문자열로 이스케이프한다."""
    parts = [f'<<<{boundary}:BEGIN pr_description>>>']
    if sent.has_description:
        parts += [f'title: {_quoted(sent.metadata.title)}', 'body:', sent.metadata.body]
        if sent.metadata.body_truncated:
            parts.append('[the description was truncated]')
    else:
        parts.append('(no description provided)')
    parts.append(f'<<<{boundary}:END>>>')
    for f in sent.files:
        parts.append(f'<<<{boundary}:BEGIN file handle={f.handle} path={_quoted(f.path)} old_path={_quoted(f.old_path)} '
                     f'change={f.change_type.value} language={_quoted(f.language)} sides={f.side_availability}>>>')
        parts += [_line(l) for l in f.lines]
        parts.append(f'<<<{boundary}:END>>>')
    parts.append('Respond with the JSON object only.')
    return '\n'.join(parts)


@dataclass(frozen=True)
class RenderedPrompt:
    system: str = field(repr=False)
    user: str = field(repr=False)
    boundary: str = field(repr=False)


def render_request(kind: str, sent: SentInput, *, prompt_version=None) -> RenderedPrompt:
    corpus = [sent.metadata.title, sent.metadata.body]
    for f in sent.files:
        corpus += [f.path, f.old_path or '', f.language or ''] + [l.text for l in f.lines]
    boundary = new_boundary(corpus)
    return RenderedPrompt(system=system_template(kind, prompt_version).replace(PLACEHOLDER, boundary),
                          user=render_user_message(boundary, sent), boundary=boundary)


# ── 출력 JSON schema (구조적 출력용). 제약(길이·범위)은 validators가 검사한다 ──────────

_STR = {'type': 'string'}
_INT = {'type': 'integer'}


def _obj(properties: dict, required: list[str] | None = None) -> dict:
    return {'type': 'object', 'properties': properties, 'required': required or list(properties),
            'additionalProperties': False}


REVIEW_JSON_SCHEMA = _obj({
    'schema_version': _STR,
    'findings': {'type': 'array', 'items': _obj({
        'title': _STR, 'category': _STR, 'priority': _INT, 'condition': _STR, 'impact': _STR,
        'evidence_ids': {'type': 'array', 'items': _STR},
        'location': _obj({'path': _STR, 'side': _STR, 'start_line': _INT, 'end_line': _INT, 'quote': _STR}),
        'verification': _STR, 'suggestion': _STR, 'limitations': {'type': 'array', 'items': _STR}})},
})

SUMMARY_JSON_SCHEMA = _obj({
    'schema_version': _STR, 'overview': _STR, 'author_intent': _STR,
    'file_changes': {'type': 'array', 'items': _obj({
        'path': _STR, 'summary': _STR, 'evidence_ids': {'type': 'array', 'items': _STR}})},
    'unknowns': {'type': 'array', 'items': _STR},
    'evidence_ids': {'type': 'array', 'items': _STR},
})

OUTPUT_SCHEMAS = {'summary': SUMMARY_JSON_SCHEMA, 'review': REVIEW_JSON_SCHEMA}
SCHEMA_VERSIONS = {'summary': SUMMARY_SCHEMA_VERSION, 'review': REVIEW_SCHEMA_VERSION}


def output_schema(kind: str, schema_version=None, *, enforce_lengths=False) -> dict:
    version = schema_version or f'{kind}-output-v1'
    if version == f'{kind}-output-v1':
        return OUTPUT_SCHEMAS[kind]
    if version != f'{kind}-output-v2':
        raise ValueError('지원하지 않는 출력 schema입니다.')
    # Template leaves share _STR/_INT; JSON round-trip creates distinct field nodes.
    schema = json.loads(json.dumps(OUTPUT_SCHEMAS[kind]))
    schema['properties']['schema_version'] = {'type': 'string', 'enum': [version]}
    limits = REVIEW_LIMITS if kind == 'review' else SUMMARY_LIMITS
    for path, limit in limits.items():
        current = schema['properties']['findings']['items'] if kind == 'review' else schema
        for part in path.split('.'):
            current = current['properties'][part.removesuffix('[]')]
            if part.endswith('[]'): current = current['items']
        current['description'] = f'At most {limit} Unicode characters. Be concise.'
        if enforce_lengths: current['maxLength'] = limit
    return schema
