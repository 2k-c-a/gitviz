"""입력 준비·전송 미리보기 API (/api/ai/v1/inputs). AI 제공자를 호출하지 않는다.

POST는 PR 리비전을 고정하고 building 입력과 prepare 작업을 접수한다(202). 코드 취득은 worker가 한다.
GET은 작업이나 코드 취득을 시작하지 않으며 모든 응답은 private, no-store다.
"""
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.ai.dependencies import require_ai_user, require_csrf
from app.collectors.ai_errors import CollectionError
from app.collectors.github.ai_pr_collector import RepoRef, capture_revision
from app.collectors.github.github_client import GitHubClient
from app.core.ai_settings import get_ai_settings
from app.core.database import get_db
from app.schemas.ai import ErrorCode, InputStatus, PrepareInputRequest
from app.services.ai import idempotency, input_builder, input_store
from app.services.ai.access import AIAction, AIError, authorize_ai
from app.services.ai.content_policy import POLICY_VERSION
from app.services.repository_access import canonical_github_url, user_github_token

router = APIRouter(prefix='/api/ai/v1', tags=['ai-inputs'])
ROUTE = 'POST /api/ai/v1/inputs'
MAX_BODY_BYTES = 256_000
NO_STORE = {'Cache-Control': 'private, no-store'}


def guard(request: Request) -> UUID:
    """AI 기능·로그인·(변경 요청이면) CSRF를 확인하고 사용자 ID를 돌려준다."""
    user_id = require_ai_user(request)
    require_csrf(request)
    return user_id


def _json(model, status: int = 200) -> JSONResponse:
    return JSONResponse(model.model_dump(mode='json'), status_code=status, headers=NO_STORE)


def _request_id(request: Request) -> str:
    return (request.headers.get('x-request-id') or '')[:64] or '-'


def _repo_ref(repo) -> RepoRef:
    owner, name = canonical_github_url(repo.url).split('github.com/')[1].split('/')
    return RepoRef(repository_id=repo.id, github_id=repo.github_id, owner=owner, name=name)


def _token(db: Session, user_id: UUID):
    try:
        return user_github_token(db, user_id)
    except HTTPException:
        raise AIError(ErrorCode.REAUTH_REQUIRED, 'GitHub 인증을 갱신해 주세요.') from None


def _require_key(value: str | None) -> str:
    if value is None:
        raise AIError(ErrorCode.INVALID_STATE, 'Idempotency-Key 헤더가 필요합니다.')
    return idempotency.validate_key(value)


def _body_hash(body: PrepareInputRequest) -> str:
    data = body.model_dump(mode='json')
    data['selected_paths'] = sorted(data['selected_paths'])      # 선택 순서가 달라도 같은 요청이다
    return idempotency.request_hash(data)


@router.post('/inputs')
def create_input(body: PrepareInputRequest, request: Request, user_id: UUID = Depends(guard),
                 db: Session = Depends(get_db), idempotency_key: str | None = Header(None, alias='Idempotency-Key')):
    if int(request.headers.get('content-length') or 0) > MAX_BODY_BYTES:
        raise AIError(ErrorCode.INPUT_TOO_LARGE, '요청이 너무 큽니다.')
    key = _require_key(idempotency_key)
    settings = get_ai_settings()
    if len(body.selected_paths) > settings.max_files:
        raise AIError(ErrorCode.INPUT_TOO_LARGE, f'한 번에 {settings.max_files}개까지 선택할 수 있습니다.')
    now = datetime.now(timezone.utc)
    body_hash = _body_hash(body)

    repo, _ = authorize_ai(db, user_id, body.repository_id, AIAction.prepare, POLICY_VERSION)
    # 같은 key의 재전송은 PR이 그 사이 바뀌었어도 처음 만든 입력을 그대로 돌려준다.
    existing = _existing_for_key(db, user_id, key, body_hash, now)
    if existing is not None:
        return _preview_response(db, existing, user_id, now)

    ref = _repo_ref(repo)
    client = GitHubClient(token=_token(db, user_id), timeout=15, max_retries=2, max_wait_seconds=10)
    try:
        captured = capture_revision(client, ref, body.pr_number, body.expected_base_sha, body.expected_head_sha)
    except CollectionError as exc:
        raise input_builder.to_ai_error(exc) from None
    result = input_store.create_input_and_job(
        db, owner_id=user_id, repository_id=repo.id, captured=captured, selected_paths=body.selected_paths,
        route=ROUTE, idempotency_key=key, body_hash=body_hash, now=now)
    if result.created:
        input_store.audit(db, actor_id=user_id, repository_id=repo.id, action='input.prepare_requested',
                          object_id=result.job.input_id, request_id=_request_id(request),
                          details={'selected_files': len(body.selected_paths), 'pr_number': body.pr_number})
        db.commit()
    return _preview_response(db, result.job.input_id, user_id, now)


def _existing_for_key(db: Session, user_id: UUID, key: str, body_hash: str, now: datetime) -> UUID | None:
    row = db.execute(text("""SELECT request_hash, resource_id FROM request_idempotency
                             WHERE owner_user_id = :o AND route = :r AND key = :k AND expires_at > :now"""),
                     {'o': user_id, 'r': ROUTE, 'k': key, 'now': now}).one_or_none()
    if row is None:
        return None
    if row.request_hash != body_hash:
        raise AIError(ErrorCode.IDEMPOTENCY_CONFLICT, '같은 Idempotency-Key로 다른 요청을 보낼 수 없습니다.')
    if row.resource_id is None:
        return None
    return db.execute(text("SELECT input_id FROM ai_jobs WHERE id = :j AND owner_user_id = :o AND deleted_at IS NULL"),
                      {'j': row.resource_id, 'o': user_id}).scalar()


def _preview_response(db: Session, input_id: UUID, user_id: UUID, now: datetime) -> JSONResponse:
    preview = input_store.load_preview(db, input_id, user_id, now=now)
    return _json(preview, 202 if preview.status is InputStatus.building else 200)


@router.get('/inputs/{input_id}')
def get_input(input_id: UUID, user_id: UUID = Depends(guard), db: Session = Depends(get_db),
              profile_id: str | None = Query(None, max_length=64)):
    row = input_store.load_owned(db, input_id, user_id)
    authorize_ai(db, user_id, row.repository_id, AIAction.read, POLICY_VERSION)   # 접근 권한은 매번 다시 확인
    return _json(input_store.load_preview(db, input_id, user_id, profile_id=profile_id))


@router.get('/inputs/{input_id}/files/{file_id}')
def get_input_file(input_id: UUID, file_id: UUID, request: Request,
                   side: str | None = Query(None), start_line: int | None = Query(None),
                   end_line: int | None = Query(None), user_id: UUID = Depends(guard),
                   db: Session = Depends(get_db)):
    row = input_store.load_owned(db, input_id, user_id)
    authorize_ai(db, user_id, row.repository_id, AIAction.read, POLICY_VERSION)
    content = input_store.read_file_content(db, input_id, file_id, user_id, side=side, start_line=start_line,
                                            end_line=end_line, request_id=_request_id(request))
    return _json(content)


@router.delete('/inputs/{input_id}')
def delete_input(input_id: UUID, request: Request, user_id: UUID = Depends(guard), db: Session = Depends(get_db)):
    """원문 삭제와 연결 작업 취소를 접수한다. 작성자 본인의 자료 삭제는 현재 저장소 접근 권한과 무관하다."""
    status = input_store.delete_input(db, input_id, user_id, request_id=_request_id(request))
    return JSONResponse({'input_id': str(input_id), 'status': status}, status_code=202, headers=NO_STORE)
