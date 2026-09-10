"""pull_requests.changed_file_count / is_large_pr 백필.

버그: collect_pull_requests()가 쓰는 GitHub PR 목록 API(GET /pulls?state=closed)는
changed_files 필드를 애초에 안 준다(단건 조회 GET /pulls/{number}에서만 내려줌) —
그래서 이 값이 항상 0으로 저장돼 있었고, is_large_pr 판정(파일 15개 초과 또는 전체
파일의 10% 초과)이 사실상 전부 실행돼도 False로만 나와서 대형/머지 PR이 하나도
안 걸러진 채 Coupling·Network의 co-change 신호를 오염시키고 있었다.

pull_request_files에는 GraphQL로 수집한 실제 파일 목록이 이미 들어있으니, API를
다시 호출할 필요 없이 그 실제 개수로 재계산만 하면 된다.

사용법:
  python scripts/backfill_pr_large_flag.py            # 전체 레포지토리
  python scripts/backfill_pr_large_flag.py <repo_id>  # 특정 레포지토리만
"""

import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from app.core.database import SessionLocal

LARGE_PR_THRESHOLD = 15
LARGE_PR_PCT = 0.10

db = SessionLocal()
db.execute(text("SET statement_timeout = 0"))

rid_arg = sys.argv[1] if len(sys.argv) > 1 else None
repo_ids = (
    [int(rid_arg)]
    if rid_arg
    else [r[0] for r in db.execute(text("SELECT id FROM repositories")).fetchall()]
)
print(f"대상 레포지토리: {len(repo_ids)}개")

for rid in repo_ids:
    total_files = db.execute(
        text("SELECT COUNT(*) FROM files WHERE repository_id = :r"), {"r": rid}
    ).scalar()

    before = db.execute(
        text("SELECT COUNT(*) FROM pull_requests WHERE repository_id = :r AND is_large_pr"),
        {"r": rid},
    ).scalar()
    total_prs = db.execute(
        text("SELECT COUNT(*) FROM pull_requests WHERE repository_id = :r"), {"r": rid}
    ).scalar()

    if not total_prs:
        continue

    db.execute(
        text(
            """
            UPDATE pull_requests pr SET
                changed_file_count = COALESCE(sub.cnt, 0),
                is_large_pr = (
                    COALESCE(sub.cnt, 0) > :thr
                    OR (:total > 0 AND COALESCE(sub.cnt, 0) > :total * :pct)
                )
            FROM (
                SELECT pull_request_id, COUNT(*) AS cnt
                FROM pull_request_files
                GROUP BY pull_request_id
            ) sub
            WHERE pr.repository_id = :r AND sub.pull_request_id = pr.id
            """
        ),
        {"r": rid, "thr": LARGE_PR_THRESHOLD, "total": total_files, "pct": LARGE_PR_PCT},
    )
    db.commit()

    after = db.execute(
        text("SELECT COUNT(*) FROM pull_requests WHERE repository_id = :r AND is_large_pr"),
        {"r": rid},
    ).scalar()
    print(f"  repo={rid}: total_prs={total_prs} is_large_pr {before} → {after} (total_files={total_files})")

db.close()
print("완료")
