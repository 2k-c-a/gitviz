"""내 AI 사용량(월 예산·요청 한도). 한도가 0이면 상한 없이 사용량을 기록한다."""
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.ai.inputs import _json, guard
from app.core.database import get_db
from app.services.ai import job_views

router = APIRouter(prefix='/api/ai/v1', tags=['ai-usage'])


@router.get('/usage')
def get_usage(user_id: UUID = Depends(guard), db: Session = Depends(get_db)):
    return _json(job_views.usage_summary(db, user_id))
