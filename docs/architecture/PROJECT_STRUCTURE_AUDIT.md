# 프로젝트 구조 감사 (1차 · 읽기 전용)

작성일: 2026-10-30 · 기준 커밋: `65f1b62` (`feat/p08-workspace-inbox`) · [문서 안내](../index.md)

이 문서는 1차 분석 5종 중 하나다. 파일을 이동·삭제·수정하지 않고 읽기만 해서 작성했다. 다른 문서: [현재 아키텍처](CURRENT_ARCHITECTURE.md) · [의존성 분석](DEPENDENCY_ANALYSIS.md) · [제안 아키텍처](PROPOSED_ARCHITECTURE.md) · [이동 후보](FILE_MIGRATION_CANDIDATES.md)

표기: **확인**은 파일·설정·Git에서 직접 확인한 사실이고, **추정**은 근거에 기반한 판단이다. "참조 없음"은 추적 중인 텍스트 파일에서 이름이 발견되지 않았다는 뜻이며 "사용되지 않음"의 증거가 아니다(수동 실행 도구일 수 있다).

## 1. 분석 시작 시점의 Git 상태

| 항목 | 값 |
|---|---|
| 브랜치 / HEAD | `feat/p08-workspace-inbox` / `65f1b62` (총 200 커밋, 최초 커밋 2026-03-19) |
| 추적 파일의 미커밋 변경 | 없음 |
| 미추적 파일 | `Claude outputs/` 아래 보고서 5개 (커밋 대상 아님) |
| 추적 중인 `Claude outputs/` 파일 | 1개 (`GitViz_QA_단계A_감사보고서_2026-10-27.md`) — 아래 §3.15 |
| `.env` | 루트에 존재(1,564 B). **읽지 않았다.** `.gitignore`로 제외됨. 변수 이름은 `.env.example`과 소스 코드에서만 확인했다 |
| 추적 파일 수 | 642개. 최상위 분포: `app` 168, `scripts` 113, `frontend` 96, `docs` 97, `desktop` 84, `tests` 40, `migrations` 22, `contracts` 5 |

분석 중 만든 보조 스크립트는 모두 세션 임시 폴더에 있고 프로젝트 안에는 없다. 프로젝트에서 새로 생긴 파일은 `docs/architecture/`의 이 문서 5개뿐이다.

## 2. 디스크 상의 규모 (추적 여부와 별개)

| 경로 | 파일 수 | 크기 | Git 추적 |
|---|---:|---:|---|
| `desktop/` | 19,261 | 5.5 GB | 84개만 추적. `node_modules/` 527 MB, `out/` 4.9 GB, `dist/` 4 MB는 `desktop/.gitignore`로 제외 |
| `.validation/` | (대량) | **58 GB** | 전부 제외. `p01-vm` 27 GB, `p08-vm` 20 GB, `iso` 8.2 GB, 그 외 p03~p09 증거 |
| `venv/` | 10,649 | 144 MB | 제외 (Python 3.12.10) |
| `storage/` | 727 | 53 MB | 제외 (`storage/repos/`) |
| `dist/` | 134 | 42 MB | 제외 (PyInstaller 산출물) |
| `build/` | 17 | 18 MB | 제외 (PyInstaller 작업 폴더) |
| `outputs/` | 1 | 15 MB | 제외 (발표 자료 pptx 1개) |
| `docs/` | 97 | 1.4 MB | 추적 |
| `frontend/` | 96 | 3.1 MB | 추적 |
| `app/` | 319 | 3.1 MB | 추적 168개 (나머지는 `__pycache__`) |
| `scripts/` | 143 | 2.7 MB | 추적 113개 |

코드 규모(추적 파일 줄 수): `app` Python 20,584줄(그중 `app/workspace` 5,225줄), `scripts` Python 17,259줄 + PowerShell 1,597줄 + CJS 1,381줄, `tests` Python 3,884줄, `frontend` JS 8,268줄 + CSS 1,454줄 + HTML 942줄, `desktop/src` TS 2,444줄, `desktop/tests` TS 3,377줄, `docs` Markdown 7,757줄.

