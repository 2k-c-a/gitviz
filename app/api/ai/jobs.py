"""생성 작업 API (/api/ai/v1/jobs): 접수·조회·취소·재시도·삭제. 새 실행 로직은 없고 검증된 서비스를 연결한다.

- POST는 동의(TransferConsent)를 현재 상태와 대조하고(`consent.resolve_consent`) Idempotency-Key를 적용한다.
  새로 접수하면 202, 기존 작업(같은 key·같은 fingerprint의 진행 중·성공 결과)을 돌려주면 200이다.
- GET은 작업을 만들거나 코드를 가져오지 않는다. 본인 자료만, 현재 저장소 권한을 확인하고, 응답은 private/no-store다.
- 재시도(`/retry`)와 `force_regenerate`는 사용자의 명시적 요청이다. 자동 재시도는 worker가 정한 범위(호출 전 실패)뿐이다.
"""
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.api.ai.inputs import NO_STORE, _json, _request_id, _require_key, guard
from app.core.database import get_db
from app.schemas.ai import CreateJobRequest, ErrorCode, JobList, JobStatus, RetryJobRequest, TransferConsent
from app.services.ai import consent, idempotency, input_store, job_service, job_views
from app.services.ai.access import AIAction, AIError, authorize_ai
from app.services.ai.content_policy import POLICY_VERSION

router = APIRouter(prefix='/api/ai/v1', tags=['ai-jobs'])
MAX_BODY_BYTES = 8192
CREATE_ROUTE = 'POST /api/ai/v1/jobs'


def _request_hash(body) -> str:
    data = body.model_dump(mode='json')
    # Adding an optional pin must not invalidate pre-1.5 idempotency keys.
    if data['consent'].get('profile_fingerprint') is None:
        data['consent'].pop('profile_fingerprint', None)
    return idempotency.request_hash(data)


def authorize_read(db: Session, user_id: UUID, repository_id: int) -> None:
    """현재 저장소 접근 권한을 매번 다시 확인한다(권한을 잃은 자료는 존재를 숨긴다)."""
    authorize_ai(db, user_id, repository_id, AIAction.read, POLICY_VERSION)


def _check_size(request: Request) -> None:
    if int(request.headers.get('content-length') or 0) > MAX_BODY_BYTES:
        raise AIError(ErrorCode.INPUT_TOO_LARGE, '요청이 너무 큽니다.')


def _accept(db: Session, user_id: UUID, request: Request, *, kind: str, consent_in: TransferConsent, route: str,
            key: str, body_hash: str, force: bool) -> JSONResponse:
    """동의를 확인하고 작업을 접수한다. reviewable=false·만료·삭제된 입력과 key 불일치는 여기서 막힌다."""
    now = datetime.now(timezone.utc)
    row = input_store.load_owned(db, consent_in.input_id, user_id)                       # 남의 입력은 404
    replay = job_views.existing_for_key(db, user_id, route, key, body_hash, now)
    if replay is not None:                                                                # 재전송은 처음 접수한 작업을 그대로
        authorize_read(db, user_id, row.repository_id)
        return _json(job_views.get_job(db, replay, user_id, now=now), 200)
    pinned = consent.resolve_consent(db, user_id, consent_in, now=now)
    authorize_ai(db, user_id, row.repository_id,
                 AIAction.generate if pinned.profile.requires_api_key else AIAction.local_generate,
                 consent_in.policy_version)
    fingerprint = consent.generation_fingerprint(user_id, kind, (row.input_hash or '').strip(), pinned)
    result = job_service.create_job(
        db, owner_id=user_id, repository_id=row.repository_id, input_id=row.id, kind=kind, fingerprint=fingerprint,
        route=route, idempotency_key=key, body_hash=body_hash, pinned=pinned, force_regenerate=force, now=now)
    if result.created:
        input_store.audit(db, actor_id=user_id, repository_id=row.repository_id, action='job.created',
                          object_id=result.job.id, request_id=_request_id(request), object_type='job',
                          details={'kind': kind, 'provider': pinned.provider, 'model': pinned.model,
                                   'credential_version': pinned.credential_version, 'force': force})
        db.commit()
    return _json(job_views.get_job(db, result.job.id, user_id, now=now), 202 if result.created else 200)


