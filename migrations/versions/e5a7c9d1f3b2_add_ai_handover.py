"""AI 인수인계(S12): 인계 대상·준비 정보 버전·생성 원본·편집 문서.

고정 DDL이며 앱 모델을 참조하지 않는다. 준비 정보 버전·생성 원본·문서 편집 이력은 추가만 하고(수정 불가), 편집본 문서만 expected_version으로 갱신한다.
"""
from alembic import op

revision = 'e5a7c9d1f3b2'
down_revision = 'd4f8b2c6e1a9'
branch_labels = None
depends_on = None

HEX64 = r"'^[0-9a-f]{64}$'"

UPGRADE_SQL = f"""
CREATE TABLE handover_targets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    repository_id INTEGER NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    purpose TEXT,
    giver_name TEXT,
    receiver_name TEXT,
    selected_paths JSONB NOT NULL,
    snapshot_id INTEGER,
    version INTEGER NOT NULL DEFAULT 1,
    scope_version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT fk_handover_targets_snapshot FOREIGN KEY (snapshot_id, repository_id)
        REFERENCES analysis_snapshots (id, repository_id),
    CONSTRAINT uq_handover_targets_id_owner_repo UNIQUE (id, owner_user_id, repository_id),
    CONSTRAINT ck_handover_targets_name CHECK (char_length(name) BETWEEN 1 AND 120),
    CONSTRAINT ck_handover_targets_memo CHECK ((purpose IS NULL OR char_length(purpose) <= 500)
        AND (giver_name IS NULL OR char_length(giver_name) <= 100) AND (receiver_name IS NULL OR char_length(receiver_name) <= 100)),
    CONSTRAINT ck_handover_targets_paths CHECK (jsonb_typeof(selected_paths) = 'array'
        AND jsonb_array_length(selected_paths) BETWEEN 1 AND 50),
    CONSTRAINT ck_handover_targets_version CHECK (version >= 1 AND scope_version >= 1 AND scope_version <= version)
);
CREATE INDEX ix_handover_targets_owner_repo ON handover_targets (owner_user_id, repository_id, updated_at DESC);
CREATE TRIGGER handover_targets_identity BEFORE UPDATE ON handover_targets
    FOR EACH ROW EXECUTE FUNCTION gitviz_ai_immutable_columns('owner_user_id', 'repository_id', 'created_at');

CREATE TABLE handover_preparation_versions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    target_id UUID NOT NULL,
    owner_user_id UUID NOT NULL,
    repository_id INTEGER NOT NULL,
    version INTEGER NOT NULL,
    target_version INTEGER NOT NULL,
    items JSONB NOT NULL,
    author_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT fk_handover_prep_target FOREIGN KEY (target_id, owner_user_id, repository_id)
        REFERENCES handover_targets (id, owner_user_id, repository_id) ON DELETE CASCADE,
    CONSTRAINT uq_handover_prep_target_version UNIQUE (target_id, version),
    CONSTRAINT ck_handover_prep_version CHECK (version >= 1 AND target_version >= 1),
    CONSTRAINT ck_handover_prep_items CHECK (jsonb_typeof(items) = 'object')
);
CREATE TRIGGER handover_prep_immutable BEFORE UPDATE ON handover_preparation_versions
    FOR EACH ROW EXECUTE FUNCTION gitviz_ai_forbid_update();

CREATE TABLE handover_generations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    target_id UUID NOT NULL,
    owner_user_id UUID NOT NULL,
    repository_id INTEGER NOT NULL,
    number INTEGER NOT NULL,
    fingerprint CHAR(64) NOT NULL,
    target_version INTEGER NOT NULL,
    preparation_version INTEGER NOT NULL DEFAULT 0,
    sources JSONB NOT NULL,
    body JSONB NOT NULL,
    markdown TEXT NOT NULL,
    body_hash CHAR(64) NOT NULL,
    template_version TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT fk_handover_generations_target FOREIGN KEY (target_id, owner_user_id, repository_id)
        REFERENCES handover_targets (id, owner_user_id, repository_id) ON DELETE CASCADE,
    CONSTRAINT uq_handover_generations_number UNIQUE (target_id, number),
    CONSTRAINT uq_handover_generations_id_target UNIQUE (id, target_id),
    CONSTRAINT ck_handover_generations_hashes CHECK (fingerprint ~ {HEX64} AND body_hash ~ {HEX64}),
    CONSTRAINT ck_handover_generations_json CHECK (jsonb_typeof(sources) = 'object' AND jsonb_typeof(body) = 'object'),
    CONSTRAINT ck_handover_generations_size CHECK (char_length(markdown) <= 100000),
    CONSTRAINT ck_handover_generations_numbers CHECK (number >= 1 AND target_version >= 1 AND preparation_version >= 0)
);
CREATE TRIGGER handover_generations_immutable BEFORE UPDATE ON handover_generations
    FOR EACH ROW EXECUTE FUNCTION gitviz_ai_forbid_update();

CREATE TABLE handover_documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    target_id UUID NOT NULL,
    owner_user_id UUID NOT NULL,
    repository_id INTEGER NOT NULL,
    base_generation_id UUID NOT NULL,
    markdown TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    edited BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT fk_handover_documents_target FOREIGN KEY (target_id, owner_user_id, repository_id)
        REFERENCES handover_targets (id, owner_user_id, repository_id) ON DELETE CASCADE,
    CONSTRAINT fk_handover_documents_generation FOREIGN KEY (base_generation_id, target_id)
        REFERENCES handover_generations (id, target_id) ON DELETE CASCADE,
    CONSTRAINT uq_handover_documents_target UNIQUE (target_id),
    CONSTRAINT uq_handover_documents_id_target UNIQUE (id, target_id),
    CONSTRAINT ck_handover_documents_size CHECK (char_length(markdown) <= 100000),
    CONSTRAINT ck_handover_documents_version CHECK (version >= 1)
);
CREATE TRIGGER handover_documents_identity BEFORE UPDATE ON handover_documents
    FOR EACH ROW EXECUTE FUNCTION gitviz_ai_immutable_columns('target_id', 'owner_user_id', 'repository_id', 'created_at');

CREATE TABLE handover_document_versions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID NOT NULL REFERENCES handover_documents(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    kind TEXT NOT NULL,
    markdown TEXT NOT NULL,
    base_generation_id UUID NOT NULL,
    author_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_handover_document_versions UNIQUE (document_id, version),
    CONSTRAINT ck_handover_document_versions_kind CHECK (kind IN ('generated', 'edited', 'adopted')),
    CONSTRAINT ck_handover_document_versions_size CHECK (char_length(markdown) <= 100000 AND version >= 1)
);
CREATE TRIGGER handover_document_versions_immutable BEFORE UPDATE ON handover_document_versions
    FOR EACH ROW EXECUTE FUNCTION gitviz_ai_forbid_update();
"""

DOWNGRADE_SQL = """
DROP TABLE IF EXISTS handover_document_versions;
DROP TABLE IF EXISTS handover_documents;
DROP TABLE IF EXISTS handover_generations;
DROP TABLE IF EXISTS handover_preparation_versions;
DROP TABLE IF EXISTS handover_targets;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