## 3. 폴더별 분석

각 폴더에 대해 역할, 구성, 의존 관계, 중복·미사용 후보, 이동 가능 여부, 유지 이유, 권장 위치를 적는다. 의존 관계의 근거는 [의존성 분석](DEPENDENCY_ANALYSIS.md)에 있다.

### 3.1 `app/` — Python 서버와 워크스페이스 에이전트의 소스

- **역할(확인):** 하나의 `app` 패키지에 **서로 다른 두 배포 단위**가 들어 있다.
  1. 웹 서버 + Celery 워커: `app/main.py`(FastAPI)와 `api`, `routers`, `services`, `collectors`, `tasks`, `models`, `schemas`, `core`, `database.py`, `desktop_auth_middleware.py`, `utils`.
  2. 데스크톱 워크스페이스 에이전트: `app/workspace/` (39개 파일, 5,225줄). PyInstaller로 `gitviz-workspace-agent.exe`가 되어 설치 프로그램에 포함된다.
- **구성:** `api` 19개 파일(2,754줄), `collectors` 18(2,245줄), `core` 3(309줄), `models` 25(1,210줄), `routers` 2(175줄), `schemas` 4(1,053줄), `services` 40(5,934줄, AI 하위 포함), `tasks` 6(1,359줄), `utils` 1(89줄), `workspace` 39(5,225줄).
- **의존 관계:** `app/workspace`는 서버 쪽 코드를 거의 참조하지 않는다. `app.services.ai.*` 7개 모듈(정적 import 2개 `token_budget`·`providers.base`, `importlib` 동적 로드 5개 `content_policy`·`input_builder`·`validators`·`providers.registry`·`difftext`)만 공유한다. 이 경계는 `tests/workspace/test_import_boundary.py`가 검사한다(에이전트 import가 `app.main`, `sqlalchemy`, `celery`, `dotenv`를 로드하면 실패).
- **중복·미사용 후보(확인):**
  - 데이터베이스 설정이 둘이다: `app/core/database.py`(동기, `DATABASE_URL`, `Base`), `app/database.py`(비동기, `DATABASE_URL_ASYNC`, 별도 `Base`). 모델 25개 중 `user.py`만 비동기 `Base`를 쓴다고 `app.models → app.database` 간선 1개로 확인했다.
  - `app/routers/`(auth 1개)와 `app/api/`가 모두 라우터 계층이다. 팀별로 만든 흔적이 `app/main.py` 주석(`data-col 팀`, `feat/login 팀`)에 남아 있다.
  - `app/api/snapshot_route.py`(33줄)와 `app/api/snapshots.py`(45줄)는 이름이 비슷하지만 역할이 다르다(전자는 `analytics` 라우터의 커스텀 `APIRoute`, 후자는 스냅샷 목록 라우터). 중복이 아니다.
  - **참조 없음:** `app/services/github_service.py`(28줄, `GitHubService`)와 `app/services/ai/markdown_safety.py`(157줄)는 소스·문서·설정 어디에서도 이름이 발견되지 않는다.
  - `app/tasks/ai_maintenance.py`는 Python import가 없지만 `app/core/celery_app.py`의 `include=[…]` 문자열로 로드되므로 **사용 중**이다.
  - 소스가 없는 `__pycache__` 잔여물: `app/api/ai/__pycache__/{handover,teams}`, `app/schemas/__pycache__/ai_team`, `app/services/ai/__pycache__/{handover_draft,handover_service,team_access,team_report_service,team_service}`와 Python 3.14 pyc. 과거에 있던 모듈의 흔적이며 Git 추적 대상이 아니다.
