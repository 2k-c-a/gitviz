"""인수인계 API(S12): 인계 대상, 준비 체크폼, 초안 생성, 편집 문서, Markdown 내보내기.

모든 요청은 작성자 본인의 자료만 다루며(남의 것은 404), 저장소 접근 권한을 매번 다시 확인한다.
초안은 모델을 호출하지 않으므로 코드 전송·비용이 없다. 본문은 감사 기록에 넣지 않는다.
"""
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session

from app.api.ai.inputs import NO_STORE, _json, _request_id, guard
from app.api.ai.jobs import authorize_read
from app.core.database import get_db
from app.schemas.ai import (ErrorCode, HandoverAdoptRequest, HandoverDocumentPut, HandoverDocumentView, HandoverGenerateRequest, HandoverGenerationList,
                            HandoverGenerationResponse, HandoverGenerationView, HandoverPreparationPut, HandoverPreparationView, HandoverTargetCreate,
                            HandoverTargetList, HandoverTargetPatch, HandoverTargetView)
from app.services.ai import handover_service as svc
from app.services.ai.access import AIError

router = APIRouter(prefix='/api/ai/v1', tags=['ai-handover'])
MAX_DOCUMENT_BODY_BYTES = 420_000        # 100,000자(한글 3바이트) + JSON 여유
MAX_BODY_BYTES = 64_000


def _check_size(request: Request, limit: int) -> None:
    if int(request.headers.get('content-length') or 0) > limit:
        raise AIError(ErrorCode.INPUT_TOO_LARGE, '요청이 너무 큽니다.')


def _repo_of_target(db: Session, user_id: UUID, target_id: UUID) -> int:
    repo = svc.get_target(db, user_id, target_id)['repository_id']
    authorize_read(db, user_id, repo)                       # 권한을 잃은 자료는 존재를 숨긴다
    return repo


@router.post('/handover-targets')
def create_target(body: HandoverTargetCreate, request: Request, user_id: UUID = Depends(guard), db: Session = Depends(get_db)):
    _check_size(request, MAX_BODY_BYTES)
    authorize_read(db, user_id, body.repository_id)
    return _json(HandoverTargetView(**svc.create_target(db, user_id, body, request_id=_request_id(request))), 201)


@router.get('/handover-targets')
def list_targets(repository_id: int = Query(gt=0), user_id: UUID = Depends(guard), db: Session = Depends(get_db)):
    authorize_read(db, user_id, repository_id)
    return _json(HandoverTargetList(targets=[HandoverTargetView(**t) for t in svc.list_targets(db, user_id, repository_id)]))


@router.get('/handover-targets/{target_id}')
def get_target(target_id: UUID, user_id: UUID = Depends(guard), db: Session = Depends(get_db)):
    _repo_of_target(db, user_id, target_id)
    return _json(HandoverTargetView(**svc.get_target(db, user_id, target_id)))


@router.patch('/handover-targets/{target_id}')
def patch_target(target_id: UUID, body: HandoverTargetPatch, request: Request, user_id: UUID = Depends(guard), db: Session = Depends(get_db)):
    _check_size(request, MAX_BODY_BYTES)
    _repo_of_target(db, user_id, target_id)
    return _json(HandoverTargetView(**svc.patch_target(db, user_id, target_id, body, request_id=_request_id(request))))


@router.delete('/handover-targets/{target_id}')
def delete_target(target_id: UUID, request: Request, user_id: UUID = Depends(guard), db: Session = Depends(get_db)):
    _repo_of_target(db, user_id, target_id)
    svc.delete_target(db, user_id, target_id, request_id=_request_id(request))
    return JSONResponse({'target_id': str(target_id), 'status': 'deleted'}, headers=NO_STORE)


@router.get('/handover-targets/{target_id}/preparation')
def get_preparation(target_id: UUID, user_id: UUID = Depends(guard), db: Session = Depends(get_db)):
    _repo_of_target(db, user_id, target_id)
    return _json(HandoverPreparationView(**svc.get_preparation(db, user_id, target_id)))


