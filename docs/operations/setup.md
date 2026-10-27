# 설치·프로세스 안내

검토일: 2026-10-23 · 코드 기준: `a8351f0` · [문서 안내](../index.md)

확인: 기본 준비는 [README](../../README.md)와 비밀값 없는 [.env.example](../../.env.example)을 따른다. 아래는 명령 안내이며 이번 문서화에서 설치·migration·프로세스 재시작을 실행하지 않았다.

## 일반 분석 개발 환경

Python 3.12, Git, Docker를 준비한다. 프로젝트 루트 PowerShell 예시:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
if (!(Test-Path .env)) { Copy-Item .env.example .env }
docker compose up -d db redis
```

`.env.example`을 읽고 DB·Redis·OAuth·세션 비밀키·토큰 암호화 키를 설정한 뒤:

```powershell
python -m alembic upgrade head
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

별도 가상환경 터미널에서 일반 분석 worker를 실행한다.

```powershell
python -m celery -A app.core.celery_app:celery worker --loglevel=info --pool=solo
```

OAuth 개발 접속은 설정된 `http://localhost:8000`과 일치시킨다. 앱 import/startup은 스키마를 자동 설치하지 않는다. migration 파일 그래프의 현재 head는 `a9d2f4c6e8b0`이며 이는 운영 DB 적용 완료를 뜻하지 않는다.

## 선택적인 AI 프로세스

[AI 설정·운영](ai.md)을 먼저 확인한다. AI에는 worker와 dispatcher가 모두 필요하다. 호스트 실행 예시:

```powershell
python -m celery -A app.core.celery_app:celery worker -Q ai_prepare,ai_generate,ai_maintenance --loglevel=info --pool=solo
python -m app.tasks.ai_dispatch
```

위 두 명령은 각각 별도 터미널/프로세스에서 실행한다. 기존 분석 worker와 AI 큐 worker의 역할을 구분한다.

클라우드용 compose 경로도 있다.

```powershell
docker compose --profile ai up -d ai-worker ai-dispatcher
```

확인: [compose](../../docker-compose.yml)는 `.env`의 DB URL을 사용하므로 컨테이너에서 접근 가능한 DB 호스트가 필요하다. 컨테이너의 `localhost`는 호스트 PC가 아니다. 같은 PC의 로컬 AI는 현재 호스트 API·worker·런타임 구성을 사용하고 `host.docker.internal`로 임의 우회하지 않는다.

## 설치 검증의 해석

추정: 재현 가능한 전용 환경에서 설치·빈 DB upgrade·기존 DB upgrade·일반 분석/AI 큐 회귀를 각각 수행한다. [검증 절차](../validation/protocols/document-consistency.md)를 참조한다.

미확정: 최신 원격 CI, 배포 DB revision, 로컬 포트·프로세스 상태는 검사하지 않았다. 과거 WEB 보고서의 ‘서버 실행 중’은 그 실행 당시 기록이다. `.env`의 실제 비밀값을 문서·검증 결과로 복사하지 않는다.
