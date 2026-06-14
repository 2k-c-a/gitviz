from sqlalchemy import Column, Integer, String, ForeignKey, UniqueConstraint
from app.core.database import Base


class PrCommit(Base):
    __tablename__ = "pr_commits"

    id = Column(Integer, primary_key=True, index=True)
    pull_request_id = Column(Integer, ForeignKey("pull_requests.id"), nullable=False)
    commit_id = Column(Integer, ForeignKey("commits.id"), nullable=True)
    commit_sha = Column(String, nullable=False)

    __table_args__ = (
        UniqueConstraint('pull_request_id', 'commit_sha', name='uq_pr_commits_pr_sha'),
    )
