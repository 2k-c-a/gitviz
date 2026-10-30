"""compute_lightweight_score 순수 함수 검증: 결정론성, 범위, 소규모 레포 이슈 완충 확인.
기존 tests/server/test_progress_reporter.py와 같은 방식의 assert 스크립트 — pytest 없이
python tests/server/test_lightweight_score.py 로 바로 실행한다."""

import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from datetime import datetime, timezone
from app.services.lightweight_score import compute_lightweight_score

NOW = datetime(2026, 9, 29, tzinfo=timezone.utc)


def check(label, cond):
    status = "OK " if cond else "FAIL"
    print(f"[{status}] {label}")
    if not cond:
        raise AssertionError(label)


# 1) 결정론성 — 동일 입력(+동일 now) → 항상 동일 점수
fastapi_meta = {"stars": 102700, "open_issues": 83, "pushed_at": "2026-09-28T19:48:59Z"}
r1 = compute_lightweight_score(fastapi_meta, now=NOW)
r2 = compute_lightweight_score(dict(fastapi_meta), now=NOW)
check("동일 입력 → 동일 점수(순수 함수)", r1 == r2)
print("  fastapi 실측값 기준:", r1)
check("반환 키는 activity/health/total 뿐 (인기도·언어는 점수에서 제외)",
      set(r1) == {"activity", "health", "total"})

# 2) 활발·이슈 적은 레포는 만점에 가까워야 함
check("fastapi 유사 레포 total >= 90", r1["total"] >= 90)

# 3) 극단값 — push 정보 없는 빈 레포
empty_meta = {"stars": 0, "open_issues": 0, "pushed_at": None}
r3 = compute_lightweight_score(empty_meta, now=NOW)
check("빈 메타데이터 → activity 0", r3["activity"] == 0)
check("빈 메타데이터 → total이 0~100 범위 안", 0 <= r3["total"] <= 100)

# 4) 인기 없는 레포에서 이슈 1개로 health가 크게 깎이지 않아야 함 (이번 수정의 핵심)
small = {"stars": 10, "pushed_at": "2026-09-20T00:00:00Z"}
r_small_1 = compute_lightweight_score({**small, "open_issues": 1}, now=NOW)
r_small_0 = compute_lightweight_score({**small, "open_issues": 0}, now=NOW)
check("스타 10·이슈 1 → health가 만점의 절반 이상", r_small_1["health"] >= 45 / 2)
check("이슈가 늘수록 health는 감소", r_small_0["health"] > r_small_1["health"])
r_small_many = compute_lightweight_score({**small, "open_issues": 50}, now=NOW)
check("이슈가 압도적으로 많으면 health 0", r_small_many["health"] == 0)

# 5) 인기도·언어는 점수에 영향을 주지 않아야 함
base = {"open_issues": 1, "pushed_at": "2026-09-20T00:00:00Z"}
check("스타 수가 같으면 언어와 무관하게 동일 점수",
      compute_lightweight_score({**base, "stars": 500, "language": "Python"}, now=NOW)
      == compute_lightweight_score({**base, "stars": 500, "language": None}, now=NOW))

# 5-1) 방치된 레포는 이슈가 없어도 이슈 관리 만점을 받지 못해야 함 (활동성 0 → 상한 18)
dead = compute_lightweight_score({"stars": 500, "open_issues": 0, "pushed_at": "2020-01-01T00:00:00Z"}, now=NOW)
check("방치 레포(이슈 0) → health <= 18 이므로 total <= 18", dead["health"] <= 18 and dead["total"] <= 18)
check("빈 메타데이터도 total <= 18", r3["total"] <= 18)

# 6) 오래 방치된 레포는 activity가 낮아야 함 (1년 이상 미갱신)
stale_meta = {"stars": 500, "open_issues": 5, "pushed_at": "2023-01-01T00:00:00Z"}
r_stale = compute_lightweight_score(stale_meta, now=NOW)
check("1년+ 미갱신 레포 → activity 0", r_stale["activity"] == 0)

# 7) 모든 하위 점수가 항상 0~상한 범위 안에 있어야 함 (음수/초과 없음)
for meta in [fastapi_meta, empty_meta, stale_meta, {**small, "open_issues": 1}]:
    r = compute_lightweight_score(meta, now=NOW)
    check(f"범위 체크 stars={meta.get('stars')}: {r}",
          0 <= r["activity"] <= 55 and 0 <= r["health"] <= 45 and 0 <= r["total"] <= 100)

print("\n전체 통과")
