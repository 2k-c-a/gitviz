# 개발 환경 설정 (2차 · 설계)

> **4차 반영(2026-10-30):** 이 문서의 [현재] 명령은 4차 구조 변경(S1~S9) 이후의 경로를 따른다: `tools/{build,validation,ops,vm}`, `tests/{server,frontend,workspace}`, `packaging/agent`, `build-output/`. 경로 이동 이력은 [이동 이력](../architecture/MIGRATION_HISTORY.md), 실행 기록은 [구현 기록](../architecture/IMPLEMENTATION_LOG.md). [제안]으로 표시된 환경 가드·채널 설정·데이터 경로 변경은 아직 적용되지 않았다(R1~R4).

작성일: 2026-10-30 · 기준 커밋: `65f1b62` · [문서 안내](../index.md)

설계 배경은 [환경 분리 아키텍처](../architecture/ENVIRONMENT_ARCHITECTURE.md)에 있다. 짝 문서: [빌드와 릴리스](../deployment/BUILD_AND_RELEASE.md) · [설치 안내](../deployment/INSTALLATION_GUIDE.md) · [데이터 저장 정책](../deployment/DATA_STORAGE_POLICY.md)

이 문서의 명령은 **읽기만 해서 작성했고 실행하지 않았다.** 각 항목에 상태를 표시한다.

- **[현재]** README, `docs/operations/setup.md`, `package.json`, CI 워크플로, 스크립트에서 확인한 명령.
- **[제안]** 아직 구현되지 않은 설계. 3차 통합 후에 도입한다.

## 1. 원칙

1. 개발 환경은 운영 데이터에 접근하지 않는다. 개발 PC에는 운영 DB 자격 증명을 두지 않는다.
2. 개발 중인 데스크톱 앱은 설치된 앱의 사용자 데이터를 읽거나 쓰지 않는다.
3. 환경을 초기화해도 영향 범위는 개발 DB, 개발 데이터 폴더, 생성물로 한정된다.
4. 비밀은 저장소에 들어가지 않는다(`.env*`는 `.gitignore` 대상, `.env.example`은 이름만).

## 2. 사전 준비

| 도구 | 버전 | 근거 |
|---|---|---|
| Python | 3.12 (CI와 빌드는 3.12.10) | CI `setup-python`, `pyvenv.cfg` |
| Node.js | 24.20.x (`>=24.20.0 <25`) | `desktop/package.json` `engines`, CI |
| Git | 로컬 Git (워크스페이스 기능이 사용) | `app/workspace/git_service.py` |
| Docker | PostgreSQL 16, Redis 7 컨테이너 | `docker-compose.yml` |
| Windows 11 | 데스크톱 앱·설치 프로그램 검증 | `pythonPlatform: Windows` |
| Hyper-V(선택) | 클린 VM 설치 검증 | `desktop/tests/run-clean-vm.ps1` |

## 3. 처음 내려받았을 때 (시나리오 A)

**[현재]**

```powershell
git clone <저장소 URL> gitviz
cd gitviz
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
if (!(Test-Path .env)) { Copy-Item .env.example .env }
docker compose up -d db redis
```

주의: 이 PC의 기존 가상환경은 `venv/`(README는 `.venv`)이고, `.claude/launch.json`이 `venv\Scripts\python.exe`를 절대 경로로 가리킨다. 새 클론에서는 README의 `.venv`를 따른다.

**[제안]** 위 단계를 `tools/dev/bootstrap.ps1`로 묶어 다음을 검사한다: 도구 버전, `.env`에 운영 호스트가 있는지(있으면 중단), 개발 DB 컨테이너 상태.

## 4. 환경 변수

**[현재]** `.env` 하나를 `python-dotenv`가 읽는다. 변수 이름은 `.env.example`(61개)이 안내한다. 값은 저장소에 없다. 서버 시작에 필수인 값:

| 변수 | 비고 |
|---|---|
| `SESSION_SECRET_KEY` | 없거나 `change-me-in-production`이면 서버가 **시작하지 않는다**(`app/main.py`) |
| `DATABASE_URL` | 동기(psycopg2). Alembic·분석 라우터·Celery 작업이 사용 |
| `DATABASE_URL_ASYNC` | 비동기(asyncpg). 없으면 `DATABASE_URL`을 변환해 사용 |
| `REDIS_URL` | Celery 브로커·백엔드 |
| `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET`, `FRONTEND_URL`, `TOKEN_ENCRYPTION_KEY` | GitHub 로그인·토큰 보관 |

