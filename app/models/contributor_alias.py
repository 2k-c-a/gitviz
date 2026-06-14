from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from sqlalchemy.sql import func
from app.core.database import Base


class ContributorAlias(Base):
    __tablename__ = "contributor_aliases"

    id = Column(Integer, primary_key=True, index=True)
    contributor_id = Column(Integer, ForeignKey("contributors.id"), nullable=False, index=True)
    email = Column(String)
    name = Column(String)
    source = Column(String)  # git/github/manual
    created_at = Column(DateTime(timezone=True), server_default=func.now())
