from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, UniqueConstraint
from app.core.database import Base


class PullRequest(Base):
    __tablename__ = "pull_requests"

    id = Column(Integer, primary_key=True, index=True)
    repository_id = Column(Integer, ForeignKey("repositories.id"), nullable=False, index=True)
    pr_number = Column(Integer, nullable=False)
    title = Column(String)
    author_contributor_id = Column(Integer, ForeignKey("contributors.id"), nullable=True, index=True)
    author_login = Column(String)  # GitHub API raw value
    state = Column(String, default="merged")  # open/merged/closed
    base_branch = Column(String)
    head_branch = Column(String)
    opened_at = Column(DateTime(timezone=True))
    merged_at = Column(DateTime(timezone=True))
    closed_at = Column(DateTime(timezone=True))
    review_duration_hours = Column(Integer)
    changed_file_count = Column(Integer, default=0)
    is_large_pr = Column(Boolean, default=False)
    merged_commit_sha = Column(String, nullable=True)

    __table_args__ = (
        UniqueConstraint('repository_id', 'pr_number', name='uq_pull_requests_repo_pr_number'),
    )
