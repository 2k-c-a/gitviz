"""add identity resolution columns

신원 정리(identity resolution)가 쓰는 컬럼들을 스키마에 편입한다.
운영 DB에는 수동 ALTER로 이미 들어가 있어 코드(모델·마이그레이션)에만 없던 상태였다.
새 DB에서 식별 단계가 UndefinedColumn으로 죽는 것을 막는다.

  contributors.resolution_status             'unresolved' → 'auto'
  contributors.identity_search_attempted_at  재조회 쿨다운 판단용
  contributor_aliases.resolution_rule        어느 규칙이 동일인으로 판단했는지

기존 DB에도 안전하게 재실행되도록 IF NOT EXISTS / IF EXISTS 로 작성한다.

Revision ID: d1e2f3a4b5c6
Revises: b5c6d7e8f9a0
Create Date: 2026-09-15 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


revision: str = 'd1e2f3a4b5c6'
down_revision: Union[str, Sequence[str], None] = 'b5c6d7e8f9a0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE contributors
            ADD COLUMN IF NOT EXISTS resolution_status VARCHAR DEFAULT 'unresolved'
    """)
    op.execute("""
        ALTER TABLE contributors
            ADD COLUMN IF NOT EXISTS identity_search_attempted_at TIMESTAMPTZ
    """)
    op.execute("""
        ALTER TABLE contributor_aliases
            ADD COLUMN IF NOT EXISTS resolution_rule VARCHAR
    """)
    # 기존 행(컬럼을 갓 추가한 DB)의 NULL을 기본값으로 채워 resolver 쿼리가
    # resolution_status = 'unresolved' 조건에서 대상을 놓치지 않게 한다.
    op.execute("""
        UPDATE contributors SET resolution_status = 'unresolved'
        WHERE resolution_status IS NULL
    """)
    # 미해결 대상 조회(resolution_status + 쿨다운)를 자주 돌리므로 인덱스를 둔다.
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_contributors_resolution
            ON contributors (repository_id, resolution_status)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_contributors_resolution")
    op.execute("ALTER TABLE contributor_aliases DROP COLUMN IF EXISTS resolution_rule")
    op.execute("ALTER TABLE contributors DROP COLUMN IF EXISTS identity_search_attempted_at")
    op.execute("ALTER TABLE contributors DROP COLUMN IF EXISTS resolution_status")
