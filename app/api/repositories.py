from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.core.database import get_db
from app.models.repository import Repository
from app.models.analysis_job import AnalysisJob
from app.models.contributor import Contributor
from app.models.commit import Commit
from app.models.pull_request import PullRequest
from app.tasks.analyze_repository import analyze_repository
from app.routers.auth import _get_session_user_id

router = APIRouter()

# rate_limited 상태에서 resume_at을 이만큼(초) 넘겨도 여전히 rate_limited면
# 정상 대기가 아니라 워커가 죽어서 멈춘 것으로 간주한다(폴링/시계 오차 여유분).
_STALE_GRACE_SECONDS = 120
# resume_at 정보가 없는(구버전) 작업은 started_at 이후 이만큼 지나도 안 끝나면 멈춘 것으로 간주.
_STALE_FALLBACK_SECONDS = 6 * 3600


def _compute_staleness(job: AnalysisJob) -> dict:
    """진행 중인 작업의 실시간 대기 상태를 계산한다 (DB에 저장하지 않고 조회 시점에 계산).

    - remaining_seconds: rate_limited 상태일 때 남은 대기 시간(라이브 카운트다운용).
    - is_stale / stale_seconds: 대기가 끝났어야 할 시각을 한참 지났는데도 상태가
      안 바뀌었으면(=워커가 죽어서 멈춤) True. resume_at이 없는 구버전 데이터는
      started_at 기준 장시간 미완료로 폴백 판정한다.
    """
    result = {"remaining_seconds": None, "is_stale": False, "stale_seconds": None}
    if job.status not in ("running", "rate_limited"):
        return result

    now = datetime.now(timezone.utc)
    detail = job.progress_detail or {}
    resume_at_str = detail.get("resume_at")

    if resume_at_str:
        try:
            resume_at = datetime.fromisoformat(resume_at_str)
            overdue = (now - resume_at).total_seconds()
            if job.status == "rate_limited":
                result["remaining_seconds"] = max(0, int(-overdue))
            if overdue > _STALE_GRACE_SECONDS:
                result["is_stale"] = True
                result["stale_seconds"] = int(overdue)
            return result
        except (ValueError, TypeError):
            pass

    if job.started_at:
        elapsed = (now - job.started_at).total_seconds()
        if elapsed > _STALE_FALLBACK_SECONDS:
            result["is_stale"] = True
            result["stale_seconds"] = int(elapsed)

    return result


@router.get("/repositories")
def list_repositories(limit: int = 20, db: Session = Depends(get_db)):
    """최근 분석 요청된 레포지토리 목록 (홈 화면용)."""
    repos = (
        db.query(Repository)
        .order_by(Repository.id.desc())
        .limit(limit)
        .all()
    )
    result = []
    for repo in repos:
        latest_job = (
            db.query(AnalysisJob)
            .filter(AnalysisJob.repository_id == repo.id)
            .order_by(AnalysisJob.id.desc())
            .first()
        )
        commit_count = (
            db.query(func.count(Commit.id))
            .filter(Commit.repository_id == repo.id)
            .scalar() or 0
        )
        contributor_count = (
            db.query(func.count(Contributor.id))
            .filter(Contributor.repository_id == repo.id)
            .scalar() or 0
        )
        pr_count = (
            db.query(func.count(PullRequest.id))
            .filter(PullRequest.repository_id == repo.id)
            .scalar() or 0
        )
        staleness = _compute_staleness(latest_job) if latest_job else {
            "is_stale": False, "stale_seconds": None, "remaining_seconds": None,
        }
        result.append({
            "id": repo.id,
            "url": repo.url,
            "name": repo.name,
            "status": latest_job.status if latest_job else "unknown",
            "commit_count": commit_count,
            "contributor_count": contributor_count,
            "pr_count": pr_count,
            "last_analyzed_at": repo.last_analyzed_at,
            "created_at": repo.created_at,
            "is_stale": staleness["is_stale"],
            "stale_seconds": staleness["stale_seconds"],
            "remaining_seconds": staleness["remaining_seconds"],
        })
    return result


