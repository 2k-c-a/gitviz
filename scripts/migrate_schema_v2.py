"""ERD v2 정렬 마이그레이션 (PK는 int 유지).

- 누락 컬럼 추가, 네이밍 정렬, String 날짜 -> timestamptz 전환
- contributor_aliases 테이블 신규 생성
- repositories.status 제거 (분석 상태는 analysis_jobs로 일원화)
- 전체가 단일 트랜잭션: 실패 시 롤백
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from app.core.database import SessionLocal

db = SessionLocal()
db.execute(text("SET statement_timeout = 0"))

steps = [
    # ---------- repositories ----------
    ("repositories: rename github_url -> url",
     "ALTER TABLE repositories RENAME COLUMN github_url TO url"),
    ("repositories: add columns", """
        ALTER TABLE repositories
        ADD COLUMN IF NOT EXISTS name VARCHAR,
        ADD COLUMN IF NOT EXISTS platform VARCHAR DEFAULT 'github',
        ADD COLUMN IF NOT EXISTS default_branch VARCHAR,
        ADD COLUMN IF NOT EXISTS description TEXT,
        ADD COLUMN IF NOT EXISTS max_files_per_commit INTEGER,
        ADD COLUMN IF NOT EXISTS analysis_window_months INTEGER DEFAULT 6,
        ADD COLUMN IF NOT EXISTS last_analyzed_at TIMESTAMPTZ,
        ADD COLUMN IF NOT EXISTS last_accessed_at TIMESTAMPTZ,
        ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT now()"""),
    ("repositories: backfill name/platform",
     "UPDATE repositories SET name = regexp_replace(url, '^.*/', ''), platform = 'github' WHERE name IS NULL"),
    ("repositories: drop status",
     "ALTER TABLE repositories DROP COLUMN IF EXISTS status"),

    # ---------- analysis_jobs ----------
    ("analysis_jobs: add columns", """
        ALTER TABLE analysis_jobs
        ADD COLUMN IF NOT EXISTS triggered_by VARCHAR,
        ADD COLUMN IF NOT EXISTS error_message TEXT,
        ADD COLUMN IF NOT EXISTS parameters JSONB,
        ADD COLUMN IF NOT EXISTS last_commit_sha VARCHAR,
        ADD COLUMN IF NOT EXISTS last_commit_at TIMESTAMPTZ,
        ADD COLUMN IF NOT EXISTS last_api_fetched_at TIMESTAMPTZ,
        ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ,
        ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ,
        ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT now()"""),
    ("analysis_jobs: status 소문자 정규화",
     "UPDATE analysis_jobs SET status = CASE lower(status) WHEN 'processing' THEN 'running' ELSE lower(status) END"),
    ("analysis_jobs: add FK", """
        DO $$ BEGIN
            ALTER TABLE analysis_jobs
            ADD CONSTRAINT fk_analysis_jobs_repository
            FOREIGN KEY (repository_id) REFERENCES repositories(id);
        EXCEPTION WHEN duplicate_object THEN NULL; END $$"""),
    ("analysis_jobs: FK index",
     "CREATE INDEX IF NOT EXISTS ix_analysis_jobs_repository_id ON analysis_jobs (repository_id)"),

    # ---------- contributors ----------
    ("contributors: add created_at",
     "ALTER TABLE contributors ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT now()"),

    # ---------- contributor_aliases (신규) ----------
    ("contributor_aliases: create table", """
        CREATE TABLE IF NOT EXISTS contributor_aliases (
            id SERIAL PRIMARY KEY,
            contributor_id INTEGER NOT NULL REFERENCES contributors(id),
            email VARCHAR,
            name VARCHAR,
            source VARCHAR,
            created_at TIMESTAMPTZ DEFAULT now()
        )"""),
    ("contributor_aliases: indexes", """
        CREATE INDEX IF NOT EXISTS ix_contributor_aliases_contributor_id
        ON contributor_aliases (contributor_id)"""),
    ("contributor_aliases: unique", """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_contributor_aliases_cid_email_name
        ON contributor_aliases (contributor_id, COALESCE(email, ''), COALESCE(name, ''))"""),

    # ---------- commits ----------
    ("commits: rename commit_hash -> sha",
     "ALTER TABLE commits RENAME COLUMN commit_hash TO sha"),
    ("commits: add columns", """
        ALTER TABLE commits
        ADD COLUMN IF NOT EXISTS contributor_id INTEGER REFERENCES contributors(id),
        ADD COLUMN IF NOT EXISTS commit_type VARCHAR,
        ADD COLUMN IF NOT EXISTS changed_file_count INTEGER"""),
    ("commits: contributor_id index",
     "CREATE INDEX IF NOT EXISTS ix_commits_contributor_id ON commits (contributor_id)"),
    ("commits: committed_at -> timestamptz",
     "ALTER TABLE commits ALTER COLUMN committed_at TYPE TIMESTAMPTZ USING committed_at::timestamptz"),
    ("commits: backfill changed_file_count", """
        UPDATE commits c SET changed_file_count = sub.cnt
        FROM (SELECT commit_id, COUNT(*) AS cnt FROM commit_file_changes GROUP BY commit_id) sub
        WHERE sub.commit_id = c.id"""),

    # ---------- files ----------
    ("files: rename is_delete -> is_deleted",
     "ALTER TABLE files RENAME COLUMN is_delete TO is_deleted"),
    ("files: add created_at",
     "ALTER TABLE files ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT now()"),

    # ---------- commit_file_changes ----------
    ("commit_file_changes: add change_type",
     "ALTER TABLE commit_file_changes ADD COLUMN IF NOT EXISTS change_type VARCHAR"),

    # ---------- pull_requests ----------
    ("pull_requests: rename github_pr_number -> pr_number",
     "ALTER TABLE pull_requests RENAME COLUMN github_pr_number TO pr_number"),
    ("pull_requests: rename created_at -> opened_at",
     "ALTER TABLE pull_requests RENAME COLUMN created_at TO opened_at"),
    ("pull_requests: opened_at -> timestamptz",
     "ALTER TABLE pull_requests ALTER COLUMN opened_at TYPE TIMESTAMPTZ USING opened_at::timestamptz"),
    ("pull_requests: merged_at -> timestamptz",
     "ALTER TABLE pull_requests ALTER COLUMN merged_at TYPE TIMESTAMPTZ USING merged_at::timestamptz"),
    ("pull_requests: add columns", """
        ALTER TABLE pull_requests
        ADD COLUMN IF NOT EXISTS author_login VARCHAR,
        ADD COLUMN IF NOT EXISTS closed_at TIMESTAMPTZ,
        ADD COLUMN IF NOT EXISTS review_duration_hours INTEGER"""),
    ("pull_requests: backfill review_duration_hours", """
        UPDATE pull_requests
        SET review_duration_hours = FLOOR(EXTRACT(EPOCH FROM (merged_at - opened_at)) / 3600)
        WHERE merged_at IS NOT NULL AND opened_at IS NOT NULL"""),

    # ---------- pr_reviews ----------
    ("pr_reviews: submitted_at -> timestamptz",
     "ALTER TABLE pr_reviews ALTER COLUMN submitted_at TYPE TIMESTAMPTZ USING submitted_at::timestamptz"),

    # ---------- pull_request_files ----------
    ("pull_request_files: rename status -> change_type",
     "ALTER TABLE pull_request_files RENAME COLUMN status TO change_type"),

    # ---------- releases ----------
    ("releases: published_at -> timestamptz",
     "ALTER TABLE releases ALTER COLUMN published_at TYPE TIMESTAMPTZ USING published_at::timestamptz"),
]

try:
    for label, q in steps:
        db.execute(text(q))
        print(f"OK  {label}")
    db.commit()
    print("\nCOMMIT 완료")
except Exception as e:
    db.rollback()
    print(f"\nROLLBACK: {label}")
    raise
finally:
    db.close()
