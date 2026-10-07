"""분석 완료 시 생성하는 불변 결과. API 조회 중에는 갱신하지 않는다."""
from sqlalchemy import Column, Index, Integer, String, DateTime, ForeignKey, JSON, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.sql import func
from app.core.database import Base


class AnalysisSnapshot(Base):
    __tablename__ = "analysis_snapshots"
    id = Column(Integer, primary_key=True)
    repository_id = Column(Integer, ForeignKey("repositories.id"), nullable=False, index=True)
    analysis_job_id = Column(Integer, ForeignKey("analysis_jobs.id"), nullable=True)
    target_sha = Column(String, nullable=False)
    analyzed_at = Column(DateTime(timezone=True), nullable=False)
    window_start = Column(DateTime(timezone=True), nullable=True)
    window_end = Column(DateTime(timezone=True), nullable=False)
    formula_version = Column(String, nullable=False)
    parameters = Column(JSON().with_variant(JSONB, "postgresql"), nullable=False)
    payload = Column(JSON().with_variant(JSONB, "postgresql"), nullable=False)
    payload_sha256 = Column(String(64), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    # AI 이력 근거가 (snapshot, 저장소) 복합 FK로 같은 저장소의 결과만 가리키게 한다(migration d4f8b2c6e1a9).
    __table_args__ = (Index("uq_analysis_snapshots_id_repository", "id", "repository_id", unique=True),)


class RepositoryAccess(Base):
    __tablename__ = "repository_access"
    id = Column(Integer, primary_key=True)
    repository_id = Column(Integer, ForeignKey("repositories.id"), nullable=False, index=True)
    # users는 별도 async Base. 저장된 연결은 목록용이고 조회 권한은 GitHub에서 재검증한다.
    user_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (UniqueConstraint("repository_id", "user_id", name="uq_repository_access_user"),)