- **이동 가능 여부:** 낮음. 모든 `import app.…` 경로, Alembic(`migrations/env.py`), Celery 포함 목록, `workspace_agent.spec`의 `excludes`/`hiddenimports`, Docker compose 명령(`app.core.celery_app.celery`), CI가 패키지 이름 `app`에 의존한다.
- **유지 이유:** 서버와 에이전트가 모두 여기서 빌드된다.
- **권장 위치:** 패키지 이름은 유지한다. 내부에서 `workspace`를 별도 최상위 영역으로 분리하는 안은 [제안 아키텍처](PROPOSED_ARCHITECTURE.md)에서 다룬다.

### 3.2 `desktop/` — Electron 앱

- **역할(확인):** Windows 데스크톱 셸. 메인 프로세스(`src/main.ts`), 보안 preload(`src/preload.ts`), 에이전트 자식 프로세스 관리(`src/agent.ts`), 원격 서버 통신(`src/remote*.ts`, `src/cloud-*.ts`), 설치 이벤트 처리(`src/squirrel.ts`), 업데이트 정책(`src/update.ts`).
- **구성:** 추적 84개 — `src` 30, `tests` 41(단위 `*.test.ts`, E2E `*.e2e.ts`, VM 스크립트 `*.ps1`), `ui` 3, `contracts/workspace.ts`, `scripts/{build,audit-bundle}.mjs`, `forge.config.ts`, tsconfig 2개, ESLint 설정.
- **의존 관계(확인):** 코드가 저장소 바깥 폴더를 **하드코딩 상대 경로**로 읽는다.
  - `scripts/build.mjs`: `../contracts/workspace/v1`, `../contracts/desktop/v1/ai-bodies.json`, `../frontend/pages/workspace.html`, `../frontend/js/workspace/*.js`(16개 파일을 이름으로 나열), `../frontend` 전체(`dist/ui/web`로 복사).
  - `forge.config.ts`: `extraResource: ../dist/workspace-agent/agent`.
  - `npm run typecheck:agent`: `../pyright-workspace.json`.
- **중복·미사용 후보:** `forge.config.ts`에 환경변수 `GITVIZ_VERIFY_P03/P04/P05/P06/P07/P08B/PACKAGE`로 전환되는 **검증용 패키지 변형 8개**가 있다. 각각 `dist/tests/*.e2e.js`를 메인으로 바꿔 별도 앱 이름(`GitVizWorkspaceP0xVerify`)으로 패키징한다. `desktop/out/`에는 이 검증용 패키지 7개와 설치 프로그램 산출물 이전 빌드 4개(`make-0.7.0-*`)가 쌓여 있다(합계 4.9 GB).
- **이동 가능 여부:** 낮음. 위 하드코딩 경로가 바뀌면 빌드가 깨진다.
- **유지 이유:** 제품 본체.
- **권장 위치:** 현 위치 유지. 대신 빌드 입력 경로를 한곳(설정)으로 모으는 개선을 권장한다.

### 3.3 `frontend/` — 정적 웹 UI (프레임워크 없음)

- **역할(확인):** 바닐라 JS/CSS/HTML. 두 곳에서 같은 파일을 쓴다.
  1. 웹: `app/main.py`가 `directory="frontend"`를 `/`에 마운트한다(**서버 실행 위치의 상대 경로**).
  2. 데스크톱: `desktop/scripts/build.mjs`가 `dist/ui/web`로 통째로 복사하고, `workspace.html`과 `js/workspace/*.js`를 `dist/ui`에 개별 복사한다.
- **구성:** `js` 28개(그중 `workspace/` 13, `ui/` 6, `platform/` 3), `pages` 10개 + `components` 2개, `css` 11개, `img` 6개, `vendor`(Chart.js 4.4.1, D3 7.8.5, Font Awesome 6.5.2).
- **서버 통신:** 웹은 same-origin `fetch`(`/api/auth/logout`, `/repositories/...`). 데스크톱은 `js/platform/transport.js`가 요청을 타입 있는 엔드포인트 데이터로 바꿔 `window.desktop.remote.request`로 메인 프로세스에 전달한다(`isDesktop()`).
- **중복·미사용 후보:** `frontend/js/workspace/status.js`는 데스크톱 빌드에서 `git-status.js`로 이름이 바뀌어 복사되고, `desktop/ui/status.js`(헬스 표시)는 별개 파일이다. 같은 이름 `status.js`가 서로 다른 두 파일이라 혼동 위험이 있다.
- **이동 가능 여부:** 낮음~중간. 서버 마운트 경로, 빌드 스크립트, CI 경로 필터(`frontend/**`), 문서가 참조한다.
- **권장 위치:** 현 위치 유지. 웹 전용과 워크스페이스 전용을 폴더로 나누는 안은 [제안 아키텍처](PROPOSED_ARCHITECTURE.md) 참고.

