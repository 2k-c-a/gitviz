"""계산용 함수와 HTTP 조회 경로를 분리: HTTP는 저장된 스냅샷만 반환한다."""
from fastapi import HTTPException
from fastapi.routing import APIRoute
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from app.core.database import SessionLocal
from app.services.analysis_snapshots import load_snapshot, serve_snapshot
from app.services.repository_access import verify_repository_access


class SnapshotAnalyticsRoute(APIRoute):
    def get_route_handler(self):
        async def handler(request):
            def read():
                from app.api.auth import _get_session_user_id
                try:
                    rid = int(request.path_params["repository_id"])
                    sid = int(request.query_params["snapshot_id"]) if "snapshot_id" in request.query_params else None
                    if rid <= 0 or (sid is not None and sid <= 0):
                        raise ValueError()
                except (ValueError, TypeError):
                    raise HTTPException(422,"저장소/스냅샷 ID가 유효하지 않습니다.")
                with SessionLocal() as db:
                    verify_repository_access(db,rid,_get_session_user_id(request))
                    snapshot = load_snapshot(db,rid,sid)
                    route = request.url.path.split("/analytics/",1)[1]
                    try:
                        result = serve_snapshot(snapshot,route,request.query_params)
                    except ValueError:
                        raise HTTPException(422,"유효하지 않은 분석 결과 ID")
                    return JSONResponse(result,headers={"Cache-Control":"private, no-store"})
            return await run_in_threadpool(read)
        return handler
