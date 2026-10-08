"""AI 팀(S13): 팀·구성원·연결 저장소·명시적 공유·보고서.

고정 DDL이며 앱 모델을 참조하지 않는다. 개인 결과·작업 테이블은 바꾸지 않는다(팀 자료는 새 테이블에만 둔다).
공유 기록은 원본(결과·인계 대상)과 저장소 연결이 사라지면 함께 사라지고, 보고서는 수정할 수 없다.
"""
from alembic import op

revision = 'f6b8d0a2c4e1'
down_revision = 'e5a7c9d1f3b2'
branch_labels = None
depends_on = None

HEX64 = r"'^[0-9a-f]{64}$'"

UPGRADE_SQL = f"""
CREATE TABLE teams (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    created_by UUID REFERENCES users(id) ON DELETE SET NULL,
    visibility_epoch INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_teams_name CHECK (char_length(name) BETWEEN 1 AND 80),
    CONSTRAINT ck_teams_epoch CHECK (visibility_epoch >= 1)
);

CREATE TABLE team_members (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    team_id UUID NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'invited',
    invited_by UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_team_members_team_user UNIQUE (team_id, user_id),
    CONSTRAINT ck_team_members_role CHECK (role IN ('admin', 'member', 'viewer')),
    CONSTRAINT ck_team_members_status CHECK (status IN ('invited', 'active'))
);
CREATE INDEX ix_team_members_user ON team_members (user_id, status);

CREATE TABLE team_repositories (
    team_id UUID NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    repository_id INTEGER NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
    added_by UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT pk_team_repositories PRIMARY KEY (team_id, repository_id)
);

CREATE TABLE team_resource_shares (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    team_id UUID NOT NULL,
    repository_id INTEGER NOT NULL,
    resource_type TEXT NOT NULL,
    review_result_id UUID,
    handover_target_id UUID,
    shared_by UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    pinned_target_version INTEGER,
    pinned_preparation_version INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT fk_team_shares_team_repo FOREIGN KEY (team_id, repository_id)
        REFERENCES team_repositories (team_id, repository_id) ON DELETE CASCADE,
    CONSTRAINT fk_team_shares_result FOREIGN KEY (review_result_id, shared_by, repository_id)
        REFERENCES ai_results (id, owner_user_id, repository_id) ON DELETE CASCADE,
    CONSTRAINT fk_team_shares_target FOREIGN KEY (handover_target_id, shared_by, repository_id)
        REFERENCES handover_targets (id, owner_user_id, repository_id) ON DELETE CASCADE,
    CONSTRAINT uq_team_shares_result UNIQUE (team_id, review_result_id),
    CONSTRAINT uq_team_shares_target UNIQUE (team_id, handover_target_id),
    CONSTRAINT ck_team_shares_type CHECK (resource_type IN ('review_result', 'handover_target')),
    CONSTRAINT ck_team_shares_resource CHECK (
        (resource_type = 'review_result' AND review_result_id IS NOT NULL AND handover_target_id IS NULL
            AND pinned_target_version IS NULL AND pinned_preparation_version IS NULL)
        OR (resource_type = 'handover_target' AND handover_target_id IS NOT NULL AND review_result_id IS NULL
            AND pinned_target_version IS NOT NULL AND pinned_preparation_version IS NOT NULL))
);
CREATE INDEX ix_team_shares_team ON team_resource_shares (team_id, created_at);
CREATE INDEX ix_team_shares_sharer ON team_resource_shares (shared_by);

CREATE TABLE team_reports (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    team_id UUID NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    created_by UUID REFERENCES users(id) ON DELETE SET NULL,
    repository_ids JSONB NOT NULL,
    source_manifest JSONB NOT NULL,
    payload JSONB NOT NULL,
    markdown TEXT NOT NULL,
    payload_hash CHAR(64) NOT NULL,
    visibility_epoch INTEGER NOT NULL,
    template_version TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_team_reports_hash CHECK (payload_hash ~ {HEX64}),
    CONSTRAINT ck_team_reports_json CHECK (jsonb_typeof(repository_ids) = 'array' AND jsonb_typeof(source_manifest) = 'object'
        AND jsonb_typeof(payload) = 'object'),
    CONSTRAINT ck_team_reports_size CHECK (char_length(markdown) <= 100000 AND visibility_epoch >= 1)
);
CREATE INDEX ix_team_reports_team ON team_reports (team_id, created_at DESC);
CREATE TRIGGER team_reports_immutable BEFORE UPDATE ON team_reports
    FOR EACH ROW EXECUTE FUNCTION gitviz_ai_forbid_update();
"""

DOWNGRADE_SQL = """
DROP TABLE IF EXISTS team_reports;
DROP TABLE IF EXISTS team_resource_shares;
DROP TABLE IF EXISTS team_repositories;
DROP TABLE IF EXISTS team_members;
DROP TABLE IF EXISTS teams;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