### 3.4 `contracts/` — 프로세스 간 계약 (JSON Schema)

- **구성(확인):** `workspace/v1/{bootstrap,request,response}.schema.json` + `fixtures.json`, `desktop/v1/ai-bodies.json` — 총 5개.
- **소비자:** 에이전트(`workspace_agent.spec`의 `datas`로 번들), 데스크톱 빌드(`dist/schema`로 복사, Ajv 검증), `tests/workspace/test_contracts.py`, `desktop/contracts/workspace.ts`(수동으로 맞춘 TS 타입).
- **중복 후보:** `desktop/contracts/workspace.ts`는 스키마에서 생성되지 않고 손으로 유지된다고 추정한다(생성 스크립트를 찾지 못함).
- **이동 가능 여부:** 중간. 4곳이 경로를 직접 참조한다.
- **유지 이유:** 에이전트와 데스크톱 사이의 단일 진실 원천.
- **권장 위치:** 유지.

### 3.5 `migrations/` — Alembic

- **확인:** 리비전 18개, **헤드 1개**(`b7e1d9c3a502`), 루트 1개(`621f692dc64b`). 체인은 선형이다.
- `migrations/schema_20261002.py`(118줄)는 "앱 모델을 참조하지 않는 수동 스키마 변경"으로, 리비전 4개(`621f692dc64b`, `d1e2f3a4b5c6`, `e2f3a4b5c6d7`, `f3a4b5c6d7e8`)가 사용한다. 과거 리비전에서 빠진 선행 테이블을 보충한다.
- 리비전 파일 이름 중 `84b735252333_.py`, `c7b4b1247f3b_.py`는 설명(slug)이 비어 있다.
- `migrations/env.py`는 `.env`의 `DATABASE_URL`(동기)과 `app.core.database.Base` + `app.models.*`를 쓴다. `users` 테이블은 비동기 `Base`가 관리하므로 autogenerate에서 제외한다.
- **이동 가능 여부:** 낮음. `alembic.ini`의 `script_location = %(here)s/migrations`와 `prepend_sys_path = .`에 의존한다.
- **권장 위치:** 유지.

### 3.6 `scripts/` — 성격이 다른 도구 107개의 혼합

루트 직속 107개 + `fixtures/` 6개. 이름 접두어와 참조 관계로 분류했다.

| 분류 | 개수 | 예 | CI 참조 |
|---|---:|---|---|
| 백엔드 테스트(스크립트형) | 35 | `test_ai_*.py`, `test_desktop_auth.py`, `test_analytics_calculations.py` | **CI가 경로로 직접 호출** |
| 테스트 지원 모듈 | 약 11 (`ai_*.py`) | `ai_test_support.py`, `ai_credential_support.py`, `ai_fake_provider.py` | 테스트가 import |
| 프런트 테스트(Node) | 4 | `test_*.cjs` | 일부 CI |
| 일회성 데이터 이관 | 8 | `migrate_*.py` | 없음 |
| 데이터 백필·정리 | 7 | `backfill_*.py`, `cleanup_*.py` | 없음 |
| 점검·조회·데모 | 약 10 | `check_*.py`, `inspect_*.py`, `show_summary.py`, `demo_*.py` | 없음 |
| 통합 검증 | 약 8 | `verify_*.py`, `smoke_ai_live.py` | 일부 |
| Windows 검증·VM 도구 | 19 | `validate_p0x_*.ps1`, `staging_*.ps1`, `p01_*.ps1`, `install_p01_vm.ps1` | `validate_workspace_windows.ps1`만 CI |
| 빌드 | 2 | `build_workspace_agent.py`, `audit_workspace_agent.py` | CI 간접 |

