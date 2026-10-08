"""인수인계 문서의 Markdown 안전 처리.

- `escape_inline` / `quote_block` / `code_span`: 사용자·AI가 쓴 문장을 문서 구조(제목·링크·HTML)로 해석되지 않게 넣는다.
- `validate_markdown`: 저장·내보내기 전에 문서에서 위험한 구성(제어문자·방향 제어문자·원시 HTML·위험한 링크·이미지)을 찾는다.
  코드 블록과 코드 span 안은 검사하지 않는다(표시용 문자일 뿐 해석되지 않는다).
"""
import re

MAX_MARKDOWN_CHARS = 100_000
MAX_LINE_CHARS = 4_000

_CONTROL = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\x80-\x9f]')
_BIDI = re.compile('[؜‎‏‪-‮⁦-⁩​-‍⁠﻿]')
_FENCE_OPEN = re.compile(r'(?P<f>`{3,}|~{3,})(?P<info>.*)$')       # 여는 줄: 들여쓰기 없이 시작
_FENCE_CLOSE = re.compile(r' {0,3}(?P<f>`{3,}|~{3,})[ \t]*$')       # 닫는 줄: 들여쓰기 0~3칸, 뒤에는 공백만
_CODE_SPAN = re.compile(r'(?P<t>`+)(?P<body>.+?)(?P=t)', re.S)
_RAW_HTML = re.compile(r'<[A-Za-z/!?]')
_IMAGE = re.compile(r'!\[')
_INLINE_LINK = re.compile(r'\]\(\s*<?([^)\s>]*)')
_REF_DEF = re.compile(r'^\s{0,3}\[[^\]]+\]:\s*<?(\S+)', re.M)
_SAFE_SCHEMES = ('http://', 'https://', 'mailto:')
_ESCAPE = re.compile(r'([\\`*_\[\]<>&|~])')
_ESCAPED = re.compile(r'\\[\s\S]')
_LINE_MARKER =re.compile(r'^(\s*)([#>+\-]|\d+[.)])')


def _clean(text: str) -> str:
    return _BIDI.sub('', _CONTROL.sub('', (text or '').replace('\r\n', '\n').replace('\r', '\n')))


def escape_inline(text: str, *, limit: int = 500) -> str:
    """한 줄 문장을 Markdown 특수문자가 해석되지 않게 이스케이프한다. 줄바꿈은 공백으로, 길이는 limit으로 줄인다."""
    one_line = ' '.join(_clean(text).split())
    if len(one_line) > limit:
        one_line = one_line[:limit - 1] + '…'
    escaped = _ESCAPE.sub(r'\\\1', one_line)
    return _LINE_MARKER.sub(r'\1\\\2', escaped)                      # 줄 머리의 제목·목록·인용 표시도 문자로 보이게 한다


def code_span(text: str, *, limit: int = 500) -> str:
    """링크·경로처럼 그대로 보여야 하는 값을 코드 span으로 감싼다(클릭되지 않고 해석되지 않는다)."""
    value = ' '.join(_clean(text).split())[:limit]
    ticks = '`' * (max((len(m) for m in re.findall(r'`+', value)), default=0) + 1)
    pad = ' ' if value.startswith('`') or value.endswith('`') else ''
    return f'{ticks}{pad}{value}{pad}{ticks}'


def quote_block(text: str, *, limit: int = 2000) -> list[str]:
    """여러 줄 문장을 인용 블록 줄들로 만든다. 각 줄을 이스케이프해 제목·목록·HTML로 해석되지 않는다."""
    lines = _clean(text)[:limit].split('\n')
    return [('> ' + escape_inline(line, limit=MAX_LINE_CHARS)) if line.strip() else '>' for line in lines]