선택: `AI_ENABLED`와 `AI_*`(기본 꺼짐), `DESKTOP_AUTH_ENABLED`·`DESKTOP_PUBLIC_ORIGIN`·`DESKTOP_AUTH_RESPONSE_KEY`(데스크톱 로그인), `SESSION_COOKIE_SECURE`(개발은 0).

**[제안] 환경별 파일:**

| 파일 | 위치 | 내용 | Git |
|---|---|---|---|
| `.env.example` | 저장소 | 모든 변수 이름과 설명, 값 없음 | 추적 |
| `.env.development` | 개발 PC | 로컬 compose DB(`gitviz_dev`), 개발용 키 | 제외 |
| `.env.test` | 개발 PC | `gitviz_test` DB, 가짜 AI 제공자 | 제외 |
| (운영 값) | 서버 시크릿 저장소 | 운영 DB·키 | 개발 PC에 두지 않음 |

로드 규칙: `GITVIZ_ENV`(기본 `development`)에 해당하는 파일 하나만 읽고, 이미 설정된 환경변수는 덮어쓰지 않는다. `GITVIZ_ENV=production`은 개발 PC에서 거부한다.

## 5. 의존성 설치 (시나리오 B)

### 서버 (웹 + Celery)

**[현재]** `pip install -r requirements.txt` (고정 버전. `requests`만 미고정).

### 워크스페이스 에이전트 (빌드·테스트용)

**[현재]** 서버 요구사항과 섞지 않는다(`packaging/agent/requirements-workspace.txt`의 주석).

```powershell
python -m venv .validation\venv-workspace          # CI와 같은 위치
.\.validation\venv-workspace\Scripts\python.exe -m pip install --require-hashes --only-binary=:all: -r packaging/agent/requirements-workspace.lock
.\.validation\venv-workspace\Scripts\python.exe -m pip install -r packaging/agent/requirements-workspace-build.txt   # PyInstaller, ruff
```

`pyright-workspace.json`이 `.validation/venv-workspace`를 가리키므로 `npm run typecheck:agent`도 이 이름을 기대한다. **[제안]** 위치를 `.local/venv-build-agent`로 옮기고 설정을 함께 바꾼다(3차).

### 데스크톱

**[현재]**

```powershell
npm --prefix desktop ci --ignore-scripts --no-audit --no-fund
node desktop\node_modules\electron\install.js        # --ignore-scripts를 쓴 경우 Electron 바이너리 설치
```

`--ignore-scripts` 없이 `npm --prefix desktop ci`만 써도 된다. CI는 보안상 스크립트를 끈다.

### 프런트엔드(웹 UI)

**[현재]** 설치할 것이 없다. 번들러·패키지 매니저 없이 `frontend/`를 서버가 정적으로 서빙한다. 벤더 라이브러리는 `frontend/vendor/`에 있다.

## 6. 개발 DB

**[현재]** `docker-compose.yml`: PostgreSQL 16, 사용자·비밀번호·DB 모두 `gitviz`, 포트 5432, 볼륨 `postgres_data`. Redis 7, 포트 6379. 이 값은 로컬 개발 전용이다.

```powershell
docker compose up -d db redis
python -m alembic upgrade head        # 스키마는 서버 시작 시 자동 생성되지 않는다
```

**테스트 DB:** compose는 `gitviz` DB 하나만 만든다. 서버 테스트는 `gitviz_test`로 시작하는 DB를 요구하므로(`ai_test_support.require_test_database`, `test_backend_improvements`) 직접 만들어야 한다(**[현재]** 문서화되어 있지 않음).

```powershell
# [제안 명령] 로컬 compose 컨테이너에서만 실행
docker compose exec db psql -U gitviz -d postgres -c "CREATE DATABASE gitviz_test;"
$env:GITVIZ_TEST_DATABASE_URL = "postgresql://gitviz:gitviz@localhost:5432/gitviz_test"
```

**[제안]** compose가 `gitviz_dev`·`gitviz_test`를 초기화 스크립트로 함께 만들고, 개발 서버와 테스트가 서로 다른 DB를 쓰게 한다.

테스트 데이터: `tests/server/fixtures/`(6개)와 테스트 내부 생성 데이터. 운영 데이터를 복사해서 쓰지 않는다.

## 7. 개발 서버 실행

### 백엔드 (시나리오 B)

**[현재]**

```powershell
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
python -m celery -A app.core.celery_app:celery worker --loglevel=info --pool=solo      # 별도 터미널
```

AI를 쓰려면(기본 꺼짐) 워커와 디스패처가 모두 필요하다.

