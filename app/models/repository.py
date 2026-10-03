from sqlalchemy import Column, Integer, String, Text, DateTime, BigInteger, Boolean
from sqlalchemy.sql import func
from app.core.database import Base

class Repository(Base):

    __tablename__= "repositories"

    id = Column(Integer, primary_key=True, index=True)
    url = Column(String, nullable=False)
    github_id = Column(BigInteger, unique=True, nullable=True)
    is_private = Column(Boolean, nullable=True)  # NULL = 아직 확인되지 않음
    visibility_checked_at = Column(DateTime(timezone=True))
    name = Column(String)
    platform = Column(String, default="github")
    default_branch = Column(String)
    description = Column(Text)
    max_files_per_commit = Column(Integer)
    analysis_window_months = Column(Integer, default=6)
    last_analyzed_at = Column(DateTime(timezone=True))
    last_accessed_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
