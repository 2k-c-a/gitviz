"""create commits table

Revision ID: 621f692dc64b
Revises: 
Create Date: 2026-05-26 20:25:59.498700

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '621f692dc64b'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 기존 revision ID는 유지하고 새 설치에 빠졌던 선행 테이블만 보완한다.
    from migrations.schema_20261002 import prerequisites
    prerequisites()
    op.create_table(
        "commits",

        sa.Column(
            "id",
            sa.Integer(),
            primary_key=True
        ),

        sa.Column(
            "repository_id",
            sa.Integer(),
            nullable=True
        ),

        sa.Column(
            "commit_hash",
            sa.String(),
            unique=True
        ),

        sa.Column(
            "author_name",
            sa.String()
        ),

        sa.Column(
            "author_email",
            sa.String()
        ),

        sa.Column(
            "message",
            sa.String()
        ),

        sa.Column(
            "committed_at",
            sa.String()
        )
    )


def downgrade() -> None:
    op.drop_table("commits")
