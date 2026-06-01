from sqlalchemy import Column, Integer, String, ForeignKey

from app.core.database import Base


class Commit(Base):

    __tablename__ = "commits"

    id = Column(Integer, primary_key=True, index=True)

    repository_id = Column(
        Integer,
        ForeignKey("repositories.id")
    )

    commit_hash = Column(String, unique=True)

    author_name = Column(String)

    author_email = Column(String)

    message = Column(String)

    committed_at = Column(String)