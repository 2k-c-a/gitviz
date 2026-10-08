"""AI 작업 유지관리 task. dispatcher가 주기적으로 같은 일을 하며, 필요하면 수동으로도 호출할 수 있다."""
from app.core.celery_app import celery
from app.tasks.ai_dispatch import maintenance_once


@celery.task(name='ai.maintenance')
def maintenance() -> dict:
    return maintenance_once()
