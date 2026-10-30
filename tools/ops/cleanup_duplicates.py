"""repository_id=2~9 중복 데이터 + analysis_jobs 고아 레코드 정리.

FK에 ON DELETE CASCADE가 없으므로 자식 테이블부터 순서대로 삭제한다.
전체가 단일 트랜잭션이며, 교차 참조 발견 시 롤백하고 중단한다.
"""

import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from sqlalchemy import text
from app.core.database import SessionLocal

KEEP_ID = 1
TARGET = "repository_id BETWEEN 2 AND 9"

db = SessionLocal()

# Supabase 기본 statement timeout 해제 (이 세션에 한함)
db.execute(text("SET statement_timeout = 0"))

# FK 검사용 인덱스 (없으면 DELETE 시 자식 테이블 풀스캔으로 timeout 발생)
fk_indexes = [
    "CREATE INDEX IF NOT EXISTS ix_pr_commits_commit_id ON pr_commits (commit_id)",
    "CREATE INDEX IF NOT EXISTS ix_cfc_commit_id ON commit_file_changes (commit_id)",
    "CREATE INDEX IF NOT EXISTS ix_cfc_file_id ON commit_file_changes (file_id)",
    "CREATE INDEX IF NOT EXISTS ix_prf_file_id ON pull_request_files (file_id)",
    "CREATE INDEX IF NOT EXISTS ix_pr_author_contributor_id ON pull_requests (author_contributor_id)",
    "CREATE INDEX IF NOT EXISTS ix_prr_reviewer_contributor_id ON pr_reviews (reviewer_contributor_id)",
    "CREATE INDEX IF NOT EXISTS ix_commits_repository_id ON commits (repository_id)",
    "CREATE INDEX IF NOT EXISTS ix_files_repository_id ON files (repository_id)",
    "CREATE INDEX IF NOT EXISTS ix_contributors_repository_id ON contributors (repository_id)",
    "CREATE INDEX IF NOT EXISTS ix_pull_requests_repository_id ON pull_requests (repository_id)",
    "CREATE INDEX IF NOT EXISTS ix_releases_repository_id ON releases (repository_id)",
]
print("=== FK 인덱스 생성 ===")
for q in fk_indexes:
    db.execute(text(q))
    print(f"  {q.split(' ')[5]}")
db.commit()
db.execute(text("SET statement_timeout = 0"))

# --- 사전 체크: 살아남을 데이터(repo1)가 삭제 대상 레코드를 참조하면 중단 ---
checks = {
    "repo1 pr_reviews -> target contributors": f"""
        SELECT COUNT(*) FROM pr_reviews rv
        JOIN pull_requests pr ON pr.id = rv.pull_request_id
        WHERE pr.repository_id = {KEEP_ID}
          AND rv.reviewer_contributor_id IN (SELECT id FROM contributors WHERE {TARGET})""",
    "repo1 pull_requests -> target contributors": f"""
        SELECT COUNT(*) FROM pull_requests
        WHERE repository_id = {KEEP_ID}
          AND author_contributor_id IN (SELECT id FROM contributors WHERE {TARGET})""",
    "repo1 pr_commits -> target commits": f"""
        SELECT COUNT(*) FROM pr_commits pc
        JOIN pull_requests pr ON pr.id = pc.pull_request_id
        WHERE pr.repository_id = {KEEP_ID}
          AND pc.commit_id IN (SELECT id FROM commits WHERE {TARGET})""",
    "repo1 commit_file_changes -> target files": f"""
        SELECT COUNT(*) FROM commit_file_changes cfc
        JOIN commits c ON c.id = cfc.commit_id
        WHERE c.repository_id = {KEEP_ID}
          AND cfc.file_id IN (SELECT id FROM files WHERE {TARGET})""",
    "repo1 pull_request_files -> target files": f"""
        SELECT COUNT(*) FROM pull_request_files pf
        JOIN pull_requests pr ON pr.id = pf.pull_request_id
        WHERE pr.repository_id = {KEEP_ID}
          AND pf.file_id IN (SELECT id FROM files WHERE {TARGET})""",
}

print("=== 사전 체크 (전부 0이어야 함) ===")
ok = True
for label, q in checks.items():
    n = db.execute(text(q)).scalar()
    print(f"  {label}: {n}")
    if n:
        ok = False

if not ok:
    print("\n교차 참조 발견 — 삭제 중단. 수동 확인 필요.")
    db.close()
    sys.exit(1)

# --- 삭제 (자식 → 부모 순) ---
deletes = [
    (
        "commit_file_changes",
        f"""
        DELETE FROM commit_file_changes
        WHERE commit_id IN (SELECT id FROM commits WHERE {TARGET})
           OR file_id   IN (SELECT id FROM files   WHERE {TARGET})""",
    ),
    (
        "pr_commits",
        f"""
        DELETE FROM pr_commits
        WHERE pull_request_id IN (SELECT id FROM pull_requests WHERE {TARGET})
           OR commit_id       IN (SELECT id FROM commits       WHERE {TARGET})""",
    ),
    (
        "pr_reviews",
        f"""
        DELETE FROM pr_reviews
        WHERE pull_request_id IN (SELECT id FROM pull_requests WHERE {TARGET})""",
    ),
    (
        "pull_request_files",
        f"""
        DELETE FROM pull_request_files
        WHERE pull_request_id IN (SELECT id FROM pull_requests WHERE {TARGET})
           OR file_id         IN (SELECT id FROM files         WHERE {TARGET})""",
    ),
    ("pull_requests", f"DELETE FROM pull_requests WHERE {TARGET}"),
    ("releases", f"DELETE FROM releases WHERE {TARGET}"),
    ("commits", f"DELETE FROM commits WHERE {TARGET}"),
    ("files", f"DELETE FROM files WHERE {TARGET}"),
    ("contributors", f"DELETE FROM contributors WHERE {TARGET}"),
    (
        "analysis_jobs (repo!=1)",
        f"DELETE FROM analysis_jobs WHERE repository_id <> {KEEP_ID}",
    ),
    ("repositories", "DELETE FROM repositories WHERE id BETWEEN 2 AND 9"),
]

print("\n=== 삭제 실행 ===")
try:
    for label, q in deletes:
        result = db.execute(text(q))
        print(f"  {label}: {result.rowcount} rows")
    db.commit()
    print("\nCOMMIT 완료")
except Exception:
    db.rollback()
    print("\n오류 발생 — ROLLBACK")
    raise
finally:
    db.close()
