"""AI 코어 스키마: PR 리비전·고정 입력·작업·결과·피드백·멱등성·outbox·예산·감사.

앱 모델을 참조하지 않는 고정 DDL이다. 사용자 테이블(users)은 별도 async Base에 있어
FK 대상을 여기서 명시한다. 기존 분석 테이블은 변경하지 않는다.
"""
from alembic import op

revision = 'a7c1e5b2d903'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None

SHA = r"'^[0-9a-f]{40}([0-9a-f]{24})?$'"
HEX64 = r"'^[0-9a-f]{64}$'"

LIMITATIONS = ("'not_selected','unsupported_language','too_large','truncated','generated_file','binary',"
               "'link_or_submodule','secret_suspected','fetch_failed','analysis_failed'")

TABLES_SQL = f"""
CREATE TABLE pr_revisions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    repository_id INTEGER NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
    pr_number INTEGER NOT NULL,
    github_pr_id BIGINT NOT NULL,
    base_sha TEXT NOT NULL,
    head_sha TEXT NOT NULL,
    merge_base_sha TEXT,
    revision_fingerprint CHAR(64) NOT NULL,
    state_at_capture TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_pr_revisions_owner_repo_fingerprint UNIQUE (owner_user_id, repository_id, revision_fingerprint),
    CONSTRAINT uq_pr_revisions_id_owner_repo UNIQUE (id, owner_user_id, repository_id),
    CONSTRAINT ck_pr_revisions_pr_number CHECK (pr_number > 0 AND github_pr_id > 0),
    CONSTRAINT ck_pr_revisions_shas CHECK (base_sha ~ {SHA} AND head_sha ~ {SHA}
        AND (merge_base_sha IS NULL OR merge_base_sha ~ {SHA})),
    CONSTRAINT ck_pr_revisions_fingerprint CHECK (revision_fingerprint ~ {HEX64}),
    CONSTRAINT ck_pr_revisions_state CHECK (state_at_capture IN ('open','closed','merged')),
    CONSTRAINT ck_pr_revisions_metadata CHECK (jsonb_typeof(metadata) = 'object')
);
CREATE INDEX ix_pr_revisions_owner_repo_created ON pr_revisions (owner_user_id, repository_id, created_at, id);

CREATE TABLE ai_inputs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_user_id UUID NOT NULL,
    repository_id INTEGER NOT NULL,
    revision_id UUID NOT NULL,
    status TEXT NOT NULL DEFAULT 'building',
    manifest JSONB,
    manifest_hash CHAR(64),
    input_hash CHAR(64),
    coverage JSONB NOT NULL,
    valid_until TIMESTAMPTZ,
    expires_at TIMESTAMPTZ NOT NULL,
    policy_version TEXT NOT NULL,
    finalized_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT fk_ai_inputs_revision FOREIGN KEY (revision_id, owner_user_id, repository_id)
        REFERENCES pr_revisions (id, owner_user_id, repository_id) ON DELETE CASCADE,
    CONSTRAINT uq_ai_inputs_id_owner_repo UNIQUE (id, owner_user_id, repository_id),
    CONSTRAINT ck_ai_inputs_status CHECK (status IN ('building','ready','failed','expired')),
    CONSTRAINT ck_ai_inputs_ready CHECK (status <> 'ready' OR (manifest IS NOT NULL AND manifest_hash IS NOT NULL
        AND input_hash IS NOT NULL AND finalized_at IS NOT NULL AND valid_until IS NOT NULL)),
    CONSTRAINT ck_ai_inputs_hashes CHECK ((manifest_hash IS NULL OR manifest_hash ~ {HEX64})
        AND (input_hash IS NULL OR input_hash ~ {HEX64})),
    CONSTRAINT ck_ai_inputs_json CHECK (jsonb_typeof(coverage) = 'object'
        AND (manifest IS NULL OR jsonb_typeof(manifest) = 'object')),
    CONSTRAINT ck_ai_inputs_policy CHECK (length(policy_version) > 0)
);
CREATE INDEX ix_ai_inputs_owner_repo_created ON ai_inputs (owner_user_id, repository_id, created_at, id);
CREATE INDEX ix_ai_inputs_status_expires ON ai_inputs (status, expires_at);

CREATE TABLE ai_input_files (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    input_id UUID NOT NULL REFERENCES ai_inputs(id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    old_path TEXT,
    side_availability TEXT NOT NULL,
    language TEXT,
    content_state TEXT NOT NULL,
    reason TEXT,
    blob_oids JSONB,
    content_hash CHAR(64),
    encrypted_payload BYTEA,
    key_version TEXT,
    line_map JSONB NOT NULL DEFAULT '{{}}'::jsonb,
    byte_count BIGINT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_ai_input_files_input_path UNIQUE (input_id, path),
    CONSTRAINT ck_ai_input_files_side CHECK (side_availability IN ('LEFT','RIGHT','BOTH')),
    CONSTRAINT ck_ai_input_files_state CHECK (content_state IN ('read','excluded','failed')),
    CONSTRAINT ck_ai_input_files_reason CHECK (
        (content_state = 'read' AND reason IS NULL AND content_hash IS NOT NULL)
        OR (content_state <> 'read' AND reason IS NOT NULL AND reason IN ({LIMITATIONS}))),
    CONSTRAINT ck_ai_input_files_hash CHECK (content_hash IS NULL OR content_hash ~ {HEX64}),
    CONSTRAINT ck_ai_input_files_payload_key CHECK (encrypted_payload IS NULL OR key_version IS NOT NULL),
    CONSTRAINT ck_ai_input_files_json CHECK (jsonb_typeof(line_map) = 'object'
        AND (blob_oids IS NULL OR jsonb_typeof(blob_oids) = 'object')),
    CONSTRAINT ck_ai_input_files_bytes CHECK (byte_count >= 0)
);

CREATE TABLE ai_jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_user_id UUID NOT NULL,
    repository_id INTEGER NOT NULL,
    input_id UUID NOT NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    fingerprint CHAR(64) NOT NULL,
    generation INTEGER NOT NULL DEFAULT 0,
    request_options JSONB NOT NULL DEFAULT '{{}}'::jsonb,
    consent_manifest_hash CHAR(64),
    provider_profile TEXT,
    prompt_version TEXT,
    schema_version TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    cancel_requested_at TIMESTAMPTZ,
    lease_token UUID,
    lease_until TIMESTAMPTZ,
    heartbeat_at TIMESTAMPTZ,
    next_attempt_at TIMESTAMPTZ,
    error_code TEXT,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    deleted_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT fk_ai_jobs_input FOREIGN KEY (input_id, owner_user_id, repository_id)
        REFERENCES ai_inputs (id, owner_user_id, repository_id) ON DELETE CASCADE,
    CONSTRAINT uq_ai_jobs_owner_kind_fingerprint_generation UNIQUE (owner_user_id, kind, fingerprint, generation),
    CONSTRAINT uq_ai_jobs_id_owner_repo UNIQUE (id, owner_user_id, repository_id),
    CONSTRAINT ck_ai_jobs_kind CHECK (kind IN ('prepare','summary','review')),
    CONSTRAINT ck_ai_jobs_status CHECK (status IN
        ('queued','running','retry_wait','cancel_requested','succeeded','failed','cancelled')),
    CONSTRAINT ck_ai_jobs_fingerprint CHECK (fingerprint ~ {HEX64}
        AND (consent_manifest_hash IS NULL OR consent_manifest_hash ~ {HEX64})),
    CONSTRAINT ck_ai_jobs_counters CHECK (generation >= 0 AND attempt_count >= 0),
    CONSTRAINT ck_ai_jobs_options CHECK (jsonb_typeof(request_options) = 'object'),
    CONSTRAINT ck_ai_jobs_generation_profile CHECK (kind = 'prepare' OR (provider_profile IS NOT NULL
        AND prompt_version IS NOT NULL AND schema_version IS NOT NULL)),
    CONSTRAINT ck_ai_jobs_running_lease CHECK (status <> 'running' OR (lease_token IS NOT NULL AND lease_until IS NOT NULL)),
    CONSTRAINT ck_ai_jobs_terminal CHECK (status NOT IN ('succeeded','failed','cancelled') OR finished_at IS NOT NULL),
    CONSTRAINT ck_ai_jobs_failed_code CHECK (status <> 'failed' OR error_code IS NOT NULL)
);
CREATE INDEX ix_ai_jobs_status_next_attempt ON ai_jobs (status, next_attempt_at);
CREATE INDEX ix_ai_jobs_owner_created ON ai_jobs (owner_user_id, created_at, id);
CREATE INDEX ix_ai_jobs_input ON ai_jobs (input_id);

CREATE TABLE ai_attempts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id UUID NOT NULL REFERENCES ai_jobs(id) ON DELETE CASCADE,
    attempt_no INTEGER NOT NULL,
    lease_token UUID NOT NULL,
    status TEXT NOT NULL DEFAULT 'started',
    provider_request_id TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    provider_reported_cost NUMERIC(18,6),
    estimated_cost NUMERIC(18,6),
    outcome_uncertain BOOLEAN NOT NULL DEFAULT false,
    error_code TEXT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    CONSTRAINT uq_ai_attempts_job_attempt UNIQUE (job_id, attempt_no),
    CONSTRAINT ck_ai_attempts_no CHECK (attempt_no >= 1),
    CONSTRAINT ck_ai_attempts_status CHECK (status IN ('started','succeeded','failed','cancelled','uncertain')),
    CONSTRAINT ck_ai_attempts_uncertain CHECK ((status = 'uncertain') = outcome_uncertain),
    CONSTRAINT ck_ai_attempts_usage CHECK (COALESCE(input_tokens,0) >= 0 AND COALESCE(output_tokens,0) >= 0
        AND COALESCE(provider_reported_cost,0) >= 0 AND COALESCE(estimated_cost,0) >= 0)
);

CREATE TABLE ai_results (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id UUID NOT NULL,
    owner_user_id UUID NOT NULL,
    repository_id INTEGER NOT NULL,
    payload JSONB NOT NULL,
    payload_hash CHAR(64) NOT NULL,
    coverage JSONB NOT NULL,
    structure_validation TEXT NOT NULL,
    human_validation TEXT NOT NULL DEFAULT 'unverified',
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT fk_ai_results_job FOREIGN KEY (job_id, owner_user_id, repository_id)
        REFERENCES ai_jobs (id, owner_user_id, repository_id) ON DELETE CASCADE,
    CONSTRAINT uq_ai_results_job UNIQUE (job_id),
    CONSTRAINT uq_ai_results_id_owner_repo UNIQUE (id, owner_user_id, repository_id),
    CONSTRAINT ck_ai_results_hash CHECK (payload_hash ~ {HEX64}),
    CONSTRAINT ck_ai_results_json CHECK (jsonb_typeof(payload) = 'object' AND jsonb_typeof(coverage) = 'object'),
    CONSTRAINT ck_ai_results_structure CHECK (structure_validation IN ('passed','failed')),
    CONSTRAINT ck_ai_results_human CHECK (human_validation IN ('unverified','valid','false_positive','uncertain'))
);

CREATE TABLE ai_findings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    result_id UUID NOT NULL REFERENCES ai_results(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL,
    fingerprint CHAR(64) NOT NULL,
    category TEXT NOT NULL,
    priority INTEGER NOT NULL,
    title TEXT NOT NULL,
    detail JSONB NOT NULL,
    source_file_id UUID NOT NULL REFERENCES ai_input_files(id) ON DELETE CASCADE,
    side TEXT NOT NULL,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_ai_findings_result_ordinal UNIQUE (result_id, ordinal),
    CONSTRAINT ck_ai_findings_ordinal CHECK (ordinal >= 1 AND priority >= 1),
    CONSTRAINT ck_ai_findings_fingerprint CHECK (fingerprint ~ {HEX64}),
    CONSTRAINT ck_ai_findings_side CHECK (side IN ('LEFT','RIGHT')),
    CONSTRAINT ck_ai_findings_lines CHECK (start_line > 0 AND start_line <= end_line),
    CONSTRAINT ck_ai_findings_detail CHECK (jsonb_typeof(detail) = 'object')
);
CREATE INDEX ix_ai_findings_source_file ON ai_findings (source_file_id);

CREATE TABLE finding_feedback (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    finding_id UUID NOT NULL REFERENCES ai_findings(id) ON DELETE CASCADE,
    author_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    verdict TEXT NOT NULL,
    note TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_finding_feedback_finding_author UNIQUE (finding_id, author_user_id),
    CONSTRAINT ck_finding_feedback_verdict CHECK (verdict IN ('valid','false_positive','uncertain')),
    CONSTRAINT ck_finding_feedback_version CHECK (version > 0),
    CONSTRAINT ck_finding_feedback_note CHECK (note IS NULL OR length(note) <= 2000)
);

CREATE TABLE request_idempotency (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    route TEXT NOT NULL,
    key TEXT NOT NULL,
    request_hash CHAR(64) NOT NULL,
    resource_id UUID,
    status TEXT NOT NULL DEFAULT 'in_progress',
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_request_idempotency_owner_route_key UNIQUE (owner_user_id, route, key),
    CONSTRAINT ck_request_idempotency_key CHECK (length(key) BETWEEN 1 AND 255 AND length(route) > 0),
    CONSTRAINT ck_request_idempotency_hash CHECK (request_hash ~ {HEX64}),
    CONSTRAINT ck_request_idempotency_status CHECK (status IN ('in_progress','completed'))
);
CREATE INDEX ix_request_idempotency_expires ON request_idempotency (expires_at);

CREATE TABLE task_outbox (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id UUID NOT NULL REFERENCES ai_jobs(id) ON DELETE CASCADE,
    dispatch_generation INTEGER NOT NULL DEFAULT 0,
    event_type TEXT NOT NULL,
    available_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    published_at TIMESTAMPTZ,
    retry_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_task_outbox_job_generation_event UNIQUE (job_id, dispatch_generation, event_type),
    CONSTRAINT ck_task_outbox_counters CHECK (dispatch_generation >= 0 AND retry_count >= 0),
    CONSTRAINT ck_task_outbox_event CHECK (length(event_type) > 0)
);
CREATE INDEX ix_task_outbox_unpublished ON task_outbox (available_at) WHERE published_at IS NULL;

CREATE TABLE usage_budgets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    period_start DATE NOT NULL,
    currency CHAR(3) NOT NULL,
    amount_limit NUMERIC(18,6) NOT NULL,
    reserved_amount NUMERIC(18,6) NOT NULL DEFAULT 0,
    spent_amount NUMERIC(18,6) NOT NULL DEFAULT 0,
    version INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_usage_budgets_owner_period_currency UNIQUE NULLS NOT DISTINCT (owner_user_id, period_start, currency),
    CONSTRAINT ck_usage_budgets_amounts CHECK (amount_limit >= 0 AND reserved_amount >= 0
        AND spent_amount >= 0 AND version >= 0),
    CONSTRAINT ck_usage_budgets_currency CHECK (currency ~ '^[A-Z]{{3}}$')
);

CREATE TABLE usage_reservations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id UUID REFERENCES ai_jobs(id) ON DELETE SET NULL,
    attempt_no INTEGER NOT NULL,
    budget_id UUID NOT NULL REFERENCES usage_budgets(id),
    reserved_amount NUMERIC(18,6) NOT NULL,
    settled_amount NUMERIC(18,6),
    status TEXT NOT NULL DEFAULT 'reserved',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_usage_reservations_job_attempt_budget UNIQUE (job_id, attempt_no, budget_id),
    CONSTRAINT ck_usage_reservations_attempt CHECK (attempt_no >= 1),
    CONSTRAINT ck_usage_reservations_amounts CHECK (reserved_amount >= 0 AND COALESCE(settled_amount,0) >= 0),
    CONSTRAINT ck_usage_reservations_status CHECK (status IN ('reserved','settled','uncertain','released')),
    CONSTRAINT ck_usage_reservations_settled CHECK (status <> 'settled' OR settled_amount IS NOT NULL)
);
CREATE INDEX ix_usage_reservations_budget ON usage_reservations (budget_id);

CREATE TABLE audit_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    actor_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    repository_id INTEGER REFERENCES repositories(id) ON DELETE SET NULL,
    action TEXT NOT NULL,
    object_type TEXT NOT NULL,
    object_id TEXT,
    request_id TEXT NOT NULL,
    details JSONB NOT NULL DEFAULT '{{}}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_audit_events_details CHECK (jsonb_typeof(details) = 'object'
        AND NOT (details ?| ARRAY['code','source','token','access_token','note','content','payload','diff']))
);
CREATE INDEX ix_audit_events_actor_created ON audit_events (actor_user_id, created_at);
"""

