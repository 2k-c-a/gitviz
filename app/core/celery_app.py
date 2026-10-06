from celery import Celery
from dotenv import load_dotenv
import os

load_dotenv()

REDIS_URL = os.getenv("REDIS_URL")

celery = Celery(
    "gitviz",
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=["app.tasks.analyze_repository", "app.tasks.ai_dispatch", "app.tasks.ai_maintenance",
             "app.tasks.ai_prepare"]
)

# AI 작업은 분석 task와 큐를 분리한다(기존 분석은 기본 큐 그대로).
celery.conf.task_routes = {
    "ai.prepare_job": {"queue": "ai_prepare"},
    "ai.generate_job": {"queue": "ai_generate"},
    "ai.maintenance": {"queue": "ai_maintenance"},
}
