from uuid import UUID
from fastapi import APIRouter, Depends, Query, Path
from sqlalchemy.orm import Session
from app.api.ai.inputs import _json, _repo_ref, guard
from app.api.ai.pulls import _client
from app.core.database import get_db
from app.collectors.ai_errors import CollectionError
from app.collectors.github.ai_commit_collector import list_ai_commits, list_commit_files
from app.schemas.ai import CommitList, PullFilesView
from app.services.ai import input_builder
from app.services.ai.access import AIAction, authorize_ai
from app.services.ai.content_policy import POLICY_VERSION

router = APIRouter(prefix='/api/ai/v1', tags=['ai-commits'])


@router.get('/commits')
def get_commits(repository_id: int = Query(gt=0), limit: int = Query(30, ge=1, le=50),
                cursor: str | None = Query(None, max_length=600), user_id: UUID = Depends(guard),
                db: Session = Depends(get_db)):
    repo, _ = authorize_ai(db, user_id, repository_id, AIAction.prepare, POLICY_VERSION)
    try:
        page = list_ai_commits(_client(db, user_id), _repo_ref(repo), cursor, limit)
    except CollectionError as exc:
        raise input_builder.to_ai_error(exc) from None
    return _json(CommitList(items=page.items, next_cursor=page.next_cursor))


@router.get('/commits/{sha}/files')
def get_commit_files(sha: str = Path(pattern=r'^[0-9a-f]{40}$'), repository_id: int = Query(gt=0), limit: int = Query(100, ge=1, le=100),
                     cursor: str | None = Query(None, max_length=600), user_id: UUID = Depends(guard),
                     db: Session = Depends(get_db)):
    repo, _ = authorize_ai(db, user_id, repository_id, AIAction.prepare, POLICY_VERSION)
    try:
        page = list_commit_files(_client(db, user_id), _repo_ref(repo), sha, cursor, limit)
    except CollectionError as exc:
        raise input_builder.to_ai_error(exc) from None
    return _json(PullFilesView(files=page.files, base_sha=page.base_sha, head_sha=page.head_sha,
        pr_total_files=page.pr_total_files, inventory_coverage=page.inventory_coverage, next_cursor=page.next_cursor))
