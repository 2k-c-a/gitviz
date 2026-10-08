"""GitViz 이력 근거 API. 코드 지적과 별개의 불변 근거이며, 분석 snapshot의 저장된 값만 옮긴다(재계산·모델 호출·외부 전송 없음).

- 결과 작성자만 만들고 볼 수 있고, 저장소 접근 권한을 매번 다시 확인한다.
- 검증하지 못한 snapshot은 이유와 함께 `excluded`로 기록되며 근거 값이 실리지 않는다.
"""
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.api.ai.inputs import _json, _request_id, guard
from app.api.ai.jobs import authorize_read
from app.core.database import get_db
from app.schemas.ai import (AttachHistoryRequest, HistoryEvidenceList, HistoryEvidenceView, HistoryOptions, HistoryOptionView)
from app.services.ai import history_context, input_store, job_views

router = APIRouter(prefix='/api/ai/v1', tags=['ai-history'])


def _view(row) -> HistoryEvidenceView:
    return HistoryEvidenceView(evidence_id=row.id, result_id=row.result_id, snapshot_id=row.snapshot_id, status=row.status,
                               exclusion_reasons=list(row.exclusion_reasons or []), as_of=row.as_of, created_at=row.created_at,
                               payload_hash=row.payload_hash.strip(), evidence=row.payload)


@router.get('/history/snapshots')
def history_snapshots(repository_id: int = Query(gt=0), user_id: UUID = Depends(guard), db: Session = Depends(get_db)):
    """이력 근거로 고를 수 있는 분석 snapshot과 사용 가능 여부(이유). 검증하지 못한 snapshot은 eligible=false다."""
    authorize_read(db, user_id, repository_id)
    return _json(HistoryOptions(repository_id=repository_id,
                                snapshots=[HistoryOptionView(**o) for o in history_context.snapshot_options(db, repository_id, as_of=datetime.now(timezone.utc))]))


@router.get('/results/{result_id}/history')
def get_history(result_id: UUID, user_id: UUID = Depends(guard), db: Session = Depends(get_db)):
    now = datetime.now(timezone.utc)
    row = job_views.load_result_row(db, result_id, user_id, now)
    authorize_read(db, user_id, row.repository_id)
    return _json(HistoryEvidenceList(result_id=result_id, evidence=[_view(e) for e in history_context.list_evidence(db, result_id)]))


@router.post('/results/{result_id}/history')
def attach_history(result_id: UUID, body: AttachHistoryRequest, request: Request, user_id: UUID = Depends(guard), db: Session = Depends(get_db)):
    """결과에 이력 근거를 붙인다(같은 결과·snapshot은 한 번만 만들어지고 다시 요청하면 그대로 돌려준다)."""
    now = datetime.now(timezone.utc)
    row = job_views.load_result_row(db, result_id, user_id, now)
    authorize_read(db, user_id, row.repository_id)
    before = {e.id for e in history_context.list_evidence(db, result_id)}
    evidence = history_context.build_history_evidence(db, result_id, body.snapshot_id, as_of=body.as_of)
    created = evidence.id not in before
    if created:
        input_store.audit(db, actor_id=user_id, repository_id=row.repository_id, action='history.attached', object_id=result_id,
                          request_id=_request_id(request), object_type='result',
                          details={'snapshot_id': body.snapshot_id, 'status': evidence.status, 'reasons': list(evidence.exclusion_reasons or [])})
    db.commit()
    db.refresh(evidence)
    return _json(_view(evidence), 201 if created else 200)
