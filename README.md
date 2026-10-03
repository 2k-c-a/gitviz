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

