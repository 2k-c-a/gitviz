"""Support commit reviews alongside PR revisions; retain retired feature data.

Revision ID: a9d2f4c6e8b0
Revises: f6b8d0a2c4e1
"""
from alembic import op
import sqlalchemy as sa

revision = 'a9d2f4c6e8b0'
down_revision = 'f6b8d0a2c4e1'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('pr_revisions', sa.Column('source_type', sa.Text(), nullable=False, server_default='pr'))
    op.alter_column('pr_revisions', 'pr_number', nullable=True)
    op.alter_column('pr_revisions', 'github_pr_id', nullable=True)
    op.drop_constraint('ck_pr_revisions_pr_number', 'pr_revisions')
    op.drop_constraint('ck_pr_revisions_state', 'pr_revisions')
    op.create_check_constraint('ck_pr_revisions_pr_number', 'pr_revisions',
        "(source_type = 'pr' AND pr_number IS NOT NULL AND pr_number > 0 AND github_pr_id IS NOT NULL AND github_pr_id > 0) OR (source_type = 'commit' AND pr_number IS NULL AND github_pr_id IS NULL)")
    op.create_check_constraint('ck_pr_revisions_state', 'pr_revisions',
        "(source_type = 'pr' AND state_at_capture IN ('open','closed','merged')) OR (source_type = 'commit' AND state_at_capture = 'commit')")


def downgrade():
    # Never delete commit reviews as a side effect of downgrading.
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT 1 FROM pr_revisions WHERE source_type='commit' LIMIT 1")).first():
        raise RuntimeError('Commit reviews exist; downgrade would lose review data.')
    op.drop_constraint('ck_pr_revisions_pr_number', 'pr_revisions')
    op.drop_constraint('ck_pr_revisions_state', 'pr_revisions')
    op.alter_column('pr_revisions', 'pr_number', nullable=False)
    op.alter_column('pr_revisions', 'github_pr_id', nullable=False)
    op.create_check_constraint('ck_pr_revisions_pr_number', 'pr_revisions', 'pr_number > 0 AND github_pr_id > 0')
    op.create_check_constraint('ck_pr_revisions_state', 'pr_revisions', "state_at_capture IN ('open','closed','merged')")
    op.drop_column('pr_revisions', 'source_type')
