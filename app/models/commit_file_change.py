from sqlalchemy import Column, Integer, String, ForeignKey, UniqueConstraint
from app.core.database import Base


class CommitFileChange(Base):
    __tablename__ = "commit_file_changes"

    id = Column(Integer, primary_key=True, index=True)

    commit_id = Column(Integer, ForeignKey("commits.id"), index=True)
    file_id = Column(Integer, ForeignKey("files.id"), index=True)

    change_type = Column(String)  # add/modify/delete/rename
    additions = Column(Integer, default=0)
    deletions = Column(Integer, default=0)

    __table_args__ = (
        UniqueConstraint('commit_id', 'file_id', name='uq_commit_file_changes_commit_id_file_id'),
    )