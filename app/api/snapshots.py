"""저장된 분석 이력과 두 결과 비교 API."""
from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.orm import Session, load_only
from app.core.database import get_db
from app.models.analysis_snapshot import AnalysisSnapshot
from app.api.auth import _get_session_user_id
from app.services.repository_access import verify_repository_access
from app.services.analysis_snapshots import load_snapshot, snapshot_metadata
from app.services.snapshot_comparison import compare_snapshots

router = APIRouter(prefix='/repositories/{repository_id}/snapshots', tags=['snapshots'])


@router.get('')
def list_snapshots(repository_id: int, request: Request, response: Response,
                   limit: int = Query(20, ge=1, le=100), before_id: int | None = Query(None, ge=1),
                   db: Session = Depends(get_db)):
    verify_repository_access(db, repository_id, _get_session_user_id(request))
    query = db.query(AnalysisSnapshot).options(load_only(
        AnalysisSnapshot.id, AnalysisSnapshot.target_sha, AnalysisSnapshot.analyzed_at,
        AnalysisSnapshot.window_start, AnalysisSnapshot.window_end, AnalysisSnapshot.formula_version,
        AnalysisSnapshot.parameters, AnalysisSnapshot.payload_sha256,
    )).filter(AnalysisSnapshot.repository_id == repository_id)
    if before_id is not None:
        query = query.filter(AnalysisSnapshot.id < before_id)
    rows = query.order_by(AnalysisSnapshot.id.desc()).limit(limit + 1).all()
    response.headers['Cache-Control'] = 'private, no-store'
    return {'snapshots': [snapshot_metadata(s) for s in rows[:limit]],
            'next_before_id': rows[limit - 1].id if len(rows) > limit else None}


@router.get('/compare')
def compare_results(repository_id: int, request: Request, response: Response,
                    before: int = Query(..., ge=1), after: int = Query(..., ge=1),
                    offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=1000),
                    db: Session = Depends(get_db)):
    verify_repository_access(db, repository_id, _get_session_user_id(request))
    result = compare_snapshots(load_snapshot(db, repository_id, before),
                               load_snapshot(db, repository_id, after))
    count = len(result['changes'])
    result['changes'] = result['changes'][offset:offset+limit]
    result['pagination'] = {'total_changes': count, 'offset': offset, 'limit': limit,
                            'next_offset': offset+limit if offset+limit < count else None}
    response.headers['Cache-Control'] = 'private, no-store'
    return result