def _strip_code(markdown: str) -> str:
    # CommonMark 코드 블록 규칙에 맞춘다. 렌더러가 코드로 보지 않는 줄을 코드로 보면 그 뒤의 HTML을 놓치므로 애매하면 코드로 보지 않는다:
    # - 여는 줄은 들여쓰기 없이 시작한 것만 인정한다(목록·인용 안의 들여쓴 블록은 컨테이너가 끝나면 닫히므로 판단하지 않는다).
    # - 백틱 블록의 정보 문자열(여는 줄의 나머지)에 백틱이 있으면 코드 블록이 아니다.
    # - 닫는 줄은 같은 문자로 같거나 더 길게, 들여쓰기 0~3칸, 뒤에 공백만 있어야 한다. 닫히지 않으면 문서 끝까지 코드다.
    out, fence = [], None
    for line in markdown.split('\n'):
        if fence is None:
            m = _FENCE_OPEN.match(line)
            if m and not (m.group('f')[0] == '`' and '`' in m.group('info')):
                fence = m.group('f')
                continue
            out.append(line)
        else:
            m = _FENCE_CLOSE.match(line)
            if m and m.group('f')[0] == fence[0] and len(m.group('f')) >= len(fence):
                fence = None
    return '\n\n'.join(_inline_visible(par) for par in re.split(r'\n[ \t]*\n', '\n'.join(out)))


def _inline_visible(text: str) -> str:
    """한 문단에서 코드 span과 백슬래시 이스케이프를 렌더러와 같은 순서(왼쪽부터)로 걷어낸다.
    이스케이프된 백틱(\\`)은 코드 span을 열지 못하고, 코드 span 안의 백슬래시는 이스케이프가 아니다."""
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c == '\\' and i + 1 < n:
            out.append(' ')
            i += 2
        elif c == '`':
            j = i
            while j < n and text[j] == '`':
                j += 1
            run, pos, close = text[i:j], j, -1
            while True:
                k = text.find(run, pos)
                if k == -1:
                    break
                end = k + len(run)
                if (k == 0 or text[k - 1] != '`') and (end >= n or text[end] != '`'):
                    close = end
                    break
                pos = k + 1
            if close == -1:
                out.append(run)
                i = j
            else:
                out.append(' ')
                i = close
        else:
            out.append(c)
            i += 1
    return ''.join(out)


def _unsafe_target(target: str) -> bool:
    t = re.sub(r'[\s\x00-\x20]+', '', target).lower()
    if '&' in t or '%' in t[:12] and ':' in t[:12]:                  # 문자 참조·인코딩으로 가린 scheme은 허용하지 않는다
        return True
    if t.startswith('//'):
        return True                                                   # 프로토콜 상대 주소
    if t.startswith(('#', '/', './', '../')) or ':' not in t.split('/')[0].split('?')[0]:
        return False                                                  # 같은 문서 anchor·상대 경로
    return not t.startswith(_SAFE_SCHEMES)


def validate_markdown(markdown: str) -> list[str]:
    """문서에서 허용하지 않는 구성의 이유 코드 목록(비어 있으면 안전). 값을 고치지 않고 알리기만 한다."""
    if not isinstance(markdown, str):
        return ['not_text']
    issues = []
    if len(markdown) > MAX_MARKDOWN_CHARS:
        issues.append('too_long')
    if _CONTROL.search(markdown.replace('\n', '').replace('\t', '').replace('\r', '')) or '\x00' in markdown:
        issues.append('control_chars')
    if _BIDI.search(markdown):
        issues.append('hidden_or_bidi_chars')
    if any(len(line) > MAX_LINE_CHARS for line in markdown.split('\n')):
        issues.append('line_too_long')
    body = _strip_code(markdown)                                 # 코드 블록·코드 span·이스케이프된 문자는 해석되지 않으므로 제외한다
    if _RAW_HTML.search(body):
        issues.append('raw_html')
    if _IMAGE.search(body):
        issues.append('image')
    targets = _INLINE_LINK.findall(body) + _REF_DEF.findall(body)
    if any(_unsafe_target(t) for t in targets):
        issues.append('unsafe_link')
    return issues


ISSUE_TEXT = {
    'not_text': '문서는 텍스트여야 합니다.',
    'too_long': f'문서가 너무 깁니다(최대 {MAX_MARKDOWN_CHARS:,}자).',
    'control_chars': '제어 문자가 포함돼 있습니다.',
    'hidden_or_bidi_chars': '보이지 않는 문자나 방향 제어 문자가 포함돼 있습니다.',
    'line_too_long': f'한 줄이 너무 깁니다(최대 {MAX_LINE_CHARS:,}자).',
    'raw_html': 'HTML 태그는 쓸 수 없습니다(코드 블록·코드 span 안은 가능합니다).',
    'image': '이미지는 넣을 수 없습니다.',
    'unsafe_link': '링크는 http, https, mailto 또는 같은 문서·상대 경로만 쓸 수 있습니다.',
}
