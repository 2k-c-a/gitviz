from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.repository import Repository
from app.models.analysis_job import AnalysisJob
from app.tasks.analyze_repository import analyze_repository

router = APIRouter()


@router.post("/repositories")
def create_repository(github_url: str, db: Session = Depends(get_db)):
    github_url = github_url.rstrip("/").removesuffix(".git")

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
        else:
            db.add(AnalysisJob(repository_id=existing.id, status="pending", triggered_by="api"))
        db.commit()
        analyze_repository.delay(existing.id)
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

    job = AnalysisJob(repository_id=repository.id, status="pending", triggered_by="api")
    db.add(job)
    db.commit()

    analyze_repository.delay(repository.id)

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

    return {
        "repository_id": repository_id,
        "job_id": job.id,
        "status": job.status,
        "progress_detail": job.progress_detail or {},
        "error_message": job.error_message,
        "started_at": job.started_at,
        "completed_at": job.completed_at,
    }
