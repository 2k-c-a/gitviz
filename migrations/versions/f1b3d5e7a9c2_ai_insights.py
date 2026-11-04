"""Add AI insight reports and conversations (automatic and conversational analysis). Additive only."""
from alembic import op

revision = 'f1b3d5e7a9c2'
down_revision = 'd4f6a8c0e2b3'
branch_labels = None
depends_on = None

_STATUS = "status IN ('queued','running','succeeded','failed')"


def upgrade():
    op.execute(f"""CREATE TABLE ai_insight_reports (
        id UUID DEFAULT gen_random_uuid() NOT NULL,
        owner_user_id UUID NOT NULL,
        repository_id INTEGER NOT NULL,
        snapshot_id INTEGER NOT NULL,
        status TEXT DEFAULT 'queued' NOT NULL,
        provider_profile TEXT NOT NULL,
        include_code BOOLEAN DEFAULT false NOT NULL,
        provider TEXT NOT NULL,
        model TEXT NOT NULL,
        prompt_version TEXT NOT NULL,
        tool_version TEXT NOT NULL,
        policy_version TEXT NOT NULL,
        progress JSONB DEFAULT '[]'::jsonb NOT NULL,
        result JSONB,
        error_code TEXT,
        call_count INTEGER DEFAULT 0 NOT NULL,
        cost NUMERIC(18, 6),
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        started_at TIMESTAMP WITH TIME ZONE,
        finished_at TIMESTAMP WITH TIME ZONE,
        deleted_at TIMESTAMP WITH TIME ZONE,
        PRIMARY KEY (id),
        FOREIGN KEY(owner_user_id) REFERENCES users (id) ON DELETE CASCADE,
        FOREIGN KEY(repository_id) REFERENCES repositories (id) ON DELETE CASCADE,
        FOREIGN KEY(snapshot_id) REFERENCES analysis_snapshots (id) ON DELETE CASCADE,
        CONSTRAINT ck_ai_insight_reports_status CHECK ({_STATUS}),
        CONSTRAINT ck_ai_insight_reports_progress CHECK (jsonb_typeof(progress) = 'array'),
        CONSTRAINT ck_ai_insight_reports_result CHECK (result IS NULL OR jsonb_typeof(result) = 'object'),
        CONSTRAINT ck_ai_insight_reports_failed_code CHECK (status <> 'failed' OR error_code IS NOT NULL)
    )""")
    op.execute('CREATE INDEX ix_ai_insight_reports_owner_repo ON ai_insight_reports (owner_user_id, repository_id, created_at)')
    op.execute("""CREATE TABLE ai_insight_conversations (
        id UUID DEFAULT gen_random_uuid() NOT NULL,
        owner_user_id UUID NOT NULL,
        repository_id INTEGER NOT NULL,
        snapshot_id INTEGER NOT NULL,
        provider_profile TEXT NOT NULL,
        include_code BOOLEAN DEFAULT false NOT NULL,
        title TEXT DEFAULT '' NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        deleted_at TIMESTAMP WITH TIME ZONE,
        PRIMARY KEY (id),
        FOREIGN KEY(owner_user_id) REFERENCES users (id) ON DELETE CASCADE,
        FOREIGN KEY(repository_id) REFERENCES repositories (id) ON DELETE CASCADE,
        FOREIGN KEY(snapshot_id) REFERENCES analysis_snapshots (id) ON DELETE CASCADE
    )""")
    op.execute('CREATE INDEX ix_ai_insight_conversations_owner_repo ON ai_insight_conversations (owner_user_id, repository_id, updated_at)')
    op.execute(f"""CREATE TABLE ai_insight_messages (
        id UUID DEFAULT gen_random_uuid() NOT NULL,
        conversation_id UUID NOT NULL,
        seq INTEGER NOT NULL,
        role TEXT NOT NULL,
        content JSONB DEFAULT '{{}}'::jsonb NOT NULL,
        status TEXT DEFAULT 'succeeded' NOT NULL,
        progress JSONB DEFAULT '[]'::jsonb NOT NULL,
        error_code TEXT,
        provider TEXT,
        model TEXT,
        prompt_version TEXT,
        tool_version TEXT,
        call_count INTEGER DEFAULT 0 NOT NULL,
        cost NUMERIC(18, 6),
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        finished_at TIMESTAMP WITH TIME ZONE,
        PRIMARY KEY (id),
        FOREIGN KEY(conversation_id) REFERENCES ai_insight_conversations (id) ON DELETE CASCADE,
        CONSTRAINT uq_ai_insight_messages_seq UNIQUE (conversation_id, seq),
        CONSTRAINT ck_ai_insight_messages_role CHECK (role IN ('user','assistant')),
        CONSTRAINT ck_ai_insight_messages_status CHECK ({_STATUS}),
        CONSTRAINT ck_ai_insight_messages_json CHECK (jsonb_typeof(content) = 'object' AND jsonb_typeof(progress) = 'array'),
        CONSTRAINT ck_ai_insight_messages_failed_code CHECK (status <> 'failed' OR error_code IS NOT NULL)
    )""")


def downgrade():
    raise RuntimeError('AI insight reports and conversations are user content and are preserved; hide the feature instead.')