@router.post('/jobs')
def create_job(body: CreateJobRequest, request: Request, user_id: UUID = Depends(guard), db: Session = Depends(get_db),
               idempotency_key: str | None = Header(None, alias='Idempotency-Key')):
    _check_size(request)
    key = _require_key(idempotency_key)
    body_hash = _request_hash(body)
    return _accept(db, user_id, request, kind=body.kind, consent_in=body.consent, route=CREATE_ROUTE, key=key,
                   body_hash=body_hash, force=body.force_regenerate)


@router.get('/jobs')
def list_jobs(repository_id: int = Query(gt=0), kind: str | None = Query(None, pattern='^(prepare|summary|review)$'),
              status: JobStatus | None = Query(None), input_id: UUID | None = Query(None),
              limit: int = Query(job_views.DEFAULT_PAGE, ge=1, le=job_views.MAX_PAGE),
              cursor: str | None = Query(None, max_length=200),
              user_id: UUID = Depends(guard), db: Session = Depends(get_db)):
    authorize_read(db, user_id, repository_id)
    jobs, next_cursor = job_views.list_jobs(db, user_id, repository_id, kind=kind,
                                            status=status.value if status else None, input_id=input_id,
                                            limit=limit, cursor=cursor)
    return _json(JobList(jobs=jobs, next_cursor=next_cursor))


@router.get('/jobs/{job_id}')
def get_job(job_id: UUID, user_id: UUID = Depends(guard), db: Session = Depends(get_db)):
    row = job_views.load_job_row(db, job_id, user_id)
    authorize_read(db, user_id, row.repository_id)
    return _json(job_views.get_job(db, job_id, user_id))


@router.post('/jobs/{job_id}/cancel')
def cancel_job(job_id: UUID, user_id: UUID = Depends(guard), db: Session = Depends(get_db)):
    """취소를 접수한다(대기 중이면 즉시, 실행 중이면 처리자가 확정). 종료된 작업에는 변화가 없다.
    본인 작업은 저장소 권한과 무관하게 취소할 수 있다."""
    status = job_service.request_cancel(db, job_id, user_id)
    return _json(job_views.get_job(db, job_id, user_id), 202 if status == 'cancel_requested' else 200)


@router.post('/jobs/{job_id}/retry')
def retry_job(job_id: UUID, body: RetryJobRequest, request: Request, user_id: UUID = Depends(guard),
              db: Session = Depends(get_db), idempotency_key: str | None = Header(None, alias='Idempotency-Key')):
    """실패·취소된 생성 작업을 새 generation으로 다시 시작한다. 비용이 다시 생기므로 동의를 다시 받는다.

    호출 결과를 알 수 없었거나(OUTCOME_UNCERTAIN) 결과를 저장하지 못한(RESULT_LOST) 작업도 이 요청으로만 다시 시작된다.
    """
    _check_size(request)
    key = _require_key(idempotency_key)
    row = job_views.load_job_row(db, job_id, user_id)
    if row.kind == 'prepare':
        raise AIError(ErrorCode.INVALID_STATE, '입력 준비는 새 입력으로 다시 시작해 주세요.')
    if row.status not in ('failed', 'cancelled'):
        raise AIError(ErrorCode.INVALID_STATE, '실패하거나 취소된 작업만 다시 시작할 수 있습니다.')
    if body.consent.input_id != row.input_id:
        raise AIError(ErrorCode.PREVIEW_CHANGED, '이 작업의 입력과 다른 입력에 동의했습니다.')
    body_hash = _request_hash(body)
    return _accept(db, user_id, request, kind=row.kind, consent_in=body.consent,
                   route=f'POST /api/ai/v1/jobs/{job_id}/retry', key=key, body_hash=body_hash, force=True)


@router.delete('/jobs/{job_id}')
def delete_job(job_id: UUID, request: Request, user_id: UUID = Depends(guard), db: Session = Depends(get_db)):
    """끝난 작업과 결과·피드백을 삭제한다. 본인 자료의 삭제는 현재 저장소 권한과 무관하다."""
    job_views.delete_job(db, job_id, user_id, request_id=_request_id(request))
    return JSONResponse({'job_id': str(job_id), 'status': 'deleted'}, headers=NO_STORE)
