"""add pr file collection completeness columns

PR 파일 상세의 "부분 수집"을 구분하기 위한 컬럼을 추가한다.

기존 pr_file_coverage 는 "상세가 1건 이상 있는 PR / 수집된 병합 PR" 이라서
100개 중 1개만 받은 PR도 수집 완료로 집계된다. 중간 페이지 실패·인증 없음·권한
부족·한도 대기를 구분할 수 없어 누락 복구도 불가능했다.

  file_collection_status  complete / partial / failed / skipped
  file_collection_reason  unauthenticated / forbidden / rate_limited / page_error / NULL
  files_collected_count   실제 저장한 상세 수
  files_total_count       GitHub(GraphQL changedFiles)이 보고한 전체 변경 파일 수

기존 행은 상태를 알 수 없으므로 NULL로 남긴다(= 미판정). 재수집 시 채워진다.

Revision ID: a1b2c3d4e5f6
Revises: f3a4b5c6d7e8
Create Date: 2026-10-04 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = 'f3a4b5c6d7e8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE pull_requests
            ADD COLUMN IF NOT EXISTS file_collection_status VARCHAR,
            ADD COLUMN IF NOT EXISTS file_collection_reason VARCHAR,
            ADD COLUMN IF NOT EXISTS files_collected_count INTEGER,
            ADD COLUMN IF NOT EXISTS files_total_count INTEGER
    """)
    # 부분/실패 PR만 재수집 대상으로 추려야 하므로 저장소별 상태 조회에 인덱스를 둔다.
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_pull_requests_file_collection
            ON pull_requests (repository_id, file_collection_status)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_pull_requests_file_collection")
    op.execute("""
        ALTER TABLE pull_requests
            DROP COLUMN IF EXISTS files_total_count,
            DROP COLUMN IF EXISTS files_collected_count,
            DROP COLUMN IF EXISTS file_collection_reason,
            DROP COLUMN IF EXISTS file_collection_status
    """)
