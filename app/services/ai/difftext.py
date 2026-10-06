"""보관한 unified diff를 줄 번호가 붙은 줄 목록으로 푼다. DB·네트워크·설정에 의존하지 않는다."""
import re

from app.schemas.ai import DiffLine, DiffLineKind

_HUNK_RE = re.compile(r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@')


def split_lines(text: str) -> list[str]:
    r"""git의 줄 구분('\n')으로만 나눈다.

    str.splitlines()는 form feed(\x0c)·vertical tab(\x0b)·NEL(\x85)·  같은 문자에서도 나뉘어 줄 번호가
    어긋나므로 쓰지 않는다. 마지막 개행 뒤의 빈 조각은 줄이 아니다.
    """
    parts = text.split('\n')
    if parts and parts[-1] == '':
        parts.pop()
    return parts


def diff_lines(text: str) -> list[DiffLine]:
    r"""줄 번호는 실제 commit의 1부터 시작하는 번호다. CRLF 파일의 줄 끝 '\r'은 표시용 텍스트에서 뺀다."""
    lines, old_no, new_no, in_hunk = [], 0, 0, False
    for raw in split_lines(text):
        m = _HUNK_RE.match(raw)
        if m:
            old_no, new_no, in_hunk = int(m.group(1)), int(m.group(3)), True
            continue
        if not in_hunk or raw.startswith('\\'):          # '\ No newline at end of file'
            continue
        body = raw[1:].removesuffix('\r')
        if raw.startswith('-'):
            lines.append(DiffLine(kind=DiffLineKind.removed, left_line=old_no, text=body))
            old_no += 1
        elif raw.startswith('+'):
            lines.append(DiffLine(kind=DiffLineKind.added, right_line=new_no, text=body))
            new_no += 1
        else:
            lines.append(DiffLine(kind=DiffLineKind.context, left_line=old_no, right_line=new_no, text=body))
            old_no += 1
            new_no += 1
    return lines