```powershell
python -m celery -A app.core.celery_app:celery worker -Q ai_prepare,ai_generate,ai_maintenance --loglevel=info --pool=solo
python -m app.tasks.ai_dispatch
```

반드시 **저장소 루트에서** 실행한다. `app/main.py`가 `StaticFiles(directory="frontend")`를 작업 디렉터리 기준으로 열기 때문이다.

### 프런트엔드

**[현재]** 별도 실행이 없다. `http://127.0.0.1:8000`에서 서버가 서빙하고 `Cache-Control: no-cache`를 붙이므로 수정 후 새로고침하면 된다. GitHub OAuth를 쓰려면 `FRONTEND_URL`과 접속 주소가 일치해야 한다.

### 데스크톱

**[현재]**

```powershell
npm --prefix desktop run build
npm --prefix desktop start          # = electron .
```

두 가지를 알아야 한다. (1) 앱은 `resources\agent\gitviz-workspace-agent.exe`를 요구하는데 개발 실행(`electron .`)에는 이 경로가 없으므로, 로컬 워크스페이스 기능을 보려면 에이전트를 먼저 빌드하고 `process.resourcesPath` 쪽에 놓아야 한다. 이 방법은 문서화되어 있지 않다. (2) 사용자 데이터가 설치된 앱과 같은 `%APPDATA%\gitviz-workspace`다.

**[제안]** 개발 빌드(`app.isPackaged === false`)에서만 유효한 두 변수를 도입한다.

