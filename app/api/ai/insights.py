"""AI 인사이트 API (/api/ai/v1/insights): 자동 분석 리포트와 대화형 분석.

- 앱 전용 BYOK(AD-32 ⑧): 사용자의 API key는 앱이 보관하고, AI를 부르는 요청(리포트 실행·질문)마다
  `X-GitViz-AI-Key` 헤더로 보낸다. 서버는 key를 DB·큐·로그에 남기지 않고 그 실행의 메모리에서만 쓴다.
  그래서 실행은 Celery가 아니라 이 API 프로세스의 백그라운드 스레드에서 한다(서버가 재시작되면 그 실행은 실패로 끝난다).
- 서버 비밀 설정 없이 동작한다. 끄려면 AI_INSIGHTS_ENABLED=0.
- 본인 자료만 보이고, 조회할 때마다 저장소 읽기 권한을 다시 확인한다. 모든 응답은 private, no-store다.
- 변경 요청은 로그인·CSRF(앱 bearer 요청은 통과)를 거친다.
"""
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.api.ai.dependencies import request_api_key, require_csrf
from app.api.ai.inputs import NO_STORE
from app.api.auth import _get_session_user_id
from app.core.ai_settings import get_ai_settings
from app.core.database import SessionLocal, get_db
from app.schemas.ai import (AskInsightQuestionRequest, CreateInsightConversationRequest, CreateInsightReportRequest,
                            ErrorCode)
from app.services.ai import insight_service
from app.services.ai.access import AIAction, AIError, authorize_ai
from app.services.ai.content_policy import POLICY_VERSION

router = APIRouter(prefix='/api/ai/v1/insights', tags=['ai-insights'])
MAX_BODY_BYTES = 8192
# 실행 하나가 도구 조회용 DB 세션을 끝까지 잡는다. 프로세스당 DB 풀이 5개(app/core/database.py)라 동시 실행은 2개로 두고
# 나머지는 순서대로 기다린다(대기 중인 행은 queued로 보인다).
RUN_CONCURRENCY = 2
_RUNNER = ThreadPoolExecutor(max_workers=RUN_CONCURRENCY, thread_name_prefix='ai-insight')


def guard(request: Request) -> UUID:
    """AI 분석이 켜져 있고 로그인했는지, (변경 요청이면) CSRF를 확인한다."""
    if not get_ai_settings().insights_enabled:
        raise AIError(ErrorCode.RESOURCE_NOT_FOUND, '사용할 수 없는 기능입니다.')
    user_id = _get_session_user_id(request)
    if user_id is None:
        raise AIError(ErrorCode.AUTH_REQUIRED, '로그인이 필요합니다.')
    require_csrf(request)
    return user_id


def _body_size(request: Request) -> None:
    if int(request.headers.get('content-length') or 0) > MAX_BODY_BYTES:
        raise AIError(ErrorCode.INPUT_TOO_LARGE, '요청이 너무 큽니다.')


def _ok(body, status: int = 200) -> JSONResponse:
    return JSONResponse(body, status_code=status, headers=NO_STORE)


def _submit(run, row_id: UUID, factory) -> None:
    """백그라운드 스레드에서 실행한다(시험에서 바꿀 수 있도록 분리)."""
    _RUNNER.submit(run, row_id, SessionLocal, factory)


def _start(db: Session, kind: str, table: str, row_id: UUID, api_key: str | None) -> None:
    try:
        _submit(getattr(insight_service, kind), row_id, insight_service.provider_factory(api_key))
    except Exception:
        insight_service.mark_enqueue_failed(db, table, row_id)


@router.get('/models')
def list_models(user_id: UUID = Depends(guard)):
    """AI 분석에 고를 수 있는 모델. key 등록 여부는 앱이 안다(서버는 key를 보관하지 않는다)."""
    return _ok({'models': insight_service.models()})


@router.post('/reports')
def create_report(body: CreateInsightReportRequest, request: Request, user_id: UUID = Depends(guard),
                  db: Session = Depends(get_db)):
    _body_size(request)
    api_key = request_api_key(request, insight_service.resolve_profile(body.profile_id, body.provider))
    report_id = insight_service.create_report(db, user_id, body.repository_id, body.snapshot_id, body.profile_id,
                                              body.include_code, body.provider)
    _start(db, 'run_report', 'ai_insight_reports', report_id, api_key)
    return _ok(insight_service.get_report(db, user_id, report_id), 202)


@router.get('/reports')
def list_reports(repository_id: int = Query(gt=0), user_id: UUID = Depends(guard), db: Session = Depends(get_db)):
    authorize_ai(db, user_id, repository_id, AIAction.read, POLICY_VERSION, insight=True)
    return _ok({'reports': insight_service.list_reports(db, user_id, repository_id)})


@router.get('/reports/{report_id}')
def get_report(report_id: UUID, user_id: UUID = Depends(guard), db: Session = Depends(get_db)):
    repository_id = insight_service.report_repository(db, user_id, report_id)
    authorize_ai(db, user_id, repository_id, AIAction.read, POLICY_VERSION, insight=True)
    return _ok(insight_service.get_report(db, user_id, report_id))


@router.post('/conversations')
def create_conversation(body: CreateInsightConversationRequest, request: Request, user_id: UUID = Depends(guard),
                        db: Session = Depends(get_db)):
    _body_size(request)
    conversation_id = insight_service.create_conversation(db, user_id, body.repository_id, body.snapshot_id,
                                                          body.profile_id, body.include_code)
    return _ok(insight_service.get_conversation(db, user_id, conversation_id), 201)


@router.get('/conversations')
def list_conversations(repository_id: int = Query(gt=0), user_id: UUID = Depends(guard), db: Session = Depends(get_db)):
    authorize_ai(db, user_id, repository_id, AIAction.read, POLICY_VERSION, insight=True)
    return _ok({'conversations': insight_service.list_conversations(db, user_id, repository_id)})


@router.get('/conversations/{conversation_id}')
def get_conversation(conversation_id: UUID, user_id: UUID = Depends(guard), db: Session = Depends(get_db)):
    repository_id = insight_service.conversation_repository(db, user_id, conversation_id)
    authorize_ai(db, user_id, repository_id, AIAction.read, POLICY_VERSION, insight=True)
    return _ok(insight_service.get_conversation(db, user_id, conversation_id))


@router.post('/conversations/{conversation_id}/messages')
def ask(conversation_id: UUID, body: AskInsightQuestionRequest, request: Request, user_id: UUID = Depends(guard),
        db: Session = Depends(get_db)):
    _body_size(request)
    profile = insight_service.conversation_profile(db, user_id, conversation_id)
    if body.provider != profile.provider:
        raise AIError(ErrorCode.INVALID_SELECTION, '이 대화의 모델과 제공자가 맞지 않습니다.')
    api_key = request_api_key(request, profile)
    message_id = insight_service.ask(db, user_id, conversation_id, body.question)
    _start(db, 'run_answer', 'ai_insight_messages', message_id, api_key)
    return _ok(insight_service.get_conversation(db, user_id, conversation_id), 202)


@router.delete('/conversations/{conversation_id}')
def delete_conversation(conversation_id: UUID, user_id: UUID = Depends(guard), db: Session = Depends(get_db)):
    """대화 초기화: 대화를 목록에서 숨기고 진행 중인 답변을 취소한다. 작성자 본인 자료는 저장소 권한과 무관하게 지울 수 있다."""
    insight_service.delete_conversation(db, user_id, conversation_id)
    return _ok({'id': str(conversation_id), 'deleted': True})
