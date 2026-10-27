# GitViz

GitHub 저장소의 이력을 수집하고 코드 변경 집중도·공동 변경·협업 관계를 분석한다.
분석 결과는 기준 SHA와 기간을 가진 불변 스냅샷으로 저장한다.

## 로컬 실행

Python 3.12, Git, Docker가 필요하다. 프로젝트 루트에서 실행한다.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
if (!(Test-Path .env)) { Copy-Item .env.example .env }
docker compose up -d db redis
```

`.env`의 DB/Redis 연결을 아래처럼 설정하고, GitHub OAuth 설정과 암호화 키·고유 세션 비밀키도 `.env.example`에 따라 준비한다.

```text
DATABASE_URL=postgresql://gitviz:gitviz@localhost:5432/gitviz
DATABASE_URL_ASYNC=postgresql+asyncpg://gitviz:gitviz@localhost:5432/gitviz
REDIS_URL=redis://localhost:6379/0
FRONTEND_URL=http://localhost:8000
```

스키마를 먼저 설치한다. 앱은 실행 중 테이블을 자동 생성하지 않는다.

```powershell
python -m alembic upgrade head
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

별도 터미널에서 가상환경을 활성화하고 워커를 실행한다.

```powershell
python -m celery -A app.core.celery_app:celery worker --loglevel=info --pool=solo
```

브라우저는 `http://localhost:8000`으로 접속한다. 새 분석/재분석 요청에는 로그인이 필요하다.
기존 raw 데이터만 있고 저장 결과가 없는 DB는 [SNAPSHOT_AND_ACCESS.md](SNAPSHOT_AND_ACCESS.md)의 backfill 절차를 사용한다.

## 검증

아래 DB는 운영 DB와 별개로 생성해야 한다. 백엔드 통합 검사는 `gitviz_test`로 시작하는 DB 이름만 허용한다.

```powershell
$env:GITVIZ_TEST_DATABASE_URL='postgresql://gitviz_test:validation@localhost:5432/gitviz_test'
python scripts/test_backend_improvements.py
python scripts/test_analytics_calculations.py
python scripts/test_repository_snapshots.py
python scripts/test_lightweight_score.py
node scripts/test_metric_display.cjs
```

GitHub Actions의 `Backend validation`도 PostgreSQL 16에서 이 검사를 수행한다.
GitHub API/OAuth 실제 로그인 및 수집의 완전성을 검증하는 테스트와는 별개다.

## AI 리뷰 (선택, 기본 꺼짐)

사용자가 자신의 AI API key(BYOK)를 등록하고, 선택한 PR 파일의 변경 코드를 동의 하에 제공자로 보내 변경 설명과 코드 리뷰를 받는다. 서버 공용 제공자 key는 없다. 결과는 모델이 만든 참고 자료이며 사람이 검증하기 전이다.

AI 리뷰는 PR·커밋을 지원하며 S11은 전체 변경 파일의 추가 검토 후보를 표시합니다. **S12 인수인계·S13 보고서는 폐기**, **S14 GitHub App·Webhook·자동 리뷰와 S15 GitHub 댓글 게시는 보류**합니다. [AI 구현 계획](docs/GitViz_AI_의존성기반_구현계획_v0.5.md)을 참고하세요.

```powershell
# .env: AI_ENABLED=1, AI_INPUT_ENCRYPTION_KEYS, AI_CREDENTIAL_ENCRYPTION_KEYS(서로 다른 값), AI_ALLOW_EXTERNAL_TRANSFER=1 …
python scripts/ai_ops.py check                       # 설정·migration·Redis 점검
docker compose up -d db redis
docker compose --profile ai up -d ai-worker ai-dispatcher   # 작업 처리·발행·유지관리(둘 다 필요)
```

화면은 `/pages/ai-review.html?repo=<id>`(상단 메뉴 "AI 리뷰"). 운영(설정·확인·취소·불명 호출 정리·기한 삭제)은 [AI 운영 안내](docs/AI_OPERATIONS.md), 수용 상태와 출시 전 남은 확인은 [AI MVP 수용 보고](docs/AI_MVP_ACCEPTANCE.md), 화면 설계는 [AI_S9_UI_DESIGN.md](docs/AI_S9_UI_DESIGN.md)를 본다.

AI는 Anthropic·OpenAI·Gemini 사용자 BYOK와 같은 PC의 Ollama를 지원한다. [지원 모델·설정·smoke 안내](docs/AI_PROVIDER_SUPPORT.md)를 참고한다. Ollama는 API key 없이 설치 모델의 digest를 고정하며, GitViz API·worker와 같은 PC의 호스트 프로세스로 실행한다. 웹 GitViz에서 각 사용자 PC의 모델을 연결하는 기능은 [후속 과제](docs/AI_LOCAL_MODEL_PLAN.md)로 남긴다. 실제 제공자·설치 모델 연동과 품질 평가는 별도로 필요하다.

AI 검증(일회용 PostgreSQL·Redis 필요, `GITVIZ_TEST_REDIS_URL`은 시작 시 비워진다):

```powershell
python scripts/verify_ai_mvp_integration.py   # 실제 DB·Redis + app·worker·dispatcher·가짜 제공자 프로세스
python scripts/test_ai_ops.py
python scripts/test_ai_evaluation.py
node scripts/test_ai_frontend.cjs
python scripts/evaluate_ai_review.py --check-release   # 실제 모델 평가(별도 예산)에서 남은 항목
python scripts/test_ai_smoke.py               # smoke 도구(실제 호출은 smoke_ai_live.py --live, docs/AI_EVALUATION_PROTOCOL.md)
```

## 분석 이력·비교 API

```text
GET /repositories/{id}/snapshots?limit=20&before_id=100
GET /repositories/{id}/snapshots/compare?before=10&after=20
```

이력은 최신 ID부터 반환하며 `next_before_id`로 다음 페이지를 요청한다.
비교는 같은 저장소·계산 버전만 허용하고, 정확히 같은 파일 경로를 대응한다.
이름 변경은 삭제/추가로 표시하고 데이터가 없는 지표의 차이는 `null`이다.
Risk Score 차이는 저장소 내 상대 점수 변화로 해석해야 한다.

## 담당별 문서

- [데이터 수집 팀원 전달 사항](docs/DATA_COLLECTION_HANDOFF.md)
- [시각화 팀원 전달 사항](docs/VISUALIZATION_HANDOFF.md)
- [백엔드 개선 결과와 검증](docs/BACKEND_IMPROVEMENTS.md)
- [분석 타당성 검증 계획](docs/ANALYSIS_VALIDATION_PLAN.md)
- [공개 저장소 실연동 검증 결과](docs/LIVE_VALIDATION.md)
- [AI 인터페이스 계약](docs/AI_INTERFACE_CONTRACT.md) · [AI 운영 안내](docs/AI_OPERATIONS.md) · [AI MVP 수용 보고](docs/AI_MVP_ACCEPTANCE.md)