- **확인:** 루트 107개 중 CI 워크플로가 경로로 참조하는 파일은 39개다. 워크플로 3개가 `scripts/…`를 총 40회(고유 37개) 언급한다.
- **참조 없음 32개** (이름 기반 재검증 후): `backfill_commit_type/pr_author_login/pr_large_flag`, `check_connections/date_formats/job/resolution_progress`, `cleanup_duplicates/repo10/test_repo`, `demo_incremental_read`, `inspect_duplicates`, `migrate_*` 7개, `run_pr_authorship`, `show_summary`, `staging_vm.ps1`, `test_pr_detail_collection`, `trigger_async`, `validate_p04_package/p06_package/p06_postgres.ps1`, `verify_analysis_sql/other_repo/schema_v2`(이 셋은 `Claude outputs`의 QA 보고서에서만 언급), `ai_integration_stack`, `ai_ui_mock_server`. 상세는 [이동 후보](FILE_MIGRATION_CANDIDATES.md)에 있다.
- **구조적 문제:** 테스트(`scripts/test_*`)와 운영 도구와 검증 증거 수집기가 한 폴더에 있어, 이 폴더의 파일 이동은 곧 CI 파괴다.
- **이동 가능 여부:** 테스트·CI 참조 파일은 낮음, 참조 없음 파일은 높음.

### 3.7 `tests/` — 에이전트 단위 테스트

- **확인:** 40개 파일이 모두 `tests/workspace/`(unittest, 3,884줄)에 있다. 실행은 `python -m unittest discover -s tests/workspace`(Windows 검증 스크립트 `validate_workspace_windows.ps1`). 서버 쪽 테스트는 `tests/`가 아니라 `scripts/test_*.py`에 있다.
- **이동 가능 여부:** 중간. `pyright-workspace.json`, `ruff-workspace.toml` 대상 경로와 CI 경로 필터(`tests/workspace/**`)가 참조한다.

### 3.8 `docs/` — 문서

- **확인:** 97개 추적(한글 파일명 4개 포함), 하위: `architecture` 2(+이번 5), `archive`(원문 보존, `-text` 속성), `audit`, `contracts`, `decisions`, `migration`(+`validation`), `operations`, `product`, `ux-redesign`, `validation`.
- **CI 제약(확인):** `docs/audit/check_documents.py`가 **`docs/index.md`에서 도달할 수 없는 문서를 오류로 처리**한다. 이번 5개 문서는 `docs/index.md`에 아직 링크되지 않았으므로 문서 CI가 실패할 수 있다(§6).
- **이동 가능 여부:** 낮음. 보존 기준선과 이동 경로표(`relocation-map.json`)가 있다.

### 3.9 `build/`, `dist/` — 에이전트 빌드 산출물

- **확인:** 둘 다 `.gitignore`의 `build/`, `dist/` 패턴으로 제외된다. `scripts/build_workspace_agent.py`가 `dist/workspace-agent`(출력)와 `build/workspace-agent`(작업)를 쓰고, `workspace_agent.spec`이 `build/workspace-agent/entry.py` 진입 shim을 **빌드 때마다 생성**한다.
- `dist/workspace-agent/agent/gitviz-workspace-agent.exe`(2026-10-29 00:25 빌드)는 `app/workspace`, `contracts/workspace`, `app/services/ai` 아래 어떤 파일보다도 늦다. 번들 대상 소스가 exe보다 새로운 경우는 **없다**(확인).
- 데스크톱 `forge.config.ts`가 `../dist/workspace-agent/agent`를 읽으므로, 이 폴더 이름과 위치가 데스크톱 패키징의 암묵적 계약이다.
- **이동 가능 여부:** 중간(경로 두 곳 + spec + 스크립트를 함께 바꿔야 한다).