@router.post("/repositories")
def create_repository(github_url: str, request: Request, db: Session = Depends(get_db)):
    github_url = github_url.rstrip("/").removesuffix(".git")

    # 로그인한 사용자가 요청했으면 그 사용자의 GitHub 토큰으로 수집한다(공용 토큰 대신
    # 개인 rate limit 사용). 비로그인 요청은 기존처럼 공용 GITHUB_TOKEN으로 폴백.
    user_id = _get_session_user_id(request)
    user_id_str = str(user_id) if user_id else None

    existing = (
        db.query(Repository)
        .filter(Repository.url == github_url)
        .order_by(Repository.id)
        .first()
    )
    if existing:
        job = db.query(AnalysisJob).filter(AnalysisJob.repository_id == existing.id).first()
        if job:
            job.status = "pending"
            job.triggered_by_user_id = user_id
        else:
            db.add(AnalysisJob(
                repository_id=existing.id, status="pending", triggered_by="api",
                triggered_by_user_id=user_id,
            ))
        db.commit()
        analyze_repository.delay(existing.id, user_id=user_id_str)
        return {
            "message": "Repository re-analysis started",
            "repository_id": existing.id,
        }

    repository = Repository(
        url=github_url,
        name=github_url.rstrip("/").split("/")[-1],
        platform="github",
    )
    db.add(repository)
    db.commit()
    db.refresh(repository)

    job = AnalysisJob(
        repository_id=repository.id, status="pending", triggered_by="api",
        triggered_by_user_id=user_id,
    )
    db.add(job)
    db.commit()

    analyze_repository.delay(repository.id, user_id=user_id_str)

    return {
        "message": "Repository analysis started",
        "repository_id": repository.id,
    }


@router.get("/repositories/{repository_id}")
def get_repository(repository_id: int, db: Session = Depends(get_db)):
    repository = db.query(Repository).filter(Repository.id == repository_id).first()

    if not repository:
        return {"error": "Repository not found"}

    latest_job = (
        db.query(AnalysisJob)
        .filter(AnalysisJob.repository_id == repository.id)
        .order_by(AnalysisJob.id.desc())
        .first()
    )

    return {
        "id": repository.id,
        "url": repository.url,
        "name": repository.name,
        "status": latest_job.status if latest_job else None,
        "last_analyzed_at": repository.last_analyzed_at,
    }


@router.get("/repositories/{repository_id}/status")
def get_repository_status(repository_id: int, db: Session = Depends(get_db)):
    """프론트엔드 폴링용: 현재 상태 + 실시간 진행 상황 (업무지시서 3-1)."""
    job = (
        db.query(AnalysisJob)
        .filter(AnalysisJob.repository_id == repository_id)
        .order_by(AnalysisJob.id.desc())
        .first()
    )
    if not job:
        return {"error": "No analysis job found"}

    total_contributors = (
        db.query(func.count(Contributor.id))
        .filter(Contributor.repository_id == repository_id)
        .scalar() or 0
    )
    commit_count = (
        db.query(func.count(Commit.id))
        .filter(Commit.repository_id == repository_id)
        .scalar() or 0
    )
    pr_count = (
        db.query(func.count(PullRequest.id))
        .filter(PullRequest.repository_id == repository_id)
        .scalar() or 0
    )

    staleness = _compute_staleness(job)

    return {
        "repository_id": repository_id,
        "job_id": job.id,
        "status": job.status,
        "progress_detail": job.progress_detail or {},
        "error_message": job.error_message,
        "started_at": job.started_at,
        "completed_at": job.completed_at,
        "commit_count": commit_count,
        "pr_count": pr_count,
        "contributors_total": total_contributors,
        "remaining_seconds": staleness["remaining_seconds"],
        "is_stale": staleness["is_stale"],
        "stale_seconds": staleness["stale_seconds"],
    }
