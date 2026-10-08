"""사용자 API key(BYOK): 암호화 저장·버전, 작업에 제공자·모델·key 버전 고정.

고정 DDL이며 앱 모델을 참조하지 않는다. 기존 AI 테이블의 데이터는 그대로 두고 ai_jobs에 nullable 컬럼만 더한다.
"""
from alembic import op

revision = 'c3e9a1b7d550'
down_revision = 'a7c1e5b2d903'
branch_labels = None
depends_on = None

HEX64 = r"'^[0-9a-f]{64}$'"

UPGRADE_SQL = f"""
CREATE TABLE ai_credentials (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    preferred_profile_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_ai_credentials_owner_provider UNIQUE (owner_user_id, provider),
    CONSTRAINT uq_ai_credentials_id_owner UNIQUE (id, owner_user_id),
    CONSTRAINT ck_ai_credentials_status CHECK (status IN ('active', 'deleted')),
    CONSTRAINT ck_ai_credentials_provider CHECK (provider ~ '^[a-z][a-z0-9_-]{{1,31}}$')
);

CREATE TABLE ai_credential_versions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    credential_id UUID NOT NULL,
    owner_user_id UUID NOT NULL,
    version INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    encrypted_key BYTEA,
    key_version TEXT,
    key_hint TEXT NOT NULL,
    key_fingerprint CHAR(64) NOT NULL,
    validated_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    retired_at TIMESTAMPTZ,
    secret_purged_at TIMESTAMPTZ,
    CONSTRAINT fk_ai_credential_versions_credential FOREIGN KEY (credential_id, owner_user_id)
        REFERENCES ai_credentials (id, owner_user_id) ON DELETE CASCADE,
    CONSTRAINT uq_ai_credential_versions_credential_version UNIQUE (credential_id, version),
    CONSTRAINT uq_ai_credential_versions_id_owner UNIQUE (id, owner_user_id),
    CONSTRAINT ck_ai_credential_versions_status CHECK (status IN ('active', 'retired', 'deleted')),
    CONSTRAINT ck_ai_credential_versions_version CHECK (version >= 1),
    CONSTRAINT ck_ai_credential_versions_secret CHECK ((encrypted_key IS NULL) = (key_version IS NULL)),
    CONSTRAINT ck_ai_credential_versions_active_has_secret CHECK (status <> 'active' OR encrypted_key IS NOT NULL),
    CONSTRAINT ck_ai_credential_versions_deleted_has_no_secret CHECK (status <> 'deleted' OR encrypted_key IS NULL),
    CONSTRAINT ck_ai_credential_versions_fingerprint CHECK (key_fingerprint ~ {HEX64}),
    CONSTRAINT ck_ai_credential_versions_hint CHECK (length(key_hint) BETWEEN 1 AND 40)
);
CREATE UNIQUE INDEX uq_ai_credential_versions_one_active ON ai_credential_versions (credential_id) WHERE status = 'active';

ALTER TABLE ai_jobs
    ADD COLUMN provider TEXT,
    ADD COLUMN model TEXT,
    ADD COLUMN credential_version_id UUID,
    ADD COLUMN profile_fingerprint CHAR(64);
ALTER TABLE ai_jobs
    ADD CONSTRAINT fk_ai_jobs_credential_version FOREIGN KEY (credential_version_id, owner_user_id)
        REFERENCES ai_credential_versions (id, owner_user_id),
    ADD CONSTRAINT ck_ai_jobs_pinned CHECK (credential_version_id IS NULL OR (provider IS NOT NULL
        AND model IS NOT NULL AND provider_profile IS NOT NULL AND profile_fingerprint IS NOT NULL)),
    ADD CONSTRAINT ck_ai_jobs_profile_fingerprint CHECK (profile_fingerprint IS NULL OR profile_fingerprint ~ {HEX64});
CREATE INDEX ix_ai_jobs_credential_version ON ai_jobs (credential_version_id) WHERE credential_version_id IS NOT NULL;
"""