@router.get('/handover-targets/{target_id}/preparation/versions')
def preparation_versions(target_id: UUID, user_id: UUID = Depends(guard), db: Session = Depends(get_db)):
    _repo_of_target(db, user_id, target_id)
    return JSONResponse({'versions': [HandoverPreparationView(**v).model_dump(mode='json') for v in svc.list_preparation_versions(db, user_id, target_id)]},
                        headers=NO_STORE)


@router.put('/handover-targets/{target_id}/preparation')
def put_preparation(target_id: UUID, body: HandoverPreparationPut, request: Request, user_id: UUID = Depends(guard), db: Session = Depends(get_db)):
    _check_size(request, MAX_BODY_BYTES)
    _repo_of_target(db, user_id, target_id)
    return _json(HandoverPreparationView(**svc.put_preparation(db, user_id, target_id, body, request_id=_request_id(request))))


@router.post('/handover-targets/{target_id}/generations')
def generate(target_id: UUID, body: HandoverGenerateRequest, request: Request, user_id: UUID = Depends(guard), db: Session = Depends(get_db)):
    _check_size(request, MAX_BODY_BYTES)
    _repo_of_target(db, user_id, target_id)
    out = svc.generate(db, user_id, target_id, body, request_id=_request_id(request))
    reused = out['generation']['reused']
    return _json(HandoverGenerationResponse(generation=HandoverGenerationView(**out['generation']), document_id=out['document_id'],
                                            document_updated=out['document_updated']), 200 if reused else 201)


@router.get('/handover-targets/{target_id}/generations')
def list_generations(target_id: UUID, user_id: UUID = Depends(guard), db: Session = Depends(get_db)):
    _repo_of_target(db, user_id, target_id)
    return _json(HandoverGenerationList(generations=[HandoverGenerationView(**g) for g in svc.list_generations(db, user_id, target_id)]))


@router.get('/handover-generations/{generation_id}')
def get_generation(generation_id: UUID, user_id: UUID = Depends(guard), db: Session = Depends(get_db)):
    view, target = svc.get_generation(db, user_id, generation_id)
    authorize_read(db, user_id, target.repository_id)
    return _json(HandoverGenerationView(**view))


@router.get('/handover-targets/{target_id}/document')
def get_document(target_id: UUID, user_id: UUID = Depends(guard), db: Session = Depends(get_db)):
    _repo_of_target(db, user_id, target_id)
    return _json(HandoverDocumentView(**svc.get_document(db, user_id, target_id)))


def _authorize_document(db: Session, user_id: UUID, document_id: UUID) -> None:
    doc = svc.get_document_by_id(db, user_id, document_id)
    _repo_of_target(db, user_id, doc['target_id'])


@router.put('/handover-documents/{document_id}')
def put_document(document_id: UUID, body: HandoverDocumentPut, request: Request, user_id: UUID = Depends(guard), db: Session = Depends(get_db)):
    _check_size(request, MAX_DOCUMENT_BODY_BYTES)
    _authorize_document(db, user_id, document_id)
    return _json(HandoverDocumentView(**svc.put_document(db, user_id, document_id, body, request_id=_request_id(request))))


@router.post('/handover-documents/{document_id}/adopt')
def adopt(document_id: UUID, body: HandoverAdoptRequest, request: Request, user_id: UUID = Depends(guard), db: Session = Depends(get_db)):
    _check_size(request, MAX_BODY_BYTES)
    _authorize_document(db, user_id, document_id)
    return _json(HandoverDocumentView(**svc.adopt_generation(db, user_id, document_id, body, request_id=_request_id(request))))


@router.get('/handover-documents/{document_id}/export')
def export(document_id: UUID, request: Request, format: Literal['markdown'] = Query('markdown'), user_id: UUID = Depends(guard), db: Session = Depends(get_db)):
    _authorize_document(db, user_id, document_id)
    filename, content = svc.export_markdown(db, user_id, document_id, request_id=_request_id(request))
    return Response(content=content.encode('utf-8'), media_type='text/markdown; charset=utf-8',
                    headers={**NO_STORE, 'Content-Disposition': f'attachment; filename="{filename}"', 'X-Content-Type-Options': 'nosniff'})
