"""AI 이력 근거(GitViz 분석 snapshot에서 가져온 불변 요약). 결과·지적과 분리해 저장한다.

고정 DDL이며 앱 모델을 참조하지 않는다. analysis_snapshots의 데이터는 바꾸지 않고(불변 trigger 유지) 복합 FK를 위한 유일 색인만 더한다.
"""
from alembic import op

revision = 'd4f8b2c6e1a9'
down_revision = 'c3e9a1b7d550'
branch_labels = None
depends_on = None

HEX64 = r"'^[0-9a-f]{64}$'"

UPGRADE_SQL = f"""
CREATE UNIQUE INDEX uq_analysis_snapshots_id_repository ON analysis_snapshots (id, repository_id);

CREATE TABLE ai_evidence (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    result_id UUID NOT NULL,
    owner_user_id UUID NOT NULL,
    repository_id INTEGER NOT NULL,
    kind TEXT NOT NULL DEFAULT 'history',
    snapshot_id INTEGER NOT NULL,
    snapshot_sha256 CHAR(64) NOT NULL,
    status TEXT NOT NULL,
    exclusion_reasons JSONB NOT NULL DEFAULT '[]'::jsonb,
    as_of TIMESTAMPTZ NOT NULL,
    payload JSONB NOT NULL,
    payload_hash CHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT fk_ai_evidence_result FOREIGN KEY (result_id, owner_user_id, repository_id)
        REFERENCES ai_results (id, owner_user_id, repository_id) ON DELETE CASCADE,
    CONSTRAINT fk_ai_evidence_snapshot FOREIGN KEY (snapshot_id, repository_id)
        REFERENCES analysis_snapshots (id, repository_id),
    CONSTRAINT uq_ai_evidence_result_snapshot_kind UNIQUE (result_id, snapshot_id, kind),
    CONSTRAINT ck_ai_evidence_kind CHECK (kind IN ('history')),
    CONSTRAINT ck_ai_evidence_status CHECK (status IN ('ok', 'excluded')),
    CONSTRAINT ck_ai_evidence_hashes CHECK (snapshot_sha256 ~ {HEX64} AND payload_hash ~ {HEX64}),
    CONSTRAINT ck_ai_evidence_json CHECK (jsonb_typeof(payload) = 'object' AND jsonb_typeof(exclusion_reasons) = 'array'),
    CONSTRAINT ck_ai_evidence_reasons CHECK ((status = 'ok' AND jsonb_array_length(exclusion_reasons) = 0)
                                             OR (status = 'excluded' AND jsonb_array_length(exclusion_reasons) > 0))
);
CREATE INDEX ix_ai_evidence_result ON ai_evidence (result_id);
CREATE INDEX ix_ai_evidence_snapshot ON ai_evidence (snapshot_id);

CREATE TRIGGER ai_evidence_immutable BEFORE UPDATE ON ai_evidence
    FOR EACH ROW EXECUTE FUNCTION gitviz_ai_forbid_update();
"""

DOWNGRADE_SQL = """
DROP TABLE IF EXISTS ai_evidence;
DROP INDEX IF EXISTS uq_analysis_snapshots_id_repository;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
