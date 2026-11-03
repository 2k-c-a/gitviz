"""AI 인사이트 API (/api/ai/v1/insights): 자동 분석 리포트와 대화형 분석.

- 접수(POST)는 행을 만들고 worker 작업을 넣는다(202). AI 호출은 worker가 한다.
- 본인 자료만 보이고, 조회할 때마다 저장소 읽기 권한을 다시 확인한다. 모든 응답은 private, no-store다.
- 변경 요청은 다른 AI API와 같은 guard(AI 기능·로그인·CSRF)를 거친다.
"""
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.api.ai.inputs import NO_STORE, guard
from app.core.database import get_db
from app.schemas.ai import (AskInsightQuestionRequest, CreateInsightConversationRequest, CreateInsightReportRequest,
                            ErrorCode)
from app.services.ai import insight_service
from app.services.ai.access import AIAction, AIError, authorize_ai
from app.services.ai.content_policy import POLICY_VERSION

router = APIRouter(prefix='/api/ai/v1/insights', tags=['ai-insights'])
MAX_BODY_BYTES = 8192


def _body_size(request: Request) -> None:
    if int(request.headers.get('content-length') or 0) > MAX_BODY_BYTES:
        raise AIError(ErrorCode.INPUT_TOO_LARGE, '요청이 너무 큽니다.')


def _ok(body, status: int = 200) -> JSONResponse:
    return JSONResponse(body, status_code=status, headers=NO_STORE)


def _enqueue(db: Session, task: str, table: str, row_id: UUID) -> None:
    from app.tasks import ai_insights
    try:
        getattr(ai_insights, task).delay(str(row_id))
    except Exception:
        insight_service.mark_enqueue_failed(db, table, row_id)


@router.post('/reports')
def create_report(body: CreateInsightReportRequest, request: Request, user_id: UUID = Depends(guard),
                  db: Session = Depends(get_db)):
    _body_size(request)
    report_id = insight_service.create_report(db, user_id, body.repository_id, body.snapshot_id, body.profile_id,
                                              body.include_code)
    _enqueue(db, 'run_insight_report', 'ai_insight_reports', report_id)
    return _ok(insight_service.get_report(db, user_id, report_id), 202)


@router.get('/reports')
def list_reports(repository_id: int = Query(gt=0), user_id: UUID = Depends(guard), db: Session = Depends(get_db)):
    authorize_ai(db, user_id, repository_id, AIAction.read, POLICY_VERSION)
    return _ok({'reports': insight_service.list_reports(db, user_id, repository_id)})


@router.get('/reports/{report_id}')
def get_report(report_id: UUID, user_id: UUID = Depends(guard), db: Session = Depends(get_db)):
    repository_id = insight_service.report_repository(db, user_id, report_id)
    authorize_ai(db, user_id, repository_id, AIAction.read, POLICY_VERSION)
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
    authorize_ai(db, user_id, repository_id, AIAction.read, POLICY_VERSION)
    return _ok({'conversations': insight_service.list_conversations(db, user_id, repository_id)})


@router.get('/conversations/{conversation_id}')
def get_conversation(conversation_id: UUID, user_id: UUID = Depends(guard), db: Session = Depends(get_db)):
    repository_id = insight_service.conversation_repository(db, user_id, conversation_id)
    authorize_ai(db, user_id, repository_id, AIAction.read, POLICY_VERSION)
    return _ok(insight_service.get_conversation(db, user_id, conversation_id))


@router.post('/conversations/{conversation_id}/messages')
def ask(conversation_id: UUID, body: AskInsightQuestionRequest, request: Request, user_id: UUID = Depends(guard),
        db: Session = Depends(get_db)):
    _body_size(request)
    message_id = insight_service.ask(db, user_id, conversation_id, body.question)
    _enqueue(db, 'run_insight_answer', 'ai_insight_messages', message_id)
    return _ok(insight_service.get_conversation(db, user_id, conversation_id), 202)


@router.delete('/conversations/{conversation_id}')
def delete_conversation(conversation_id: UUID, user_id: UUID = Depends(guard), db: Session = Depends(get_db)):
    """대화 초기화: 대화를 목록에서 숨기고 진행 중인 답변을 취소한다. 작성자 본인 자료는 저장소 권한과 무관하게 지울 수 있다."""
    insight_service.delete_conversation(db, user_id, conversation_id)
    return _ok({'id': str(conversation_id), 'deleted': True})
