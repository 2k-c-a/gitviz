"""GitHub 검색 결과에 대한 경량 예비 점수 — 클론 없이 메타데이터만으로 계산한다.
딥 분석(Risk Score/Bus Factor, app/api/analytics.py._compute_risk_scores)과는
전혀 다른 척도이며 절대 섞이면 안 된다. 순수 함수로 만들어 동일 입력에는 항상
동일 점수를 내도록 한다 — "최근일수록 高점"을 datetime.now()로 계산하면 같은
입력이 내일은 다른 값을 내서 순수성이 깨지므로, 기준 시각(now)을 인자로 받는다.

점수는 "프로젝트가 얼마나 건강한가"를 간략히 보는 용도라서 건강도와 직접 관련된
두 항목(활동성·이슈 관리)만 반영한다. 인기도(stars)와 언어는 건강도 지표가 아니라
신생·소규모 레포를 불리하게 만들었기 때문에 점수에서 뺐다(카드의 stars 수/언어
배지로는 그대로 표시된다).
"""

from datetime import datetime

_ACTIVITY_MAX = 55
_ACTIVITY_DECAY_DAYS = 365  # 이 기간 이상 push가 없으면 activity 0점

_HEALTH_MAX = 45
_HEALTH_RATIO_FLOOR = 0.10  # 보정된 open_issues/stars 비율이 이 값 이상이면 health 0점
# 스타가 적은 레포는 이슈 1~2개만으로 비율이 한계값에 닿아 health가 0이 되던 문제를
# 막기 위해 분모에 가상의 스타를 더해 비율을 완충한다. 스타가 많아질수록 원래 비율에 수렴한다.
_HEALTH_STAR_PRIOR = 50
# 방치된 레포는 이슈가 없다는 이유만으로 이슈 관리 만점을 받으면 안 된다(이슈를 받는 사람이
# 없어서 0인 것일 수 있음). 활동성이 0이면 health 상한이 만점의 이 비율이 되고, 활동성이
# 높아질수록 상한이 선형으로 올라 만점에 도달한다.
_HEALTH_CAP_FLOOR = 0.4


def _activity_score(pushed_at: str | None, now: datetime) -> float:
    if not pushed_at:
        return 0.0
    try:
        pushed = datetime.fromisoformat(pushed_at.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    days = max((now - pushed).total_seconds() / 86400, 0.0)
    return max(0.0, _ACTIVITY_MAX * (1 - days / _ACTIVITY_DECAY_DAYS))


def _health_score(open_issues: int, stars: int, activity: float) -> float:
    # watchers_count는 GitHub 레거시 동작으로 stargazers_count와 항상 동일하고
    # (실측 확인됨) subscribers_count는 검색 API 응답에 없어서, stars 대비로
    # 계산한다. open_issues에 open PR이 섞여 있어 PR이 활발한 레포가 상대적으로
    # 낮게 나올 수 있는 점은 알려진 한계.
    ratio = (open_issues or 0) / (max(stars or 0, 0) + _HEALTH_STAR_PRIOR)
    raw = _HEALTH_MAX * (1 - min(ratio / _HEALTH_RATIO_FLOOR, 1))
    cap = _HEALTH_MAX * (_HEALTH_CAP_FLOOR + (1 - _HEALTH_CAP_FLOOR) * activity / _ACTIVITY_MAX)
    return max(0.0, min(raw, cap))


def compute_lightweight_score(metadata: dict, now: datetime) -> dict:
    """GitHub 검색 결과 메타데이터(stars/open_issues/pushed_at)만으로
    0~100점의 경량 예비 점수를 계산하는 순수 함수. 클론·딥 분석과는 무관하다."""
    activity = _activity_score(metadata.get("pushed_at"), now)
    health = _health_score(metadata.get("open_issues", 0), metadata.get("stars", 0), activity)

    return {
        "activity": round(activity),
        "health": round(health),
        "total": round(activity + health),
    }