| 변수 | 효과 | 포장 빌드 |
|---|---|---|
| `GITVIZ_USER_DATA_DIR` | `userData`/`sessionData`를 지정 폴더로. 기본값 `%LOCALAPPDATA%\GitViz-Dev\` | 무시 |
| `GITVIZ_AGENT_COMMAND` | 에이전트를 `python -m app.workspace` 같은 명령으로 실행 | 무시 |
| `GITVIZ_SERVER_ORIGIN` | 개발 서버의 HTTPS 원점 | 무시(내장 설정 사용) |

## 8. 디버깅

| 대상 | 방법 | 상태 |
|---|---|---|
| 서버 | `python -m uvicorn … --reload`(코드 변경 자동 재시작), 브라우저 개발자 도구, `.claude/launch.json`(로컬 도구 설정) | `--reload`는 [제안], 나머지 [현재] |
| Celery | `--loglevel=debug` | [현재 옵션] |
| 에이전트 | 단위 테스트(`unittest`)를 IDE에서 실행. 표준 입출력 프레임을 직접 보려면 테스트 하니스(`tests/workspace/fixture_*.py`)를 참고 | [현재] |
| 데스크톱 렌더러 | 앱 창은 `devTools: false`(`window.ts`)라 개발자 도구가 열리지 않는다. 진단에는 Chromium 스위치 `--remote-debugging-port=<포트>`로 CDP에 붙였다(이번 프로젝트의 진단 세션에서 포장 앱에도 동작함을 확인) | [현재] |
| 로그 | 서버: 콘솔(`logging`을 쓰는 파일은 5개뿐, `print` 78회). 데스크톱: 로그 파일 없음 | 개선은 [제안] |

**[제안]** 포장 빌드에서는 `--remote-debugging-port`·`--inspect`를 거부하도록 하고(Electron fuses 또는 시작 시 인자 검사), 개발 빌드에서만 허용한다. 현재 포장 앱이 이 스위치를 받아들이는 점은 [환경 분리 아키텍처](../architecture/ENVIRONMENT_ARCHITECTURE.md) §6의 검토 항목이다.

## 9. 테스트 실행 (시나리오 C)

### 서버

**[현재]** CI와 같은 방식: 파일을 하나씩 실행한다. DB가 필요한 테스트는 `GITVIZ_TEST_DATABASE_URL`이 필요하다.

```powershell
$env:GITVIZ_TEST_DATABASE_URL = "postgresql://gitviz:gitviz@localhost:5432/gitviz_test"
python tests/server/test_backend_improvements.py
python tests/server/test_ai_contracts.py
# 전체 목록: .github/workflows/backend-validation.yml (36줄)
node tests/frontend/test_metric_display.cjs
```

AI 통합(큐) 테스트는 `GITVIZ_TEST_REDIS_URL`(테스트 전용 Redis)도 요구한다. 시작 시 해당 DB를 비우므로 개발 Redis와 절대 같은 값을 쓰면 안 된다(`test_ai_queue_integration.py` 설명).

에이전트 테스트(`tests/workspace`)는 개발용 `venv`가 아니라 에이전트 잠금 환경(`.validation/venv-workspace`)에서 실행한다. 개발용 `venv`에는 `jsonschema`가 없어 17개 모듈이 import 오류로 실패한다(4차 S0 실측).

### 에이전트

**[현재]**

```powershell
.\.validation\venv-workspace\Scripts\python.exe -m unittest discover -s tests/workspace -p "test_*.py" -v
.\.validation\venv-workspace\Scripts\python.exe -m ruff check --no-cache --config ruff-workspace.toml app/workspace tests/workspace tools/build/build_workspace_agent.py
npm --prefix desktop run typecheck:agent        # pyright strict
```

### 데스크톱

**[현재]**

```powershell
npm --prefix desktop run lint
npm --prefix desktop run typecheck
npm --prefix desktop run build
npm --prefix desktop test                        # node --test (단위 17개)
npm --prefix desktop run test:foundation         # Electron E2E 1건. 나머지 E2E는 validate_workspace_windows.ps1이 순서대로 실행
```

E2E는 `desktop/tests/profile.ts`가 임시 폴더를 `userData`/`sessionData`로 지정하므로 실제 사용자 데이터를 쓰지 않는다.

### 전체

**[현재]** Windows 통합 게이트: `tools/validation/validate_workspace_windows.ps1 -Node <node.exe> -EvidenceDirectory <폴더>`(단계: `ruff`, `desktop-build`, `agent-freeze`, `host-unit`, `workspace-unit`, `web-navigation`, `web-regression`, `documents`). **[제안]** `tools/dev/test.ps1 [server|agent|desktop|all]`로 `GITVIZ_ENV=test`와 임시 데이터 폴더를 강제.

## 10. 개발 환경 초기화

원칙: 초기화 명령은 **개발 DB와 개발 데이터 폴더만** 대상으로 하고 시작 전에 대상을 출력해 확인한다.

| 대상 | 방법 | 상태 |
|---|---|---|
| 개발 DB | `docker compose down -v`(볼륨 삭제) 후 `up -d`, `alembic upgrade head` | [현재 명령]. 운영 DB에는 영향 없음 — 단, 개발 `.env`가 로컬 compose를 가리킬 때만 |
| 테스트 DB | `gitviz_test*` DB에서 실행한다(DB 이름이 `gitviz_test`로 시작하지 않으면 거부). `test_backend_improvements.py`는 `command.upgrade(config, 'head')`로 Alembic 전체 체인을 적용하며, 오래된 리비전(`393e33b2dcbb`, `e2f3a4b5c6d7`)에서 `head`로 올려도 데이터가 보존되는지도 검사한다 | [현재] |
| 개발 데스크톱 데이터 | 개발 전용 폴더 삭제 | [제안] (현재는 설치 앱과 같은 폴더라 **삭제하지 말 것**) |
| 생성물 | `desktop\dist`, `build-output\` 삭제 후 재빌드 (4차 S9 이후 에이전트·패키지·설치 파일 모두 `build-output` 아래) | [현재] |
| 가상환경 | 폴더 삭제 후 재생성 | [현재] |

**금지:** `.env`의 `DATABASE_URL`이 원격 호스트인 상태에서 `tools/ops/cleanup_*.py`, `tools/ops/legacy/migrate_*.py`, `tools/ops/backfill_*.py`를 실행하지 않는다. 이 스크립트들은 현재 DB 이름·호스트를 검사하지 않는다(가드 0개). **[제안]** 가드 도입 전까지 로컬 compose 환경에서만 실행.

## 11. 운영 데이터와의 격리 점검표

개발 시작 전과 PR 전에 확인한다.

- [ ] `.env`/`.env.development`의 `DATABASE_URL*` 호스트가 `localhost`·`127.0.0.1`·`db`인가.
- [ ] `REDIS_URL`이 로컬인가. 테스트용 Redis는 개발 Redis와 다른가.
- [ ] `GITVIZ_SERVER_ORIGIN`이 운영 원점이 아닌가(데스크톱 개발 실행 시).
- [ ] 개발용 `SESSION_SECRET_KEY`·암호화 키가 운영 값과 다른가.
- [ ] 데스크톱 개발 실행의 사용자 데이터 폴더가 설치된 앱의 폴더와 다른가([제안]이 구현되기 전에는 개발 실행을 하는 PC에 설치된 앱의 중요한 데이터를 두지 않는다).
- [ ] `git status`에 `.env*`, `certs/`, `.validation/`, 키·토큰 파일이 없는가.
