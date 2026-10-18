"""같은 저장소 구성원에게 여는 읽기 전용 API (/api/ai/v1/shared): 다른 사람의 AI 결과와 그 판단·메모를 본다.

- GET만 있다. 저장소의 현재 접근 권한을 매 요청마다 다시 확인하고, 권한이 없으면 존재를 숨긴다.
- 결과는 그 결과가 속한 저장소의 권한으로 확인한다(다른 저장소의 결과 id로는 열 수 없다).
- 만들기·삭제·판단 저장은 기존 API가 그대로 작성자 본인에게만 허용한다. 입력 원문은 열지 않는다.
"""
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.ai.inputs import _json, guard
from app.api.ai.jobs import authorize_read
from app.core.database import get_db
from app.schemas.ai import SharedFindingList, SharedResultList
from app.services.ai import job_views, shared_views

router = APIRouter(prefix='/api/ai/v1/shared', tags=['ai-shared'])


@router.get('/results')
def list_results(repository_id: int = Query(gt=0), limit: int = Query(job_views.DEFAULT_PAGE, ge=1, le=job_views.MAX_PAGE),
                 cursor: str | None = Query(None, max_length=200), user_id: UUID = Depends(guard),
                 db: Session = Depends(get_db)):
    authorize_read(db, user_id, repository_id)
    items, next_cursor = shared_views.list_shared_results(db, user_id, repository_id, limit=limit, cursor=cursor)
    return _json(SharedResultList(results=items, next_cursor=next_cursor))


@router.get('/results/{result_id}')
def get_result(result_id: UUID, user_id: UUID = Depends(guard), db: Session = Depends(get_db)):
    row = shared_views.load_shared_result_row(db, result_id, datetime.now(timezone.utc))
    authorize_read(db, user_id, row.repository_id)
    return _json(shared_views.get_shared_result(db, row, user_id))


@router.get('/results/{result_id}/findings')
def list_findings(result_id: UUID, limit: int = Query(job_views.DEFAULT_PAGE, ge=1, le=job_views.MAX_PAGE),
                  cursor: str | None = Query(None, max_length=200), user_id: UUID = Depends(guard),
                  db: Session = Depends(get_db)):
    row = shared_views.load_shared_result_row(db, result_id, datetime.now(timezone.utc))
    authorize_read(db, user_id, row.repository_id)
    findings, next_cursor = shared_views.list_shared_findings(db, row, user_id, limit=limit, cursor=cursor)
    return _json(SharedFindingList(result_id=result_id, findings=findings, next_cursor=next_cursor))
