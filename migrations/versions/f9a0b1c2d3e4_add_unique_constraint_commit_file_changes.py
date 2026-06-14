"""add unique constraint to commit_file_changes

Revision ID: f9a0b1c2d3e4
Revises: 1f96eac1ec08
Create Date: 2026-06-11 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f9a0b1c2d3e4'
down_revision: Union[str, Sequence[str], None] = '1f96eac1ec08'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_unique_constraint(
        'uq_commit_file_changes_commit_id_file_id',
        'commit_file_changes',
        ['commit_id', 'file_id']
    )


def downgrade() -> None:
    op.drop_constraint(
        'uq_commit_file_changes_commit_id_file_id',
        'commit_file_changes',
        type_='unique'
    )
