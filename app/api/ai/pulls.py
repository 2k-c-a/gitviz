"""PR·파일 선택 화면용 조회 API (/api/ai/v1/pulls). AI 제공자를 호출하지 않고 DB에 아무것도 저장하지 않는다.

GitHub에서 현재 PR 목록과 한 PR의 전체 파일 목록을 가져와 보여 줄 뿐이며, 여기서 얻은 값은 고정 입력이 아니다.
입력을 만들 때(`POST /inputs`) 화면이 본 base/head SHA를 `expected_*`로 보내 서버가 다시 확인한다.
"""
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.ai.inputs import _json, _repo_ref, _token, guard
from app.collectors.ai_errors import CollectionError
from app.collectors.github.ai_pr_collector import MAX_FILE_PAGE, MAX_PR_PAGE, list_ai_prs, list_pr_files
from app.collectors.github.github_client import GitHubClient
from app.core.database import get_db
from app.schemas.ai import PullFilesView, PullList
from app.services.ai import input_builder
from app.services.ai.access import AIAction, authorize_ai
from app.services.ai.content_policy import POLICY_VERSION

router = APIRouter(prefix='/api/ai/v1', tags=['ai-pulls'])


def _client(db: Session, user_id: UUID) -> GitHubClient:
    return GitHubClient(token=_token(db, user_id), timeout=15, max_retries=2, max_wait_seconds=10)


@router.get('/pulls')
def get_pulls(repository_id: int = Query(gt=0), state: Literal['open', 'closed', 'merged'] = Query('open'),
              limit: int = Query(30, ge=1, le=MAX_PR_PAGE), cursor: str | None = Query(None, max_length=600),
              user_id: UUID = Depends(guard), db: Session = Depends(get_db)):
    repo, _ = authorize_ai(db, user_id, repository_id, AIAction.prepare, POLICY_VERSION)
    try:
        page = list_ai_prs(_client(db, user_id), _repo_ref(repo), state, cursor, limit)
    except CollectionError as exc:
        raise input_builder.to_ai_error(exc) from None
    return _json(PullList(items=page.items, next_cursor=page.next_cursor))


@router.get('/pulls/{pr_number}/files')
def get_pull_files(pr_number: int, repository_id: int = Query(gt=0), limit: int = Query(MAX_FILE_PAGE, ge=1, le=MAX_FILE_PAGE),
                   cursor: str | None = Query(None, max_length=600), user_id: UUID = Depends(guard),
                   db: Session = Depends(get_db)):
    repo, _ = authorize_ai(db, user_id, repository_id, AIAction.prepare, POLICY_VERSION)
    try:
        page = list_pr_files(_client(db, user_id), _repo_ref(repo), pr_number, cursor, limit)
    except CollectionError as exc:
        raise input_builder.to_ai_error(exc) from None
    return _json(PullFilesView(files=page.files, base_sha=page.base_sha, head_sha=page.head_sha,
                               pr_total_files=page.pr_total_files, inventory_coverage=page.inventory_coverage,
                               next_cursor=page.next_cursor))
