"""설명·리뷰 생성 작업 handler 등록. 이 모듈을 import하면 'summary'·'review' handler가 등록된다(celery include에 포함)."""
from app.services.ai import generation_service, job_runner

job_runner.register_handler('summary', generation_service.generate_handler)
job_runner.register_handler('review', generation_service.generate_handler)
