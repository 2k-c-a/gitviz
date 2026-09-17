# app/utils/normalize.py
import os
import re

_RENAME_BRACE_RE = re.compile(r"\{([^{}]*) => ([^{}]*)\}")
_GIT_SIMPLE_ESCAPES = {"\\": "\\", '"': '"', "a": "\a", "b": "\b", "f": "\f",
                        "n": "\n", "r": "\r", "t": "\t", "v": "\v"}

def _unquote_git_path(raw: str) -> str:
    """
    git은 core.quotePath(기본값 true)로 인해 비ASCII/특수문자가 섞인 경로를
    큰따옴표로 감싸고 각 바이트를 8진수 이스케이프(\\ooo)로 표기한다
    (예: '"tests/spec/314/247ial.txt"'). 그대로 두면 폴더/파일명에 따옴표와
    깨진 이스케이프 시퀀스가 그대로 노출된다 — 원래 UTF-8 바이트로 복원한다.
    """
    raw = raw.strip()
    if len(raw) < 2 or raw[0] != '"' or raw[-1] != '"':
        return raw
    inner = raw[1:-1]
    out = bytearray()
    i, n = 0, len(inner)
    while i < n:
        c = inner[i]
        if c == "\\" and i + 1 < n:
            nxt = inner[i + 1]
            if nxt in "01234567":
                j = i + 1
                digits = ""
                while j < n and len(digits) < 3 and inner[j] in "01234567":
                    digits += inner[j]
                    j += 1
                out.append(int(digits, 8) & 0xFF)
                i = j
                continue
            simple = _GIT_SIMPLE_ESCAPES.get(nxt)
            if simple is not None:
                out.extend(simple.encode("utf-8"))
                i += 2
                continue
        out.extend(c.encode("utf-8"))
        i += 1
    return out.decode("utf-8", errors="replace")

def resolve_rename_path(raw_path: str) -> str:
    """
    git numstat/diff의 rename 표기를 현재(new) 경로로, 따옴표+8진수 이스케이프
    표기를 원래 UTF-8 경로로 정규화한다.
    - "prefix/{old => new}/suffix" (공통 접두/접미사만 남기고 가운데만 바뀐 경우)
    - "old/path => new/path" (공통 부분이 없어 통째로 바뀐 경우)
    - '"경로"' (비ASCII/특수문자로 인해 통째로 따옴표+이스케이프된 경우)
    처리하지 않으면 "{old => new}"·" => "·따옴표/이스케이프가 그대로 path에 남아
    디렉토리 트리·GitHub 링크가 깨진다.
    """
    if not raw_path:
        return raw_path
    raw_path = raw_path.strip()

    m = _RENAME_BRACE_RE.search(raw_path)
    if m:
        raw_path = raw_path[:m.start()] + m.group(2) + raw_path[m.end():]
    elif " => " in raw_path:
        raw_path = raw_path.split(" => ")[-1].strip()

    return _unquote_git_path(raw_path)

def normalize_hash(h: str) -> str:
    """
    Commit SHA 정규화: None/빈값 방어, strip, lowercase
    """
    if not h:
        return ""
    return h.strip().lower()

def normalize_path(p: str) -> str:
    """
    파일 경로 정규화:
    - strip
    - 역슬래시를 슬래시로 변환
    - os.path.normpath로 중복/상대표현 정리
    - '.' 결과는 빈 문자열로 처리
    """
    if not p:
        return ""
    p = p.strip()
    p = p.replace("\\", "/")
    p = os.path.normpath(p).replace("\\", "/")
    if p == ".":
        return ""
    return p
