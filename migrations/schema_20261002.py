"""2026-10-02에 고정한 수동 스키마 변경. 앱 모델을 참조하지 않는다.

과거 revision에서 빠진 선행 테이블과 수동 컬럼을 새 설치/기존 설치 모두에
적용한다. 기존 행을 삭제하거나 중복을 임의로 정리하지 않는다.
"""
from alembic import op


def prerequisites():
    op.execute("""
        CREATE TABLE IF NOT EXISTS repositories (
            id SERIAL PRIMARY KEY, url VARCHAR NOT NULL, name VARCHAR,
            platform VARCHAR DEFAULT 'github', default_branch VARCHAR, description TEXT,
            max_files_per_commit INTEGER, analysis_window_months INTEGER DEFAULT 6,
            last_analyzed_at TIMESTAMPTZ, last_accessed_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ DEFAULT now());
        CREATE TABLE IF NOT EXISTS contributors (
            id SERIAL PRIMARY KEY, repository_id INTEGER REFERENCES repositories(id),
            primary_email VARCHAR, display_name VARCHAR, github_login VARCHAR,
            created_at TIMESTAMPTZ DEFAULT now(),
            CONSTRAINT uq_contributors_repo_email UNIQUE(repository_id, primary_email));
        CREATE TABLE IF NOT EXISTS contributor_aliases (
            id SERIAL PRIMARY KEY, contributor_id INTEGER NOT NULL REFERENCES contributors(id),
            email VARCHAR, name VARCHAR, source VARCHAR, created_at TIMESTAMPTZ DEFAULT now());
        CREATE TABLE IF NOT EXISTS analysis_jobs (
            id SERIAL PRIMARY KEY, repository_id INTEGER REFERENCES repositories(id),
            status VARCHAR DEFAULT 'pending', triggered_by VARCHAR, triggered_by_user_id UUID,
            progress_detail JSONB, error_message TEXT, parameters JSONB,
            last_commit_sha VARCHAR, last_commit_at TIMESTAMPTZ, last_api_fetched_at TIMESTAMPTZ,
            started_at TIMESTAMPTZ, completed_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ DEFAULT now());
    """)


def rename_column(table, old, new):
    # 식별자는 이 고정 migration의 상수만 사용한다.
    op.execute(f"""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_schema=current_schema() AND table_name='{table}' AND column_name='{old}')
           AND NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_schema=current_schema() AND table_name='{table}' AND column_name='{new}') THEN
            ALTER TABLE {table} RENAME COLUMN {old} TO {new};
        END IF;
    END $$""")


