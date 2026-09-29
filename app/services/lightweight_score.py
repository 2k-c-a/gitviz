"""GitHub 검색 결과에 대한 경량 예비 점수 — 클론 없이 메타데이터만으로 계산한다.
딥 분석(Risk Score/Bus Factor, app/api/analytics.py._compute_risk_scores)과는
전혀 다른 척도이며 절대 섞이면 안 된다. 순수 함수로 만들어 동일 입력에는 항상
동일 점수를 내도록 한다 — "최근일수록 高점"을 datetime.now()로 계산하면 같은
입력이 내일은 다른 값을 내서 순수성이 깨지므로, 기준 시각(now)을 인자로 받는다.
"""

import math
from datetime import datetime

# app/api/analytics.py의 _PRIMARY_LANGUAGE_EXTS(코드분석 트리의 "대표 언어" 판단
# 기준)와 같은 언어 집합을 재사용한다 — "주요 언어"를 프로젝트 안에서 두 가지
# 기준으로 따로 정의하지 않기 위함. GitHub API의 language 필드 표기(Python,
# JavaScript, C++ 등)에 맞춘 표시 이름 기준.
_MAJOR_LANGUAGES = {
    "Python", "JavaScript", "TypeScript", "Java", "Go", "Ruby", "PHP",
    "C", "C++", "C#", "Rust", "Kotlin", "Swift", "Scala", "Vue",
}

_ACTIVITY_MAX = 30
_ACTIVITY_DECAY_DAYS = 365  # 이 기간 이상 push가 없으면 activity 0점

_POPULARITY_MAX = 35
_POPULARITY_STAR_CAP = 100_000  # 이 stars 수 이상이면 popularity 만점(로그 스케일)

_LANGUAGE_MAX = 10
_LANGUAGE_OTHER = 6   # 목록 밖 언어(Elixir/Haskell 등)도 있으면 부분 점수 — 건강도와
                       # 무관한 항목이 100점 중 20점을 좌우하던 v1 설계의 과도한
                       # 페널티를 완화. Rust/C++/C#/Kotlin/Swift 등은 이미 아래
                       # _MAJOR_LANGUAGES에 포함돼 있어 이 경로를 안 탄다.
_LANGUAGE_NONE = 0     # language가 감지 안 된 레포(문서 전용 등)

_HEALTH_MAX = 25
_HEALTH_RATIO_FLOOR = 0.10  # open_issues/stars가 이 비율 이상이면 health 0점


def _activity_score(pushed_at: str | None, now: datetime) -> float:
    if not pushed_at:
        return 0.0
    try:
        pushed = datetime.fromisoformat(pushed_at.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    days = max((now - pushed).total_seconds() / 86400, 0.0)
    return max(0.0, _ACTIVITY_MAX * (1 - days / _ACTIVITY_DECAY_DAYS))


def _popularity_score(stars: int) -> float:
    stars = max(stars or 0, 0)
    ratio = math.log10(stars + 1) / math.log10(_POPULARITY_STAR_CAP + 1)
    return min(_POPULARITY_MAX, _POPULARITY_MAX * ratio)


def _language_score(language: str | None) -> float:
    if not language:
        return _LANGUAGE_NONE
    return _LANGUAGE_MAX if language in _MAJOR_LANGUAGES else _LANGUAGE_OTHER


def _health_score(open_issues: int, stars: int) -> float:
    # watchers_count는 GitHub 레거시 동작으로 stargazers_count와 항상 동일하고
    # (실측 확인됨) subscribers_count는 검색 API 응답에 없어서, stars 대비로
    # 계산한다. open_issues에 open PR이 섞여 있어 PR이 활발한 레포가 상대적으로
    # 낮게 나올 수 있는 점은 알려진 한계.
    ratio = (open_issues or 0) / max(stars or 0, 1)
    return max(0.0, _HEALTH_MAX * (1 - min(ratio / _HEALTH_RATIO_FLOOR, 1)))


def compute_lightweight_score(metadata: dict, now: datetime) -> dict:
    """GitHub 검색 결과 메타데이터(stars/open_issues/language/pushed_at)만으로
    0~100점의 경량 예비 점수를 계산하는 순수 함수. 클론·딥 분석과는 무관하다."""
    activity = _activity_score(metadata.get("pushed_at"), now)
    popularity = _popularity_score(metadata.get("stars", 0))
    language = _language_score(metadata.get("language"))
    health = _health_score(metadata.get("open_issues", 0), metadata.get("stars", 0))
    total = activity + popularity + language + health

    return {
        "activity": round(activity),
        "popularity": round(popularity),
        "language": round(language),
        "health": round(health),
        "total": round(total),
    }
