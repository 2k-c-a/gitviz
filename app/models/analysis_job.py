from sqlalchemy import Column, Integer, String
from app.core.database import Base

class AnalysisJob(Base):
    __tablename__ = "analysis_jobs"

    id = Column(Integer, primary_key=True, index=True)
    repository_id = Column(Integer)
    status = Column(String, default="PENDING")