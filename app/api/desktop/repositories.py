"""Read-only identity proof for explicit desktop/local project connections."""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from app.api.desktop.auth import enabled, NO_STORE
from app.core.database import get_db
from app.routers.auth import _get_session_user_id
from app.services.repository_access import canonical_github_url, verify_repository_access

router = APIRouter(prefix='/api/desktop/v1', tags=['desktop'], dependencies=[Depends(enabled)])


@router.get('/repositories/{repository_id}/identity')
def repository_identity(repository_id: int, request: Request, db=Depends(get_db)):
    user_id = _get_session_user_id(request)
    if user_id is None:
        raise HTTPException(401, '로그인이 필요합니다.')
    repository, metadata = verify_repository_access(db, repository_id, user_id)
    github_id = metadata.get('id')
    if type(github_id) is not int or github_id <= 0 or github_id != repository.github_id:
        raise HTTPException(409, '저장소 식별 정보를 확인할 수 없습니다.')
    if type(metadata.get('private')) is not bool:
        raise HTTPException(502, '저장소 공개 여부를 확인할 수 없습니다.')
    try:
        url = canonical_github_url(metadata['canonical_url'])
    except (KeyError, ValueError, TypeError):
        raise HTTPException(502, '저장소 식별 정보를 확인할 수 없습니다.') from None
    return JSONResponse({'accountId': str(user_id), 'repositoryId': repository.id,
                         'githubId': github_id, 'canonicalUrl': url,
                         'visibility': 'private' if metadata['private'] else 'public'}, headers=NO_STORE)