FUNCTIONS_SQL = """
CREATE FUNCTION gitviz_ai_forbid_update() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION USING ERRCODE = 'integrity_constraint_violation',
        MESSAGE = TG_TABLE_NAME || ' rows are immutable';
END $$;

CREATE FUNCTION gitviz_ai_immutable_columns() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE c text;
BEGIN
    FOREACH c IN ARRAY TG_ARGV LOOP
        IF to_jsonb(NEW) -> c IS DISTINCT FROM to_jsonb(OLD) -> c THEN
            RAISE EXCEPTION USING ERRCODE = 'integrity_constraint_violation',
                MESSAGE = TG_TABLE_NAME || '.' || c || ' is immutable';
        END IF;
    END LOOP;
    RETURN NEW;
END $$;

CREATE FUNCTION gitviz_ai_inputs_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.status IN ('failed', 'expired') THEN
        RAISE EXCEPTION USING ERRCODE = 'integrity_constraint_violation', MESSAGE = 'finished ai_inputs are immutable';
    END IF;
    IF OLD.status = 'building' AND NEW.status NOT IN ('building', 'ready', 'failed') THEN
        RAISE EXCEPTION USING ERRCODE = 'integrity_constraint_violation', MESSAGE = 'invalid ai_inputs transition';
    END IF;
    IF OLD.status = 'ready' THEN
        IF NEW.status NOT IN ('ready', 'expired') THEN
            RAISE EXCEPTION USING ERRCODE = 'integrity_constraint_violation', MESSAGE = 'invalid ai_inputs transition';
        END IF;
        IF (to_jsonb(NEW) - 'status' - 'updated_at') IS DISTINCT FROM (to_jsonb(OLD) - 'status' - 'updated_at') THEN
            RAISE EXCEPTION USING ERRCODE = 'integrity_constraint_violation', MESSAGE = 'ready ai_inputs are immutable';
        END IF;
    END IF;
    RETURN NEW;
END $$;

CREATE FUNCTION gitviz_ai_input_files_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE parent_status text;
BEGIN
    SELECT status INTO parent_status FROM ai_inputs WHERE id = COALESCE(NEW.input_id, OLD.input_id);
    IF TG_OP = 'INSERT' THEN
        IF parent_status IS DISTINCT FROM 'building' THEN
            RAISE EXCEPTION USING ERRCODE = 'integrity_constraint_violation',
                MESSAGE = 'files can only be added while the input is building';
        END IF;
        RETURN NEW;
    END IF;
    IF parent_status = 'building' THEN
        RETURN NEW;
    END IF;
    -- 확정된 입력은 원문 삭제(encrypted_payload/key_version 비우기)만 허용한다.
    IF (to_jsonb(NEW) - 'encrypted_payload' - 'key_version') IS DISTINCT FROM
       (to_jsonb(OLD) - 'encrypted_payload' - 'key_version')
       OR NEW.encrypted_payload IS NOT NULL THEN
        RAISE EXCEPTION USING ERRCODE = 'integrity_constraint_violation',
            MESSAGE = 'files of a finalized input are immutable';
    END IF;
    RETURN NEW;
END $$;

CREATE FUNCTION gitviz_ai_jobs_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.status IN ('succeeded', 'failed', 'cancelled') AND
       (to_jsonb(NEW) - 'updated_at' - 'deleted_at') IS DISTINCT FROM (to_jsonb(OLD) - 'updated_at' - 'deleted_at') THEN
        RAISE EXCEPTION USING ERRCODE = 'integrity_constraint_violation', MESSAGE = 'finished ai_jobs are immutable';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER pr_revisions_immutable BEFORE UPDATE ON pr_revisions
    FOR EACH ROW EXECUTE FUNCTION gitviz_ai_forbid_update();
CREATE TRIGGER ai_findings_immutable BEFORE UPDATE ON ai_findings
    FOR EACH ROW EXECUTE FUNCTION gitviz_ai_forbid_update();
CREATE TRIGGER ai_results_immutable BEFORE UPDATE ON ai_results
    FOR EACH ROW EXECUTE FUNCTION gitviz_ai_immutable_columns(
        'job_id', 'owner_user_id', 'repository_id', 'payload', 'payload_hash', 'coverage',
        'structure_validation', 'created_at');
CREATE TRIGGER ai_inputs_identity BEFORE UPDATE ON ai_inputs
    FOR EACH ROW EXECUTE FUNCTION gitviz_ai_immutable_columns(
        'owner_user_id', 'repository_id', 'revision_id', 'policy_version', 'created_at');
CREATE TRIGGER ai_inputs_guard BEFORE UPDATE ON ai_inputs
    FOR EACH ROW EXECUTE FUNCTION gitviz_ai_inputs_guard();
CREATE TRIGGER ai_input_files_guard BEFORE INSERT OR UPDATE ON ai_input_files
    FOR EACH ROW EXECUTE FUNCTION gitviz_ai_input_files_guard();
CREATE TRIGGER ai_jobs_identity BEFORE UPDATE ON ai_jobs
    FOR EACH ROW EXECUTE FUNCTION gitviz_ai_immutable_columns(
        'owner_user_id', 'repository_id', 'input_id', 'kind', 'fingerprint', 'generation', 'created_at');
CREATE TRIGGER ai_jobs_guard BEFORE UPDATE ON ai_jobs
    FOR EACH ROW EXECUTE FUNCTION gitviz_ai_jobs_guard();
"""

TABLES = ['audit_events', 'usage_reservations', 'usage_budgets', 'task_outbox', 'request_idempotency',
          'finding_feedback', 'ai_findings', 'ai_results', 'ai_attempts', 'ai_jobs', 'ai_input_files',
          'ai_inputs', 'pr_revisions']
FUNCTIONS = ['gitviz_ai_jobs_guard', 'gitviz_ai_input_files_guard', 'gitviz_ai_inputs_guard',
             'gitviz_ai_immutable_columns', 'gitviz_ai_forbid_update']


def upgrade():
    op.execute(TABLES_SQL)
    op.execute(FUNCTIONS_SQL)


def downgrade():
    # 이 revision이 만든 AI 테이블만 삭제한다. 기존 분석 데이터에는 영향이 없다.
    for table in TABLES:
        op.execute(f'DROP TABLE IF EXISTS {table} CASCADE')
    for function in FUNCTIONS:
        op.execute(f'DROP FUNCTION IF EXISTS {function}()')
