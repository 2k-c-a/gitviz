"""AI 인사이트 실행 task. 자동 분석 리포트와 대화형 답변은 서로 다른 task(실행 흐름)다.

같은 행을 두 번 실행하지 않도록 서비스가 queued → running 전환에 성공한 경우에만 진행한다.
실패는 자동으로 다시 시도하지 않는다(AI 호출 비용이 다시 생기므로 사용자가 다시 요청한다).
"""
from uuid import UUID

from app.core.celery_app import celery
from app.core.database import SessionLocal
from app.services.ai import insight_service


@celery.task(name='ai.insight_report', ignore_result=True, acks_late=False)
def run_insight_report(report_id: str) -> str:
    return insight_service.run_report(UUID(report_id), SessionLocal)


@celery.task(name='ai.insight_answer', ignore_result=True, acks_late=False)
def run_insight_answer(message_id: str) -> str:
    return insight_service.run_answer(UUID(message_id), SessionLocal)
