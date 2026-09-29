from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.sql import func
from app.core.database import Base


class Contributor(Base):

    __tablename__ = "contributors"

    id = Column(Integer, primary_key=True, index=True)
    repository_id = Column(Integer, ForeignKey("repositories.id"), index=True)
    primary_email = Column(String)
    display_name = Column(String)
    github_login = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # 신원 정리(identity resolution) 상태.
    # 'unresolved'(기본) → 'auto'(API/규칙으로 자동 식별). resolver가 갱신한다.
    resolution_status = Column(String, nullable=True, server_default="unresolved")
    # 마지막으로 GitHub 조회를 시도한 시각. 재실행 시 이미 시도한 대상을
    # 재조회하지 않도록 쿨다운(기본 30일) 판단에 쓴다.
    identity_search_attempted_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint('repository_id', 'primary_email', name='uq_contributors_repo_email'),
    )