### 3.10 `outputs/`

PowerPoint 1개(15 MB, 캡스톤 발표). `.gitignore`의 `/outputs/`로 제외. 코드에서 참조하지 않는다. 저장소 밖 보관을 권장(추정).

### 3.11 `reports/`, `test-react/`

둘 다 **파일이 0개인 빈 폴더**다(`reports` 2026-10-07, `test-react` 2026-06-18 생성). Git은 빈 폴더를 추적하지 않는다. 소스·문서·설정에서 참조하지 않는다. 삭제 검토 대상이지만 이번 단계에서는 건드리지 않았다.

### 3.12 `certs/`

`supabase-root.crt` 1개. `.gitignore`의 `/certs/`로 제외. 내용은 열지 않았다. `DATABASE_SSL_CA` 환경변수가 가리키는 사설 CA 경로일 것으로 **추정**한다(코드는 경로를 환경변수로만 받는다).

### 3.13 `.github/`

워크플로 3개: `backend-validation.yml`(문서 검사 + Postgres/Redis 서비스 컨테이너로 백엔드 테스트 약 37단계 + AI 큐 통합), `windows-desktop.yml`(경로 필터 + 고정 픽스처 검증, Windows 러너), `windows-release.yml`(수동 실행, 서명 없는 설치 프로그램 빌드, 게시 없음). 경로를 직접 적으므로 폴더 이동의 1차 영향 지점이다.

### 3.14 `.claude/`, `.validation/`, `.ruff_cache/`

- `.claude/`: `launch.json`, `settings.local.json`. Git 추적 안 됨(전역 gitignore `C:\Users\user\.gitignore_global`의 `.claude`). 개인 도구 설정으로 보인다.
- `.validation/`: **58 GB**의 검증 증거·VM 디스크·임시 venv(`venv-audit`, `venv-workspace`)·로그·스크린샷. `.gitignore`로 제외. 일부 문서가 이 경로의 증거를 인용하므로(추정) 삭제 전 확인이 필요하다. CI는 `.validation/venv-workspace`를 만들어 쓰고 `pyright-workspace.json`도 이 venv를 가리킨다 — 폴더 이름이 도구 설정에 박혀 있다.
- `.ruff_cache/`: 캐시. 안전하게 재생성된다.

### 3.15 `Claude outputs/`

- 디스크에 6개, 그중 1개(`..._단계A_감사보고서_2026-10-27.md`)는 **이미 커밋**되어 있고 5개는 미추적이다. 세션 지침상 이 폴더는 커밋 대상이 아니므로 추적 중인 1개가 의도된 것인지 확인이 필요하다.
- 코드 참조는 없다. 문서 CI의 보존 기준선에도 포함되지 않는다(`docs/` 밖).
- **권장:** 저장소 밖 또는 `.gitignore` 대상으로 이전(결정 필요).

### 3.16 `venv/` (루트)

`pyvenv.cfg`: Python 3.12.10, 144 MB, `.gitignore`의 `venv/`로 제외. 같은 저장소에 `.validation/venv-*`가 별도로 있어 가상환경이 3곳이다. `app/__pycache__`에는 Python 3.14 pyc도 있다(과거에 3.14로 실행한 흔적, 추정).

### 3.17 `storage/`

`storage/repos/<id>/`에 분석 대상 저장소 clone이 727개 파일(53 MB). `.gitignore`의 `storage/repos/`로 제외. 서버의 `clone_manager.py`가 쓰는 **런타임 데이터 경로**이며 서버 작업 디렉터리 기준 상대 경로다. 주의: clone된 저장소(`storage/repos/2`)에 GitViz 자신의 `migrations/` 사본이 있어, 저장소 전체를 대상으로 `grep`하면 중복 결과가 나온다.

### 3.18 루트 설정 파일

