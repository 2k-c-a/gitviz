"""팀 API(S13): 팀·구성원·연결 저장소·명시적 공유·보고서. 계약은 docs/AI_TEAM_ACCESS.md.

구성원이 아니면 팀의 존재를 숨기고(404), 구성원이지만 역할이 부족하면 PERMISSION_DENIED(403)다.
보이는 범위는 팀 역할 ∩ 현재 저장소 접근 ∩ 현재 유효한 공유이며 요청마다 다시 확인한다. 본문·메모는 감사 기록에 넣지 않는다.
"""
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.api.ai.inputs import NO_STORE, _json, _request_id, guard
from app.core.database import get_db
from app.schemas.ai import ErrorCode
from app.schemas.ai_team import (InviteRequest, MemberList, MemberView, ReportCreate, ReportList, ReportListItem, ReportView, RepositoryAdd, RolePatch, ShareContent,
                                 ShareCreate, ShareList, ShareView, TeamCreate, TeamList, TeamPatch, TeamRepositoryList, TeamRepositoryView, TeamView)
from app.services.ai import team_report_service as reports
from app.services.ai import team_service as svc
from app.services.ai.access import AIError

router = APIRouter(prefix='/api/ai/v1', tags=['ai-teams'])
MAX_BODY_BYTES = 16_000


def _check_size(request: Request) -> None:
    if int(request.headers.get('content-length') or 0) > MAX_BODY_BYTES:
        raise AIError(ErrorCode.INPUT_TOO_LARGE, '요청이 너무 큽니다.')


@router.post('/teams')
def create_team(body: TeamCreate, request: Request, user_id: UUID = Depends(guard), db: Session = Depends(get_db)):
    _check_size(request)
    return _json(TeamView(**svc.create_team(db, user_id, body, request_id=_request_id(request))), 201)


@router.get('/teams')
def list_teams(user_id: UUID = Depends(guard), db: Session = Depends(get_db)):
    return _json(TeamList(teams=[TeamView(**t) for t in svc.list_teams(db, user_id)]))


@router.get('/teams/{team_id}')
def get_team(team_id: UUID, user_id: UUID = Depends(guard), db: Session = Depends(get_db)):
    return _json(TeamView(**svc.get_team(db, user_id, team_id)))


@router.patch('/teams/{team_id}')
def rename_team(team_id: UUID, body: TeamPatch, request: Request, user_id: UUID = Depends(guard), db: Session = Depends(get_db)):
    _check_size(request)
    return _json(TeamView(**svc.rename_team(db, user_id, team_id, body.name, request_id=_request_id(request))))


@router.delete('/teams/{team_id}')
def delete_team(team_id: UUID, request: Request, user_id: UUID = Depends(guard), db: Session = Depends(get_db)):
    svc.delete_team(db, user_id, team_id, request_id=_request_id(request))
    return JSONResponse({'team_id': str(team_id), 'status': 'deleted'}, headers=NO_STORE)


# ── 구성원·초대 ──
@router.get('/teams/{team_id}/members')
def list_members(team_id: UUID, user_id: UUID = Depends(guard), db: Session = Depends(get_db)):
    return _json(MemberList(members=[MemberView(**m) for m in svc.list_members(db, user_id, team_id)]))


@router.post('/teams/{team_id}/members')
def invite(team_id: UUID, body: InviteRequest, request: Request, user_id: UUID = Depends(guard), db: Session = Depends(get_db)):
    _check_size(request)
    return _json(MemberView(**svc.invite(db, user_id, team_id, body, request_id=_request_id(request))), 201)


@router.patch('/teams/{team_id}/members/{member_id}')
def change_role(team_id: UUID, member_id: UUID, body: RolePatch, request: Request, user_id: UUID = Depends(guard), db: Session = Depends(get_db)):
    _check_size(request)
    return _json(MemberView(**svc.change_role(db, user_id, team_id, member_id, body.role, request_id=_request_id(request))))


@router.delete('/teams/{team_id}/members/{member_id}')
def remove_member(team_id: UUID, member_id: UUID, request: Request, user_id: UUID = Depends(guard), db: Session = Depends(get_db)):
    """관리자가 구성원을 제거한다. member_id가 본인이면 탈퇴(또는 초대 거절)다."""
    svc.remove_member(db, user_id, team_id, member_id, request_id=_request_id(request))
    return JSONResponse({'team_id': str(team_id), 'user_id': str(member_id), 'status': 'removed'}, headers=NO_STORE)