def reconcile():
    prerequisites()
    for table, old, new in [
        ('repositories', 'github_url', 'url'), ('commits', 'commit_hash', 'sha'),
        ('files', 'is_delete', 'is_deleted'), ('pull_requests', 'github_pr_number', 'pr_number'),
        ('pull_requests', 'created_at', 'opened_at'), ('pull_request_files', 'status', 'change_type'),
    ]:
        rename_column(table, old, new)
    columns = {
        'repositories': ["name VARCHAR", "platform VARCHAR DEFAULT 'github'", "default_branch VARCHAR",
            "description TEXT", "max_files_per_commit INTEGER", "analysis_window_months INTEGER DEFAULT 6",
            "last_analyzed_at TIMESTAMPTZ", "last_accessed_at TIMESTAMPTZ", "created_at TIMESTAMPTZ DEFAULT now()"],
        'analysis_jobs': ["triggered_by VARCHAR", "triggered_by_user_id UUID", "progress_detail JSONB",
            "error_message TEXT", "parameters JSONB", "last_commit_sha VARCHAR", "last_commit_at TIMESTAMPTZ",
            "last_api_fetched_at TIMESTAMPTZ", "started_at TIMESTAMPTZ", "completed_at TIMESTAMPTZ",
            "created_at TIMESTAMPTZ DEFAULT now()"],
        'contributors': ["primary_email VARCHAR", "display_name VARCHAR", "github_login VARCHAR",
            "created_at TIMESTAMPTZ DEFAULT now()", "resolution_status VARCHAR DEFAULT 'unresolved'",
            "identity_search_attempted_at TIMESTAMPTZ"],
        'contributor_aliases': ["resolution_rule VARCHAR"],
        'commits': ["contributor_id INTEGER REFERENCES contributors(id)", "commit_type VARCHAR",
            "commit_type_source VARCHAR", "commit_type_confidence DOUBLE PRECISION",
            "changed_file_count INTEGER", "is_large_commit BOOLEAN DEFAULT false", "on_default_branch BOOLEAN"],
        'files': ["created_at TIMESTAMPTZ DEFAULT now()"],
        'commit_file_changes': ["change_type VARCHAR"],
        'pull_requests': ["author_login VARCHAR", "closed_at TIMESTAMPTZ", "review_duration_hours INTEGER"],
    }
    for table, definitions in columns.items():
        for definition in definitions:
            op.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {definition}")
    for table, column in [('commits', 'committed_at'), ('pull_requests', 'opened_at'),
                          ('pull_requests', 'merged_at'), ('pr_reviews', 'submitted_at'), ('releases', 'published_at')]:
        op.execute(f"""DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM information_schema.columns
                       WHERE table_schema=current_schema() AND table_name='{table}'
                       AND column_name='{column}' AND data_type NOT IN ('timestamp with time zone')) THEN
                ALTER TABLE {table} ALTER COLUMN {column} TYPE TIMESTAMPTZ
                    USING NULLIF({column}::text, '')::timestamptz;
            END IF;
        END $$""")
    # 전역 SHA unique는 다른 저장소의 같은 커밋을 거부하므로 저장소별 unique로 교체한다.
    op.execute("""DO $$ DECLARE item RECORD; BEGIN
        FOR item IN SELECT c.conname FROM pg_constraint c
            JOIN pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=ANY(c.conkey)
            WHERE c.conrelid='commits'::regclass AND c.contype='u'
              AND array_length(c.conkey,1)=1 AND a.attname='sha'
        LOOP EXECUTE format('ALTER TABLE commits DROP CONSTRAINT %I', item.conname); END LOOP;
    END $$""")
    for name, table, columns in [
        ('uq_commits_repo_hash', 'commits', 'repository_id,sha'),
        ('uq_files_repo_path', 'files', 'repository_id,path'),
        ('uq_contributors_repo_email', 'contributors', 'repository_id,primary_email'),
        ('uq_contributor_aliases_cid_email_name', 'contributor_aliases', "contributor_id,COALESCE(email,''),COALESCE(name,'')"),
    ]:
        op.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS {name} ON {table} ({columns})")
    for table, column in [('commits','repository_id'), ('commits','contributor_id'),
                          ('files','repository_id'), ('contributors','repository_id'),
                          ('analysis_jobs','repository_id'), ('contributor_aliases','contributor_id'),
                          ('commit_file_changes','commit_id'), ('commit_file_changes','file_id'),
                          ('pull_requests','repository_id'), ('pull_requests','author_contributor_id'),
                          ('pr_reviews','reviewer_contributor_id')]:
        op.execute(f"CREATE INDEX IF NOT EXISTS ix_{table}_{column} ON {table} ({column})")


def users():
    op.execute("""CREATE TABLE IF NOT EXISTS users (
        id UUID PRIMARY KEY, github_id BIGINT NOT NULL UNIQUE, github_login VARCHAR(255) NOT NULL,
        display_name VARCHAR(255), avatar_url TEXT, email VARCHAR(255), access_token_encrypted TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(), last_login_at TIMESTAMPTZ);
        ALTER TABLE users ADD COLUMN IF NOT EXISTS access_token_encrypted TEXT;
        CREATE INDEX IF NOT EXISTS ix_users_github_id ON users (github_id);
    """)
