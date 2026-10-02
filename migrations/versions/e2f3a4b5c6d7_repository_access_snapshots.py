"""저장소 공개 여부, 사용자 연결, 불변 분석 결과."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "e2f3a4b5c6d7"
down_revision = "d1e2f3a4b5c6"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("repositories", sa.Column("github_id", sa.BigInteger(), nullable=True))
    op.add_column("repositories", sa.Column("is_private", sa.Boolean(), nullable=True))
    op.add_column("repositories", sa.Column("visibility_checked_at", sa.DateTime(timezone=True)))
    op.create_unique_constraint("uq_repositories_github_id", "repositories", ["github_id"])
    op.create_table("repository_access",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("repository_id", sa.Integer(), sa.ForeignKey("repositories.id"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("repository_id", "user_id", name="uq_repository_access_user"))
    op.create_index("ix_repository_access_repository_id", "repository_access", ["repository_id"])
    op.create_index("ix_repository_access_user_id", "repository_access", ["user_id"])
    op.execute("""INSERT INTO repository_access (repository_id,user_id)
                  SELECT DISTINCT repository_id,triggered_by_user_id FROM analysis_jobs
                  WHERE triggered_by_user_id IS NOT NULL""")
    op.create_table("analysis_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("repository_id", sa.Integer(), sa.ForeignKey("repositories.id"), nullable=False),
        sa.Column("analysis_job_id", sa.Integer(), sa.ForeignKey("analysis_jobs.id")),
        sa.Column("target_sha", sa.String(), nullable=False),
        sa.Column("analyzed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True)),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("formula_version", sa.String(), nullable=False),
        sa.Column("parameters", postgresql.JSONB(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("payload_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()))
    op.create_index("ix_analysis_snapshots_repository_id", "analysis_snapshots", ["repository_id"])
    op.execute("""CREATE FUNCTION gitviz_snapshot_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
                  BEGIN RAISE EXCEPTION 'analysis snapshots are immutable'; END $$""")
    op.execute("""CREATE TRIGGER analysis_snapshot_immutable BEFORE UPDATE OR DELETE ON analysis_snapshots
                  FOR EACH ROW EXECUTE FUNCTION gitviz_snapshot_immutable()""")


def downgrade():
    op.drop_table("analysis_snapshots")
    op.execute("DROP FUNCTION gitviz_snapshot_immutable()")
    op.drop_table("repository_access")
    op.drop_constraint("uq_repositories_github_id", "repositories", type_="unique")
    for name in ["visibility_checked_at", "is_private", "github_id"]:
        op.drop_column("repositories", name)