FUNCTIONS_SQL = """
CREATE FUNCTION gitviz_ai_credential_versions_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.status = 'deleted' AND NEW.status <> 'deleted' THEN
        RAISE EXCEPTION USING ERRCODE = 'integrity_constraint_violation', MESSAGE = 'deleted credentials cannot be revived';
    END IF;
    IF OLD.status = 'retired' AND NEW.status = 'active' THEN
        RAISE EXCEPTION USING ERRCODE = 'integrity_constraint_violation', MESSAGE = 'retired credentials cannot be reactivated';
    END IF;
    IF OLD.encrypted_key IS NULL AND NEW.encrypted_key IS NOT NULL THEN
        RAISE EXCEPTION USING ERRCODE = 'integrity_constraint_violation', MESSAGE = 'a purged secret cannot be restored';
    END IF;
    IF NEW.encrypted_key IS NOT NULL AND NEW.encrypted_key IS DISTINCT FROM OLD.encrypted_key THEN
        RAISE EXCEPTION USING ERRCODE = 'integrity_constraint_violation', MESSAGE = 'a stored secret cannot be changed, only purged';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER ai_credentials_identity BEFORE UPDATE ON ai_credentials
    FOR EACH ROW EXECUTE FUNCTION gitviz_ai_immutable_columns('owner_user_id', 'provider', 'created_at');
CREATE TRIGGER ai_credential_versions_identity BEFORE UPDATE ON ai_credential_versions
    FOR EACH ROW EXECUTE FUNCTION gitviz_ai_immutable_columns(
        'credential_id', 'owner_user_id', 'version', 'key_hint', 'key_fingerprint', 'created_at');
CREATE TRIGGER ai_credential_versions_guard BEFORE UPDATE ON ai_credential_versions
    FOR EACH ROW EXECUTE FUNCTION gitviz_ai_credential_versions_guard();

-- 작업에 고정한 제공자·모델·key 버전·동의는 이후 바뀌지 않는다.
DROP TRIGGER ai_jobs_identity ON ai_jobs;
CREATE TRIGGER ai_jobs_identity BEFORE UPDATE ON ai_jobs
    FOR EACH ROW EXECUTE FUNCTION gitviz_ai_immutable_columns(
        'owner_user_id', 'repository_id', 'input_id', 'kind', 'fingerprint', 'generation', 'created_at',
        'provider', 'model', 'provider_profile', 'profile_fingerprint', 'credential_version_id',
        'consent_manifest_hash');
"""

DOWNGRADE_SQL = """
DROP TRIGGER ai_jobs_identity ON ai_jobs;
CREATE TRIGGER ai_jobs_identity BEFORE UPDATE ON ai_jobs
    FOR EACH ROW EXECUTE FUNCTION gitviz_ai_immutable_columns(
        'owner_user_id', 'repository_id', 'input_id', 'kind', 'fingerprint', 'generation', 'created_at');
DROP INDEX IF EXISTS ix_ai_jobs_credential_version;
ALTER TABLE ai_jobs
    DROP CONSTRAINT IF EXISTS ck_ai_jobs_profile_fingerprint,
    DROP CONSTRAINT IF EXISTS ck_ai_jobs_pinned,
    DROP CONSTRAINT IF EXISTS fk_ai_jobs_credential_version,
    DROP COLUMN IF EXISTS profile_fingerprint,
    DROP COLUMN IF EXISTS credential_version_id,
    DROP COLUMN IF EXISTS model,
    DROP COLUMN IF EXISTS provider;
DROP TABLE IF EXISTS ai_credential_versions CASCADE;
DROP TABLE IF EXISTS ai_credentials CASCADE;
DROP FUNCTION IF EXISTS gitviz_ai_credential_versions_guard();
"""


def upgrade():
    op.execute(UPGRADE_SQL)
    op.execute(FUNCTIONS_SQL)


def downgrade():
    # 이 revision이 만든 객체만 지운다. 기존 AI·분석 데이터에는 영향이 없다.
    op.execute(DOWNGRADE_SQL)
