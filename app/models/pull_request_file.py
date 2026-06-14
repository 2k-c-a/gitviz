from sqlalchemy import Column, Integer, String, ForeignKey, UniqueConstraint
from app.core.database import Base


class PullRequestFile(Base):
    __tablename__ = "pull_request_files"

    id = Column(Integer, primary_key=True, index=True)
    pull_request_id = Column(Integer, ForeignKey("pull_requests.id"), nullable=False)
    file_id = Column(Integer, ForeignKey("files.id"), nullable=True)
    filename = Column(String, nullable=False)
    additions = Column(Integer, default=0)
    deletions = Column(Integer, default=0)
    change_type = Column(String)

    __table_args__ = (
        UniqueConstraint('pull_request_id', 'filename', name='uq_pull_request_files_pr_filename'),
    )
