"""사용자 API key(BYOK) 관리 API와 지원 제공자·모델 목록. 응답에는 마스킹된 힌트만 있고 key는 돌려주지 않는다."""
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Request
from sqlalchemy.orm import Session

from app.api.ai.inputs import NO_STORE, _json, _request_id, guard
from app.core.ai_settings import get_ai_settings
from app.core.database import get_db
from app.schemas.ai import (CredentialChange, CredentialDeleted, CredentialList, ErrorCode, ProvidersResponse,
                            RegisterKeyRequest, SupportedProvider)
from app.services.ai import credentials
from app.services.ai.access import AIError
from app.services.ai.providers import registry

router = APIRouter(prefix='/api/ai/v1', tags=['ai-credentials'])
MAX_BODY_BYTES = 4096
ProviderPath = Path(pattern=r'^[a-z][a-z0-9_-]{1,31}$')


@router.get('/providers')
def get_providers(user_id: UUID = Depends(guard)):
    """지원하는 제공자와 모델(가격·한도 포함). allowed=false인 모델은 서버 관리자가 허용하지 않았다."""
    body = ProvidersResponse(providers=[SupportedProvider(**p) for p in registry.supported_options()],
                             external_transfer_allowed=get_ai_settings().allow_external_transfer)
    return _json(body)


@router.get('/credentials')
def list_credentials(user_id: UUID = Depends(guard), db: Session = Depends(get_db)):
    return _json(CredentialList(credentials=credentials.list_credentials(db, user_id)))


@router.put('/credentials/{provider}')
def put_credential(body: RegisterKeyRequest, request: Request, provider: str = ProviderPath,
                   user_id: UUID = Depends(guard), db: Session = Depends(get_db)):
    """key를 등록하거나 교체한다. 같은 key를 다시 보내면 변경 없이 현재 상태를 돌려준다."""
    if int(request.headers.get('content-length') or 0) > MAX_BODY_BYTES:
        raise AIError(ErrorCode.INPUT_TOO_LARGE, '요청이 너무 큽니다.')
    if not get_ai_settings().allow_external_transfer:
        raise AIError(ErrorCode.TRANSFER_NOT_ALLOWED, '이 서버는 외부 AI 제공자 사용이 허용되지 않았습니다.')
    result = credentials.register_key(
        db, user_id, provider, body.api_key.get_secret_value(), preferred_profile_id=body.profile_id,
        verify=body.verify, request_id=_request_id(request))
    return _json(CredentialChange(credential=result.credential, changed=result.changed,
                                  previous_version=result.previous_version))


@router.delete('/credentials/{provider}')
def delete_credential(request: Request, provider: str = ProviderPath, user_id: UUID = Depends(guard),
                      db: Session = Depends(get_db)):
    """암호문을 즉시 삭제하고, 이 key에 고정된 진행 중 작업을 취소한다."""
    cancelled = credentials.delete_credential(db, user_id, provider, request_id=_request_id(request))
    return _json(CredentialDeleted(provider=provider, jobs_cancelled=cancelled))