@router.post('/teams/{team_id}/invitation/accept')
def accept_invitation(team_id: UUID, request: Request, user_id: UUID = Depends(guard), db: Session = Depends(get_db)):
    return _json(TeamView(**svc.accept_invitation(db, user_id, team_id, request_id=_request_id(request))))


# ── 저장소 ──
@router.get('/teams/{team_id}/repositories')
def list_repositories(team_id: UUID, user_id: UUID = Depends(guard), db: Session = Depends(get_db)):
    return _json(TeamRepositoryList(repositories=[TeamRepositoryView(**r) for r in svc.list_repositories(db, user_id, team_id)]))


@router.post('/teams/{team_id}/repositories')
def add_repository(team_id: UUID, body: RepositoryAdd, request: Request, user_id: UUID = Depends(guard), db: Session = Depends(get_db)):
    _check_size(request)
    return _json(TeamRepositoryView(**svc.add_repository(db, user_id, team_id, body.repository_id, request_id=_request_id(request))), 201)


@router.delete('/teams/{team_id}/repositories/{repository_id}')
def remove_repository(team_id: UUID, repository_id: int, request: Request, user_id: UUID = Depends(guard), db: Session = Depends(get_db)):
    svc.remove_repository(db, user_id, team_id, repository_id, request_id=_request_id(request))
    return JSONResponse({'team_id': str(team_id), 'repository_id': repository_id, 'status': 'removed'}, headers=NO_STORE)


# ── 명시적 공유 ──
@router.get('/teams/{team_id}/shares')
def list_shares(team_id: UUID, user_id: UUID = Depends(guard), db: Session = Depends(get_db)):
    return _json(ShareList(shares=[ShareView(**s) for s in svc.list_shares(db, user_id, team_id)]))


@router.post('/teams/{team_id}/shares')
def create_share(team_id: UUID, body: ShareCreate, request: Request, user_id: UUID = Depends(guard), db: Session = Depends(get_db)):
    _check_size(request)
    view, created = svc.create_share(db, user_id, team_id, body, request_id=_request_id(request))
    return _json(ShareView(**view), 201 if created else 200)


@router.delete('/teams/{team_id}/shares/{share_id}')
def delete_share(team_id: UUID, share_id: UUID, request: Request, user_id: UUID = Depends(guard), db: Session = Depends(get_db)):
    svc.delete_share(db, user_id, team_id, share_id, request_id=_request_id(request))
    return JSONResponse({'share_id': str(share_id), 'status': 'unshared'}, headers=NO_STORE)


@router.get('/teams/{team_id}/shares/{share_id}/content')
def share_content(team_id: UUID, share_id: UUID, user_id: UUID = Depends(guard), db: Session = Depends(get_db)):
    return _json(ShareContent(**svc.share_content(db, user_id, team_id, share_id)))


# ── 보고서 ──
@router.get('/teams/{team_id}/reports')
def list_reports(team_id: UUID, user_id: UUID = Depends(guard), db: Session = Depends(get_db)):
    return _json(ReportList(reports=[ReportListItem(**r) for r in reports.list_reports(db, user_id, team_id)]))


@router.post('/teams/{team_id}/reports')
def create_report(team_id: UUID, body: ReportCreate, request: Request, user_id: UUID = Depends(guard), db: Session = Depends(get_db)):
    _check_size(request)
    return _json(ReportView(**reports.generate(db, user_id, team_id, body, request_id=_request_id(request))), 201)


@router.get('/teams/{team_id}/reports/{report_id}')
def get_report(team_id: UUID, report_id: UUID, user_id: UUID = Depends(guard), db: Session = Depends(get_db)):
    return _json(ReportView(**reports.get_report(db, user_id, team_id, report_id)))


@router.delete('/teams/{team_id}/reports/{report_id}')
def delete_report(team_id: UUID, report_id: UUID, request: Request, user_id: UUID = Depends(guard), db: Session = Depends(get_db)):
    reports.delete_report(db, user_id, team_id, report_id, request_id=_request_id(request))
    return JSONResponse({'report_id': str(report_id), 'status': 'deleted'}, headers=NO_STORE)
