from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.sql import func
from app.core.database import Base

class AnalysisJob(Base):
    __tablename__ = "analysis_jobs"

    id = Column(Integer, primary_key=True, index=True)
    repository_id = Column(Integer, ForeignKey("repositories.id"), index=True)
    status = Column(String, default="pending")  # pending/running/rate_limited/done/failed
    triggered_by = Column(String)
    # 로그인한 사용자가 요청한 경우 users.id — 있으면 그 사용자의 GitHub 토큰으로 수집한다
    # (공용 GITHUB_TOKEN 대신 개인 rate limit을 쓰게 하기 위함). users는 별도 async Base라
    # FK 제약은 걸지 않고 값만 저장 — analyze_repository.py에서 raw SQL로 조회한다.
    triggered_by_user_id = Column(UUID(as_uuid=True), nullable=True)
    progress_detail = Column(JSONB, default=dict)
    error_message = Column(Text)
    parameters = Column(JSONB)
    last_commit_sha = Column(String)
    last_commit_at = Column(DateTime(timezone=True))
    last_api_fetched_at = Column(DateTime(timezone=True))
    started_at = Column(DateTime(timezone=True))
    completed_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
