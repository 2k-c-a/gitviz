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
    # GitHub(API/GraphQL changedFiles)이 보고한 전체 변경 파일 수.
    # 상세 페이지를 일부만 받았더라도 이 값은 전체 수를 유지한다 — 대규모 변경 판정의
    # 분모이므로 부분 수집 건수로 대체하면 판정이 흔들린다.
    changed_file_count = Column(Integer, default=0)
    is_large_pr = Column(Boolean, default=False)
    merged_commit_sha = Column(String, nullable=True)

    # ── PR 파일 상세 수집 완전성 ────────────────────────────────────────────
    # pr_file_coverage(상세가 1건이라도 있는 PR 비율)로는 1/100만 받은 PR도
    # "수집됨"으로 보여 완전성을 판단할 수 없다. 아래 4개로 상태를 구분한다.
    #   complete  전체 페이지 수집 성공
    #   partial   일부 페이지만 수집(중간 실패)
    #   failed    상세를 전혀 받지 못함
    #   skipped   수집 시도 안 함
    file_collection_status = Column(String, nullable=True)
    # unauthenticated / forbidden / rate_limited / page_error / None
    file_collection_reason = Column(String, nullable=True)
    files_collected_count = Column(Integer, nullable=True)   # 실제 저장한 상세 수
    files_total_count = Column(Integer, nullable=True)       # GitHub이 보고한 전체 수

    __table_args__ = (
        UniqueConstraint('repository_id', 'pr_number', name='uq_pull_requests_repo_pr_number'),
    )
