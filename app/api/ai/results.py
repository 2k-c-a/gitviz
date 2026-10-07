"""결과·지적 조회 API. 결과는 불변이며 GET은 아무것도 만들지 않는다. 본인 자료·현재 저장소 권한을 매번 확인한다."""
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.ai.inputs import _json, guard
from app.api.ai.jobs import authorize_read
from app.core.database import get_db
from app.schemas.ai import FindingList
from app.services.ai import job_views

router = APIRouter(prefix='/api/ai/v1', tags=['ai-results'])


@router.get('/results/{result_id}')
def get_result(result_id: UUID, user_id: UUID = Depends(guard), db: Session = Depends(get_db)):
    now = datetime.now(timezone.utc)
    row = job_views.load_result_row(db, result_id, user_id, now)
    authorize_read(db, user_id, row.repository_id)
    return _json(job_views.get_result(db, result_id, user_id, now=now))


@router.get('/results/{result_id}/findings')
def list_findings(result_id: UUID, limit: int = Query(job_views.DEFAULT_PAGE, ge=1, le=job_views.MAX_PAGE),
                  cursor: str | None = Query(None, max_length=200), user_id: UUID = Depends(guard),
                  db: Session = Depends(get_db)):
    now = datetime.now(timezone.utc)
    row = job_views.load_result_row(db, result_id, user_id, now)
    authorize_read(db, user_id, row.repository_id)
    findings, next_cursor = job_views.list_findings(db, result_id, user_id, limit=limit, cursor=cursor, now=now)
    return _json(FindingList(result_id=result_id, findings=findings, next_cursor=next_cursor))
