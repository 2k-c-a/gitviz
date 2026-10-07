"""지적 피드백 API. 원본 결과는 바꾸지 않고, 결과 작성자만 `expected_version`이 맞을 때 남길 수 있다."""
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api.ai.inputs import _json, _request_id, guard
from app.api.ai.jobs import authorize_read
from app.core.database import get_db
from app.schemas.ai import FeedbackRequest
from app.services.ai import feedback_service

router = APIRouter(prefix='/api/ai/v1', tags=['ai-feedback'])


@router.put('/findings/{finding_id}/feedback')
def put_feedback(finding_id: UUID, body: FeedbackRequest, request: Request, user_id: UUID = Depends(guard),
                 db: Session = Depends(get_db)):
    info = feedback_service.load_finding_owner(db, finding_id, user_id, datetime.now(timezone.utc))
    authorize_read(db, user_id, info.repository_id)
    view = feedback_service.put_feedback(db, finding_id, user_id, body.verdict, body.note, body.expected_version,
                                         request_id=_request_id(request))
    return _json(view)
