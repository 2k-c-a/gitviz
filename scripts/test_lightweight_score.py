"""compute_lightweight_score 순수 함수 검증: 결정론성, 범위, 언어 구간별 동작 확인.
기존 scripts/test_progress_reporter.py와 같은 방식의 assert 스크립트 — pytest 없이
python scripts/test_lightweight_score.py 로 바로 실행한다."""

import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timezone
from app.services.lightweight_score import compute_lightweight_score

NOW = datetime(2026, 9, 29, tzinfo=timezone.utc)


def check(label, cond):
    status = "OK " if cond else "FAIL"
    print(f"[{status}] {label}")
    if not cond:
        raise AssertionError(label)


# 1) 결정론성 — 동일 입력(+동일 now) → 항상 동일 점수
fastapi_meta = {
    "language": "Python", "stars": 102700, "open_issues": 83,
    "pushed_at": "2026-09-28T19:48:59Z",
}
r1 = compute_lightweight_score(fastapi_meta, now=NOW)
r2 = compute_lightweight_score(dict(fastapi_meta), now=NOW)
check("동일 입력 → 동일 점수(순수 함수)", r1 == r2)
print("  fastapi 실측값 기준:", r1)

# 2) 인기·활발·주요언어 레포는 만점에 가까워야 함
check("fastapi 유사 레포 total >= 90", r1["total"] >= 90)

# 3) 극단값 — 신규 레포(스타/이슈 0, 언어 없음, push 정보 없음)는 0에 가까워야 함
empty_meta = {"language": None, "stars": 0, "open_issues": 0, "pushed_at": None}
r3 = compute_lightweight_score(empty_meta, now=NOW)
check("빈 메타데이터 → activity 0", r3["activity"] == 0)
check("빈 메타데이터 → language 0", r3["language"] == 0)
check("빈 메타데이터 → total이 0~100 범위 안", 0 <= r3["total"] <= 100)

# 4) 주요 언어 vs 그 외 언어 vs 언어 없음 — 계단식으로 감소해야 함
base = {"stars": 500, "open_issues": 5, "pushed_at": "2026-09-20T00:00:00Z"}
r_other = compute_lightweight_score({**base, "language": "Elixir"}, now=NOW)  # 목록 밖
r_known = compute_lightweight_score({**base, "language": "Python"}, now=NOW)  # 목록 안
r_none = compute_lightweight_score({**base, "language": None}, now=NOW)
check("주요 언어(Python) > 목록 밖 언어(Elixir) > 언어 없음",
      r_known["language"] > r_other["language"] > r_none["language"])
check("목록 안 언어(Rust)도 주요 언어와 동일하게 만점 — analytics.py 기준 재사용",
      compute_lightweight_score({**base, "language": "Rust"}, now=NOW)["language"] == 10)

# 5) 오래 방치된 레포는 activity가 낮아야 함 (1년 이상 미갱신)
stale_meta = {**base, "language": "Python", "pushed_at": "2023-01-01T00:00:00Z"}
r_stale = compute_lightweight_score(stale_meta, now=NOW)
check("1년+ 미갱신 레포 → activity 0", r_stale["activity"] == 0)

# 6) 모든 하위 점수가 항상 0~상한 범위 안에 있어야 함 (음수/초과 없음)
for meta in [fastapi_meta, empty_meta, stale_meta, {**base, "language": "C++"}]:
    r = compute_lightweight_score(meta, now=NOW)
    check(f"범위 체크 {meta.get('language')}: {r}",
          0 <= r["activity"] <= 30 and 0 <= r["popularity"] <= 35
          and 0 <= r["language"] <= 10 and 0 <= r["health"] <= 25
          and 0 <= r["total"] <= 100)

print("\n전체 통과")
