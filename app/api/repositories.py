from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.services.github_service import GitHubService

from app.core.database import get_db
from app.models.repository import Repository
from app.models.analysis_job import AnalysisJob
from app.tasks.analyze_repository import analyze_repository

router = APIRouter()

github_service = GitHubService()

@router.post("/repositories")
def create_repository(github_url: str, db: Session = Depends(get_db)):

    repository = Repository(
        github_url=github_url,
        status="PENDING"
    )

    db.add(repository)
    db.commit()
    db.refresh(repository)

    job = AnalysisJob(
        repository_id=repository.id,
        status="PENDING"
    )

    db.add(job)
    db.commit()

    analyze_repository.delay(repository.id)

    return {
        "message": "Repository analysis started",
        "repository_id": repository.id
    }
    
@router.get("/repositories/{repository_id}")
def get_repository(repository_id: int, db: Session = Depends(get_db)):
    
    repository = db.query(Repository).filter(
        Repository.id == repository_id
    ).first()
    
    if not repository:
        return {"error": "Repository not found"}
    
    return{
        "id": repository.id,
        "github_url": repository.github_url,
        "status": repository.status
    }

@router.get("/test-commits")
async def test_commits():

    commits = await github_service.get_commits(
        owner="torvalds",
        repo="linux"
    )
    return commits[:5]