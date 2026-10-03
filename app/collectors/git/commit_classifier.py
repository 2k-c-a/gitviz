"""커밋 메시지 기반 commit_type 분류 (업무지시서 3-2).

규칙 우선순위:
1) Conventional Commits prefix (feat:, fix:, refactor: 등) → confidence 0.95
2) 메시지 내 keyword 탐지 → confidence 0.60
3) 해당 없음 → 'other', source='default', confidence 1.00

confidence는 분류 규칙에 부여한 신뢰도 값이며 검증된 정확도/결함 확률이 아니다.
default 1.00은 규칙에 매칭되지 않았다는 표시이지 실제 의미가 확실하다는 뜻이 아니다.

반환 commit_type ∈ {feature, bugfix, refactor, docs, chore, other}
"""

import re
from typing import Tuple

# Conventional Commits: "type" 또는 "type(scope)" 뒤에 ":" (선택적 "!")
_CONVENTIONAL_RE = re.compile(
    r"^\s*([a-z]+)(?:\([^)]*\))?!?\s*:", re.IGNORECASE
)

# conventional type → 표준 commit_type 버킷
_CONVENTIONAL_MAP = {
    "feat": "feature",
    "feature": "feature",
    "fix": "bugfix",
    "bugfix": "bugfix",
    "hotfix": "bugfix",
    "refactor": "refactor",
    "perf": "refactor",
    "docs": "docs",
    "doc": "docs",
    "chore": "chore",
    "build": "chore",
    "ci": "chore",
    "style": "chore",
    "test": "chore",
    "tests": "chore",
    "revert": "other",
}

# keyword 그룹: (정규식, commit_type) — 위에서부터 우선 적용 (업무지시서 순서: bugfix → feature → refactor)
_KEYWORD_RULES = [
    (re.compile(r"\b(bug\s?fix|hot\s?fix|fix(?:e[sd])?|bug)\b", re.IGNORECASE), "bugfix"),
    (re.compile(r"\b(feat(?:ure)?|add(?:ed|s|ing)?|implement(?:ed|s|ing)?)\b", re.IGNORECASE), "feature"),
    (re.compile(r"\b(refactor(?:ed|ing)?|clean\s?up|cleanup)\b", re.IGNORECASE), "refactor"),
]


def classify_commit(message: str) -> Tuple[str, str, float]:
    """(commit_type, commit_type_source, commit_type_confidence) 반환."""
    if not message:
        return ("other", "default", 1.00)

    first_line = message.strip().splitlines()[0] if message.strip() else ""

    # 1) Conventional Commits
    m = _CONVENTIONAL_RE.match(first_line)
    if m:
        prefix = m.group(1).lower()
        if prefix in _CONVENTIONAL_MAP:
            return (_CONVENTIONAL_MAP[prefix], "conventional_commits", 0.95)

    # 2) keyword 탐지 (제목 라인 기준)
    for pattern, ctype in _KEYWORD_RULES:
        if pattern.search(first_line):
            return (ctype, "keyword", 0.60)

    # 3) default
    return ("other", "default", 1.00)
