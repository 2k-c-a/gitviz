"""수동 스키마 변경 정식 편입 및 OAuth 테이블 관리."""
from migrations.schema_20261002 import reconcile, users

revision = 'f3a4b5c6d7e8'
down_revision = 'e2f3a4b5c6d7'
branch_labels = None
depends_on = None


def upgrade():
    reconcile()
    users()


def downgrade():
    # 기존 DB 수동 컬럼과 사용자 데이터는 이 revision에서 생성 여부를 알 수 없다.
    # 파괴적인 downgrade 대신 되돌리기 경계를 명시한다.
    raise RuntimeError('스키마 보완 migration은 데이터 보존을 위해 downgrade를 지원하지 않습니다.')