| 파일 | 역할 | 비고 |
|---|---|---|
| `alembic.ini` | Alembic 설정 | `sqlalchemy.url`은 비어 있고 `env.py`가 `.env`에서 읽는다 |
| `docker-compose.yml` | Postgres 16, Redis 7, `ai` 프로필의 AI worker/dispatcher | 개발용 비밀번호 `gitviz/gitviz`가 compose에 평문. 로컬 전용 |
| `requirements.txt` | 서버 의존성 (고정 버전 대부분, `requests`만 미고정) | `httpx2`, `httpcore2` 같은 비표준 패키지명 포함 — 의도 확인 필요 |
| `requirements-workspace.txt` / `.lock` / `-build.txt` | 에이전트 전용 (jsonschema, cryptography 50.0.2, pydantic) / 해시 잠금 / PyInstaller·ruff | 서버와 `cryptography` 버전이 다름(48.0.0 vs 50.0.2) |
| `workspace_agent.spec` | PyInstaller | 서버 모듈을 `excludes`로 차단 |
| `pyright-workspace.json`, `ruff-workspace.toml` | 에이전트 코드만 대상 | 서버 코드는 정적 분석 설정 없음 |
| `.env.example` | 변수 이름·설명 | 61개 변수 |
| `README.md` | 실행 안내 | 문서 안내로 연결 |

## 4. 구조적 문제 요약

1. **두 배포 단위가 한 패키지에 섞임:** 서버(`app/*`)와 에이전트(`app/workspace`)가 같은 `app` 패키지다. 경계는 테스트와 spec의 `excludes`로만 지켜진다.
2. **`scripts/`가 테스트·운영·검증·빌드의 혼합 폴더:** CI가 경로를 직접 호출해 정리가 어렵다.
3. **데이터베이스 접근 이중화:** 동기/비동기 엔진·`Base`가 둘이고 `users`만 비동기다.
4. **프런트 자산 결합:** 데스크톱 빌드가 `frontend`의 파일을 이름으로 나열해 복사한다. 새 워크스페이스 JS를 추가하면 `build.mjs`를 같이 고쳐야 한다.
5. **환경 설정 분산:** `os.getenv`가 `app`에서 48곳(AI 설정 제외), `load_dotenv`가 5개 모듈에 있다. 중앙 설정 객체는 `app/core/ai_settings.py`뿐이다.
6. **관측성 부족:** 160개 파일 중 `logging`을 쓰는 파일은 5개, `print(`가 78회다.
7. **빌드 산출물·증거가 저장소 폴더 안에 쌓임:** `.validation` 58 GB, `desktop/out` 4.9 GB.
8. **계층 역참조:** `services → api`(지연 import 3곳), `core → services`(2), `api → routers`의 비공개 함수(`_get_session_user_id`) 직접 import.

## 5. 위험도 평가

| 항목 | 위험도 | 이유 |
|---|---|---|
| `app` 패키지 이름 변경 | **높음** | 전 영역이 의존 |
| `scripts/` 재배치 | **높음** | CI 40곳, Windows 검증 스크립트 |
| `dist/`·`build/` 위치 변경 | **높음** | spec, 빌드 스크립트, forge 설정이 동시에 바뀌어야 함 |
| `contracts/` 이동 | 중간 | 4곳 직접 참조 |
| `frontend/` 이동 | 중간 | 서버 마운트·빌드 스크립트·CI |
| 참조 없는 일회성 스크립트 정리 | 낮음 | 단, 수동 실행 도구일 수 있음 |
| 빈 폴더·pptx·stale pyc | 낮음 | 코드 참조 없음 |

## 6. 이 문서군 자체가 만드는 CI 영향

`docs/audit/check_documents.py`는 `docs/index.md`에서 도달할 수 없는 문서를 오류로 본다. 이번에 추가한 5개 문서는 서로 링크하지만 `docs/index.md`는 수정하지 않았다(읽기 전용 원칙). 따라서 이 상태로 푸시하면 `Backend validation` 워크플로의 `docs` 작업이 실패할 수 있다. 해결은 `docs/index.md`에 이 문서군 링크를 한 줄 추가하는 것이며, 사용자 승인 후 2차 작업에서 처리하는 것을 권장한다.
