from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, UniqueConstraint
from app.core.database import Base


class Release(Base):
    __tablename__ = "releases"

    id = Column(Integer, primary_key=True, index=True)
    repository_id = Column(Integer, ForeignKey("repositories.id"), nullable=False)
    tag_name = Column(String, nullable=False)
    name = Column(String)
    base_commit_sha = Column(String)
    is_prerelease = Column(Boolean, default=False)
    published_at = Column(DateTime(timezone=True))
    release_type = Column(String, nullable=True)
    commit_count = Column(Integer, nullable=True)

    __table_args__ = (
        UniqueConstraint('repository_id', 'tag_name', name='uq_releases_repo_tag'),
    )
