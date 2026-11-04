from celery import Celery
from dotenv import load_dotenv
import os

load_dotenv()

REDIS_URL = os.getenv("REDIS_URL")

celery = Celery(
    "gitviz",
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=["app.tasks.analyze_repository"]
)
# AI 리뷰 작업은 Celery를 쓰지 않는다. 사용자 key가 요청마다 오므로 API 프로세스의 스레드에서 실행한다(AD-33).
