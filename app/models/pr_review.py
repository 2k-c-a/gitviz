from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from app.core.database import Base


class PrReview(Base):
    __tablename__ = "pr_reviews"

    id = Column(Integer, primary_key=True, index=True)
    pull_request_id = Column(Integer, ForeignKey("pull_requests.id"), nullable=False)
    reviewer_login = Column(String)
    reviewer_contributor_id = Column(Integer, ForeignKey("contributors.id"), nullable=True, index=True)
    state = Column(String)  # approved/changes_requested/commented
    submitted_at = Column(DateTime(timezone=True))
