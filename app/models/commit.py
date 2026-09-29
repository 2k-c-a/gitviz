from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, ForeignKey, UniqueConstraint

from app.core.database import Base


class Commit(Base):

    __tablename__ = "commits"

    id = Column(Integer, primary_key=True, index=True)

    repository_id = Column(
        Integer,
        ForeignKey("repositories.id"),
        index=True
    )

    contributor_id = Column(Integer, ForeignKey("contributors.id"), nullable=True, index=True)

    sha = Column(String)

    author_name = Column(String)

    author_email = Column(String)

    message = Column(String)

    commit_type = Column(String)  # feature/bugfix/refactor/docs/chore/other
    commit_type_source = Column(String)  # conventional_commits/keyword/default
    commit_type_confidence = Column(Float)

    changed_file_count = Column(Integer)
    is_large_commit = Column(Boolean, default=False)
    # 기본 브랜치에서 도달 가능한 커밋인지. NULL = 아직 판정 전(컬럼 추가 이전 수집분) → 집계에 포함
    on_default_branch = Column(Boolean, nullable=True)

    committed_at = Column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint('repository_id', 'sha', name='uq_commits_repo_hash'),
    )
