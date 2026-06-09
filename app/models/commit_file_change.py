from sqlalchemy import Column, Integer, ForeignKey
from app.core.database import Base


class CommitFileChange(Base):
    __tablename__ = "commit_file_changes"

    id = Column(Integer, primary_key=True, index=True)

    commit_id = Column(Integer, ForeignKey("commits.id"))
    file_id = Column(Integer, ForeignKey("files.id"))

    additions = Column(Integer, default=0)
    deletions = Column(Integer, default=0)