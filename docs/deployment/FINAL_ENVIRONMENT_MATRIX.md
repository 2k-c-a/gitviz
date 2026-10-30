# 최종 환경 매트릭스 (3차 확정)

작성일: 2026-10-30 · 기준 커밋: `65f1b62` · 상태: **확정 설계 (미적용)**. 표기: **[현재]** 지금 동작, **[R#]** 해당 릴리스에서 도입, **[S#]** 4차 구조 작업에서 변경.

함께 읽기: [최종 아키텍처](../architecture/FINAL_ARCHITECTURE.md) · [결정 기록](../architecture/ARCHITECTURE_DECISIONS.md) · [릴리스 이행 계획](RELEASE_MIGRATION_PLAN.md) · [개발 환경 설정](../development/DEVELOPMENT_SETUP.md) · [데이터 저장 정책](DATA_STORAGE_POLICY.md)

## 1. 환경 판별

`GITVIZ_ENV` ∈ `development` | `test` | `production` [R1]. 값이 없으면 서버·에이전트는 `development`로 간주하되 운영 DB로 보이는 URL이면 거부한다. 클라이언트 설치본(`app.isPackaged`)은 항상 `production`이며 환경변수로 바꿀 수 없다.

## 2. 환경별 매트릭스

| 항목 | Development | Testing | Build | Production (클라이언트) | Production (서버) |
|---|---|---|---|---|---|
| 설정 파일 | 루트 `.env`(Git 제외), `.env.example` 참고 | 파일 없음. 러너가 환경변수 주입 | `packaging/agent/*.lock`, `desktop/package-lock.json`, `desktop/channel.json` [R1] | `desktop/channel.json`을 빌드에 포함(공개 값만) | 호스트의 프로세스 환경변수. `.env` 미사용 [R1] |
| 로딩 우선순위 | 프로세스 환경 > `.env` > 코드 기본값 | 프로세스 환경 > 코드 기본값 (`.env` 무시) [R1] | 명령행 인수 > 환경변수 | 빌드 고정값 > 코드 기본값. 환경변수 덮어쓰기 불가 [R1] | 프로세스 환경 > 코드 기본값. `SESSION_SECRET_KEY` 없으면 기동 거부 [현재] |
| 주요 환경변수 | `DATABASE_URL`, `REDIS_URL`, `SESSION_SECRET_KEY`, AI 제공자 키, `GITVIZ_USER_DATA_DIR`, `GITVIZ_AGENT_COMMAND` | `GITVIZ_TEST_DATABASE_URL`, `GITVIZ_ENV=test` | `GITVIZ_SIGN_PFX`, `GITVIZ_SIGN_PFX_PASSWORD`(선택), `GITVIZ_VERIFY_*`(검증용) | 없음(`GITVIZ_SERVER_ORIGIN`은 비패키지 실행에서만 허용 [R1]) | `DATABASE_URL`, `REDIS_URL`, `SESSION_SECRET_KEY`, AI 키 등 |
| DB 연결 | 개발 PostgreSQL(compose). `GITVIZ_ENV=development` + 허용 호스트 목록 가드 [R1] | `gitviz_test*` 이름의 DB만. 이름 규칙 불일치 시 거부 [현재: 파일별 가드 → R1: 단일 가드] | 사용 안 함 | 사용자별 SQLite(`workspace.sqlite3`, 스키마 v8) | 운영 PostgreSQL. 마이그레이션은 사람이 `alembic upgrade head` |
| 로그 위치 | 콘솔 + `build-output/dev-data/logs/` [R1] | 테스트 출력, 임시 폴더 | `build-output/*/` 내부 로그 | `%LOCALAPPDATA%\GitViz\Workspace\logs` [R3] (현재: userData 하위) | 호스트 표준 출력 → 수집기 |
| 데이터 저장 위치 | `build-output/dev-data/` [R1]. 현재는 `%APPDATA%\gitviz-workspace`를 운영과 공유할 수 있음 | `os.tmpdir()/gitviz-test-<pid>`, 종료 시 삭제 [현재: 데스크톱 E2E는 이미 임시 프로필] | — | `%LOCALAPPDATA%\GitViz\Workspace\{data,cache,logs,backups}` [R3] | `storage/repos/`(작업 디렉터리 상대) → 설정화 [R1 후속] |
| 임시 파일 | `%TEMP%\GitViz\` | 임시 폴더 하위 | `build-output/*/work` | `%TEMP%\GitViz\` | 호스트 임시 |
| 실행 명령 | 서버: `python -m uvicorn app.main:app`, 워커: `python -m celery -A app.core.celery_app:celery worker --pool=solo`, 데스크톱: `npm --prefix desktop run build` 후 `npm --prefix desktop start` | — | — | 시작 메뉴 `GitVizWorkspace` | 배포 방식 미정의(범위 밖) |
| 빌드 명령 | — | — | 에이전트: `python tools/build/build_workspace_agent.py` [S4]; 데스크톱: `npm --prefix desktop run build` → `npm --prefix desktop run make` | — | — |
| 테스트 명령 | `python tests/server/test_<이름>.py` [S6] | CI 명령 그대로(경로만 치환): `backend-validation.yml`; `python -m unittest discover -s tests/workspace`; `npm --prefix desktop test`; `pwsh tools/validation/validate_workspace_windows.ps1` [S5] | 동일 + 빌드 검증(ASAR 허용 목록 [R2]) | 클린 VM 설치·제거 | 스테이징 서버(`tools/vm/staging_*.ps1`) |

> `celery -A app.core.celery_app:celery` 모듈 경로와 uvicorn 대상은 현재 코드 기준이다. 4차는 이 이름을 바꾸지 않는다.

## 3. 운영에서 개발 경로가 참조되지 않도록 하는 검증 전략

| # | 위험 | 방어 | 시점 |
|---|---|---|---|
| V1 | 개발자가 `.env`로 운영 DB에 접속 | `assert_database_allowed(env, url, intent)` 단일 가드를 서버·Alembic·모든 `tools/ops` 진입점에서 호출. 운영 호스트는 허용 목록 밖이면 `development`에서 거부 | R1 |
| V2 | 테스트가 개발/운영 DB를 지움 | 테스트는 DB 이름이 `gitviz_test`로 시작하지 않으면 시작 거부(현재 파일별 → 공통 모듈) | R1 |
| V3 | 패키징된 앱이 개발 경로(`GITVIZ_AGENT_COMMAND`, `GITVIZ_USER_DATA_DIR`, `GITVIZ_SERVER_ORIGIN`)를 따름 | `app.isPackaged`이면 위 변수를 무시하고 로그에 경고 없이 고정값 사용 | R1 |
| V4 | 빌드 산출물에 `.env`·비밀·개발 경로 포함 | 패키지 후 `app.asar` 항목을 **허용 목록**으로 검사하고 `.env*`, `typescript`, `dist/tests`, `*.map` 발견 시 실패 | R2 |
| V5 | 설치본이 `--remote-debugging-port` 등 디버그 인수를 수용 | 패키징된 exe에서 해당 인수 차단 | R2 |
| V6 | 운영 서버가 `.env`를 읽어 개발 값 사용 | `GITVIZ_ENV=production`이면 `load_dotenv` 호출 안 함 | R1 |
| V7 | 로그·백업·오류에 비밀값 기록 | 로그 필터 + 비밀 값 패턴 테스트 | R1 |
| V8 | 구조 변경 중 경로 하드코딩 잔존 | `git grep -n "scripts/\|desktop/out\|dist/workspace-agent" -- . ':!docs/archive' ':!.validation'` 결과가 0건 | S9 종료 시 |

## 4. 표준 실행 순서 요약

1. **개발 시작:** `python -m venv .venv` → `.venv\Scripts\python -m pip install -r requirements.txt` → `docker compose up -d db redis` → `alembic upgrade head` → uvicorn.
2. **검증:** 서버 테스트(테스트 DB) → `tests/workspace` → `desktop` 빌드·단위·E2E → `validate_workspace_windows.ps1`.
3. **릴리스 빌드:** 잠금 환경(`build-output/venv-agent`)에서 에이전트 동결 → `npm ci` → `npm run make` → SHA-256 기록 → 클린 VM 설치·제거 검증 → 승인 후 업로드.
