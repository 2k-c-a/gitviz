from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func
from app.core.database import Base

class AnalysisJob(Base):
    __tablename__ = "analysis_jobs"

    id = Column(Integer, primary_key=True, index=True)
    repository_id = Column(Integer, ForeignKey("repositories.id"), index=True)
    status = Column(String, default="pending")  # pending/running/rate_limited/done/failed
    triggered_by = Column(String)
    progress_detail = Column(JSONB, default=dict)
    error_message = Column(Text)
    parameters = Column(JSONB)
    last_commit_sha = Column(String)
    last_commit_at = Column(DateTime(timezone=True))
    last_api_fetched_at = Column(DateTime(timezone=True))
    started_at = Column(DateTime(timezone=True))
    completed_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
