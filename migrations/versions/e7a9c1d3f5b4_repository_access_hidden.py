"""'내 분석 프로젝트'에서 제거한 저장소를 사용자별로 숨긴다. 접근 기록과 분석 결과는 그대로 둔다. Additive only."""
from alembic import op

revision = 'e7a9c1d3f5b4'
down_revision = 'f1b3d5e7a9c2'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('ALTER TABLE repository_access ADD COLUMN hidden_at TIMESTAMP WITH TIME ZONE')


def downgrade():
    raise RuntimeError('Ignore hidden_at instead; dropping it would bring back projects users removed from their list.')
