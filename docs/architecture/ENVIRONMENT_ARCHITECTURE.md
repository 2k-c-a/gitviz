# 환경 분리 아키텍처 (2차 · 설계만, 적용 없음)

작성일: 2026-10-30 · 기준 커밋: `65f1b62` · [문서 안내](../index.md)

선행 문서(1차): [구조 감사](PROJECT_STRUCTURE_AUDIT.md) · [현재 아키텍처](CURRENT_ARCHITECTURE.md) · [의존성 분석](DEPENDENCY_ANALYSIS.md) · [제안 아키텍처](PROPOSED_ARCHITECTURE.md) · [이동 후보](FILE_MIGRATION_CANDIDATES.md)
같은 2차 문서: [개발 환경 설정](../development/DEVELOPMENT_SETUP.md) · [빌드와 릴리스](../deployment/BUILD_AND_RELEASE.md) · [설치 안내](../deployment/INSTALLATION_GUIDE.md) · [데이터 저장 정책](../deployment/DATA_STORAGE_POLICY.md)

이 문서는 **설계**다. 파일 이동·코드 수정·빌드·설치를 하지 않고 읽기만 해서 작성했다. 표기: **확인**(코드·설정·실행 기록으로 확인), **추정**(근거 있는 판단), **제안**(아직 구현되지 않은 설계), **결정 필요**(사용자 판단 대기).

1차 문서 5개는 모두 존재했고 실제 파일 상태와 일치했다. 한 가지 정정이 있다: `scripts/ai_ui_mock_server.py`는 추적 파일에서 참조가 없지만, 로컬 미추적 설정 `.claude/launch.json`이 실행 대상으로 등록하고 있다("참조 없음 ≠ 미사용"의 사례).

## 1. 요약

| 환경 | 주체 | 지금 | 목표 |
|---|---|---|---|
| Development | 개발자 PC | 운영 앱과 **같은 사용자 데이터 폴더**를 쓸 수 있고, 단일 `.env`에 DB가 무엇인지 코드로 막는 장치가 없다 | 개발 전용 DB·데이터 폴더·자격 증명. 운영 값과 코드 수준으로 분리 |
| Test | 개발자 PC / CI | 테스트 DB 가드가 일부 파일에만 있고 데스크톱 E2E는 임시 프로필을 쓴다(좋음) | 모든 DB 진입점이 같은 가드를 사용. 임시 데이터 폴더가 기본 |
| Build | CI(기준) / 개발 PC(검증용) | 잠금 파일은 있으나 빌드 스크립트가 "어떤 Python이든" 실행한다. 설치 파일 `asar`에 TypeScript와 E2E 테스트 코드가 포함된다 | 깨끗한 환경에서 잠금 의존성만으로 재현. 운영에 필요한 파일만 포함 |
| Production (클라이언트) | 사용자 PC | per-user 설치(`%LOCALAPPDATA%\GitVizWorkspace`), 데이터는 `%APPDATA%`(Roaming) | 설치와 데이터를 분리하고 데이터는 Local 쪽으로. 로그·백업 정책 추가 |
| Production (서버) | 운영 인프라 | 배포 정의 없음(Dockerfile 없음, compose는 개발용) | 이번 범위 밖. 클라이언트가 의존하는 경계(접속 원점·비밀 분리)만 정의 |

핵심 결정(§3에서 근거를 설명):
1. **설치 위치는 `%ProgramFiles%`가 아니라 현재의 per-user 위치를 유지한다.** 일반 사용자 권한 요구와 충돌하기 때문이다. 전사 배포가 필요하면 별도 MSI를 추가한다.
2. **사용자 데이터는 설치 폴더 밖, `%LOCALAPPDATA%` 아래 별도 폴더로 옮긴다.** 단, 제거 후 정리 로직이 `%LOCALAPPDATA%\GitVizWorkspace` 전체를 지우므로 그 하위에 두면 안 된다. (**결정 필요**: 첫 공개 릴리스 전에 정해야 이전 비용이 낮다.)
3. **운영 서버 접속 원점은 빌드 시 설정으로 내장하고(비밀 아님), 비밀은 어떤 환경의 클라이언트에도 넣지 않는다.**
4. **DB 보호는 환경 판별 변수 + 단일 가드 모듈로** 모든 진입점(서버, Alembic, 스크립트, 테스트)에 적용한다.

## 2. 환경별 역할과 현재 상태

### 2.1 Development

- **확인:** 가상환경이 위치별로 흩어져 있다. 루트 `venv/`(Python 3.12.10, `.claude/launch.json`이 절대 경로로 사용), README 안내의 `.venv`, 에이전트용 `.validation/venv-workspace`, 감사용 `.validation/venv-audit`.
- **확인:** 웹 개발은 `docker compose up -d db redis` → `python -m alembic upgrade head` → `python -m uvicorn app.main:app` → 별도 터미널 Celery worker. 프런트엔드는 빌드 단계가 없고 서버가 `frontend/`를 직접 서빙한다.
- **확인:** 데스크톱 개발은 `npm --prefix desktop run build` 후 `npm start`(= `electron .`). Electron 앱 이름이 `package.json`의 `gitviz-workspace`이고 `app.setPath`를 호출하는 코드가 없으므로, **개발 실행과 설치된 앱이 같은 `%APPDATA%\gitviz-workspace`를 쓴다**(Electron 기본 동작에서 추론. 설치 앱의 경로는 클린 VM 실행 기록으로 확인).
- **확인:** `.env`는 하나뿐이고(`load_dotenv()`가 6곳에서 각자 호출) 환경을 구분하는 변수가 없다. 개발 `.env`가 운영 DB를 가리켜도 코드는 막지 않는다. 이 PC의 `.env` 내용은 열지 않았으므로 실제 값은 **확인 불가**.

### 2.2 Test

- **확인:** CI `postgres` 작업은 `postgresql://gitviz_test:…@localhost:5432/gitviz_test`를 `GITVIZ_TEST_DATABASE_URL`로 쓴다. 가드는 파일마다 따로 있다: `scripts/ai_test_support.py`의 `require_test_database()`(AI 테스트 계열)와 `scripts/test_backend_improvements.py:23`(DB 이름이 `gitviz_test`로 시작해야 함). 그 외 테스트(`test_desktop_api_compat.py`, `test_ai_security.py`)는 프로세스 환경변수에 테스트용 URL을 **직접 지정**한다.
- **확인:** `load_dotenv()`는 기본적으로 이미 설정된 환경변수를 덮어쓰지 않으므로, 테스트가 `app` import 전에 환경변수를 지정하면 `.env`보다 우선한다. 이 순서가 깨지면 `.env`의 DB가 쓰인다.
- **확인:** 데스크톱 E2E는 `desktop/tests/profile.ts`가 임시 폴더로 `userData`·`sessionData`·`crashDumps`를 바꾸고, 실제 앱 세션을 쓰지 않는다고 명시한다. 이 패턴을 개발 실행에도 적용하는 것이 §5의 제안이다.
- **확인:** 에이전트 테스트(`tests/workspace`)는 임시 디렉터리의 SQLite를 쓴다.

### 2.3 Build

- **확인:** 에이전트: `scripts/build_workspace_agent.py` → `python -I -m PyInstaller workspace_agent.spec` → `dist/workspace-agent/agent`. 데스크톱: `npm --prefix desktop run build`(tsc + `scripts/build.mjs`) → `npm run make`(electron-forge + maker-squirrel) → `desktop/out/make/squirrel.windows/x64/GitVizWorkspaceSetup.exe`.
- **확인(재현성):** Node `>=24.20.0 <25`, 의존성은 `package-lock.json`, Python 의존성은 `requirements-workspace.lock`(해시 고정, CI는 `--require-hashes --only-binary=:all:`), CI는 Python 3.12.10·Node 24.20.0 고정.
- **확인(허점):** 로컬 빌드에서는 `build_workspace_agent.py`가 `sys.executable`을 그대로 쓰며 "잠금 환경인가"를 검사하지 않는다. 또 `forge.config.ts`는 `../dist/workspace-agent/agent`가 **언제 빌드된 것이든** 읽는다.
- **확인(포함 파일):** 현재 패키지 `app.asar`(29 MB, 985개 항목)에는 `node_modules/typescript`(156개 항목, 디스크 24 MB — `package.json`의 `dependencies`에 있음), `dist/tests`(E2E·단위 테스트 38개 항목), `dist/forge.config.js`가 들어 있다. 사용자 PC에는 불필요한 파일이다.
- **확인:** 에이전트 번들은 `VCRUNTIME140*.dll`과 UCRT `api-ms-win-*` DLL을 `_internal`에 포함하므로 별도 VC++ 재배포 패키지가 필요 없다. 코드 서명은 없다.

### 2.4 Production (클라이언트)

- **확인:** Squirrel.Windows, per-user, 관리자 권한 불필요, 설치 폴더 `%LOCALAPPDATA%\GitVizWorkspace\`(`Update.exe`, `app-x.y.z\`, `packages\`), 시작 메뉴·바탕화면 바로가기, `HKCU\…\Uninstall\GitVizWorkspace`. 사용자 데이터 `%APPDATA%\gitviz-workspace\`(Electron userData, 하위 `workspace\`에 SQLite와 DPAPI 키). 자동 업데이트 없음(수동 검증 정책만 있음). 제거 시 사용자 데이터는 보존, 설치 폴더 잔여물은 일회성 예약 작업이 삭제(대화형 로그온 세션에서만 동작 — 커밋 `65f1b62`).
- **확인:** 서버 접속 원점은 프로세스 환경변수 `GITVIZ_SERVER_ORIGIN`(HTTPS)으로만 주어진다(`desktop/src/main.ts`). 설치 프로그램은 이 값을 설정하지 않으므로 일반 사용자 PC에서는 서버 기능이 **꺼진 상태**가 기본이다.

### 2.5 Production (서버)

- **확인:** 저장소에 서버 배포 정의가 없다(Dockerfile 없음, `docker-compose.yml`은 `python:3.12-slim`에 소스를 마운트하고 `pip install`하는 개발용). 운영 DB는 Supabase 풀러로 **추정**(`certs/supabase-root.crt`, `pgbouncer` 대응 코드). 이번 범위는 클라이언트가 의존하는 경계만 다룬다.

## 3. 핵심 설계 결정과 근거

### 3.1 설치 위치: `%ProgramFiles%` 대신 per-user 유지 (**제안**, 결정 필요)

요청서는 `%ProgramFiles%\제품명\`을 기본 검토 대상으로 두었다. 검토 결과:

| 기준 | `%ProgramFiles%\GitViz\` | `%LOCALAPPDATA%\GitVizWorkspace\` (현재) |
|---|---|---|
| 설치·제거·업데이트에 관리자 권한(UAC) | 필요 | 불필요 |
| "일반 사용자 권한으로 정상 작동" 원칙 | 설치만 관리자, 실행은 일반 | 전 과정 일반 사용자 |
| 현재 설치 도구 | Squirrel은 per-user 전용. MSI(`noMsi: true`로 꺼둔 상태) 또는 다른 도구로 교체 필요 | 그대로 |
| 제거 정리·검증 | 새로 설계·검증 | 클린 VM 설치·제거 반복 검증 완료 |
| 다중 사용자 PC | 한 번 설치로 모두 사용 | 사용자마다 설치 |
| 쓰기 보호 | 설치 폴더 변조가 어려움 | 사용자가 설치 폴더를 쓸 수 있음 |
| 코드 서명 없음의 영향 | 없음 | SmartScreen·Smart App Control 영향은 위치와 무관 |

**제안:** 현재 위치를 유지한다. 전사 배포(관리자가 모든 사용자에게 설치)가 요구사항으로 확정되면 `noMsi`를 해제해 MSI를 추가하거나 MSIX를 평가한다. 어느 쪽이든 §3.2의 데이터 위치 분리는 그대로 적용된다.

### 3.2 사용자 데이터: Roaming → Local, 설치 폴더 밖 (**제안**, 결정 필요)

현재 모든 것이 `%APPDATA%\gitviz-workspace\`(Roaming)에 있다(**확인**). Chromium의 캐시·로컬 스토리지·쿠키도 Electron 기본값에 따라 같은 곳이다(Electron 기본 동작, **추정**).

Roaming이 부적절한 이유:
- 워크스페이스 DB(SQLite WAL)는 로밍 프로필·폴더 리디렉션 위에서 파일 잠금이 안전하지 않다.
- DPAPI로 보호한 키(`key.dpapi`)와 DB 안의 암호화 payload는 쌍이다. 에이전트는 시작 때 `secret-check.bin`을 복호화해 키가 맞는지 확인하고 실패하면 `RECOVERY_REQUIRED`로 멈춘다(`dispatcher.py`). 키와 DB가 다른 컴퓨터로 따로 이동하면 데이터를 읽을 수 없다.
- 창 위치 같은 상태와 캐시는 컴퓨터별 값이라 로밍 대상이 아니다.

**제안 경로:** `%LOCALAPPDATA%\GitViz\Workspace\` 아래에 `data\`, `cache\`, `logs\`, `backups\`를 둔다([데이터 저장 정책](../deployment/DATA_STORAGE_POLICY.md)). 중요한 제약: 제거 후 정리(`desktop/src/squirrel.ts`)는 이름이 정확히 `GitVizWorkspace`이고 부모가 `%LOCALAPPDATA%`인 폴더를 통째로 삭제한다. 데이터를 `%LOCALAPPDATA%\GitVizWorkspace\…`에 두면 **제거할 때 사용자 데이터까지 삭제된다.** 위 제안 경로는 이 정리 대상과 겹치지 않는다. `%APPDATA%\gitviz-workspace`는 마이그레이션 원본으로만 남긴다.

### 3.3 환경 판별과 DB 보호 (**제안**)

환경 판별 변수 `GITVIZ_ENV`(`development` | `test` | `production`)를 도입한다(이름은 제안). 모든 DB 진입점이 같은 함수 `assert_database_allowed(env, url, intent)`를 호출한다. 진입점: `app/core/database.py`, `app/database.py`, `migrations/env.py`, 스크립트 전체, 테스트 지원 모듈(`ai_test_support.require_test_database` 등을 이 함수로 대체).

| 환경 | 허용 호스트 | 허용 DB 이름 | 파괴적 작업(삭제·초기화·테스트 데이터 삽입) |
|---|---|---|---|
| development | `localhost`, `127.0.0.1`, `::1`, compose 서비스명 `db` | `gitviz_dev` (`gitviz_test*` 제외) | 허용(스크립트는 DB 이름을 확인 인자로 입력해야 실행) |
| test | 같은 로컬 호스트 | **`gitviz_test`로 시작해야만** | 허용 |
| production | 배포 시크릿 저장소가 지정한 호스트 하나 | 지정된 이름 하나 | **금지** — 삭제·초기화·테스트 삽입 코드는 이 환경에서 즉시 종료 |

부가 장치: (1) 운영 자격 증명은 개발 PC의 어떤 `.env*`에도 두지 않는다(`.env.development`, `.env.test`만 개발 PC에 존재). (2) 운영은 DB 역할을 분리한다: 앱(DML만), 마이그레이션(DDL), 읽기 전용. 개발 계정은 운영 호스트에 방화벽/허용 목록으로 접속이 불가능해야 한다. (3) 파괴적 스크립트(`cleanup_*`, `migrate_*`, `backfill_*` — 현재 확인 결과 **가드 0개**, `.env`의 `DATABASE_URL`을 그대로 사용)는 `tools/ops/`로 옮기면서 가드를 필수로 한다.

### 3.4 구성값과 비밀 분리 (**확인** + **제안**)

| 값 | 클라이언트에 포함 가능? | 위치 |
|---|---|---|
| 서버 접속 원점(HTTPS URL) | 가능(공개 정보) | 빌드 채널 설정 파일(제안). 현재는 환경변수 |
| 앱 버전, 업데이트 릴리스 URL 접두어, 서명 주체(publisher) | 가능 | 빌드 설정 |
| JSON Schema, 엔드포인트 허용 목록 | 가능 | 번들(이미 그러함) |
| `DATABASE_URL*`, DB 비밀번호 | **불가** | 서버 시크릿 저장소 |
| `GITHUB_CLIENT_SECRET`, `TOKEN_ENCRYPTION_KEY`, `SESSION_SECRET_KEY`, `DESKTOP_AUTH_RESPONSE_KEY`, `AI_*_ENCRYPTION_KEYS` | **불가** | 서버 시크릿 저장소 |
| 코드 서명 인증서와 비밀번호(`GITVIZ_SIGN_PFX*`) | **불가** | 빌드 환경 시크릿(CI 시크릿) |
| 사용자의 AI 제공자 키 | 사용자 기기에서 DPAPI로 암호화해 저장 | `data\`의 보호 저장소 |

(**확인**) 데스크톱 코드에는 위 비밀 값을 읽는 곳이 없고, 번들 검사 스크립트(`desktop/scripts/audit-bundle.mjs`, `scripts/audit_workspace_agent.py`)가 금지 모듈과 비밀 파일을 점검한다. 에이전트는 `.env`를 읽지 않는다.

환경별 설정 비교:

| 항목 | Development | Test | Production(클라이언트 / 서버) |
|---|---|---|---|
| DB | 로컬 compose Postgres `gitviz_dev` | `gitviz_test*` (CI 서비스 컨테이너 / 로컬 임시) | 서버: 관리형 Postgres(TLS 검증, `DATABASE_SSL_CA`) / 클라이언트: 로컬 SQLite |
| API 원점 | `http://127.0.0.1:8000`(웹), 데스크톱은 개발 서버 HTTPS 원점 | 테스트 더블 / 로컬 | 빌드에 내장한 HTTPS 원점 |
| 로그 수준 | DEBUG | WARNING 이상 | 서버: INFO / 클라이언트: INFO(민감정보 제외) |
| 디버그 | 개발자 도구 허용 | 비허용 | 비허용(`--remote-debugging-port` 금지) |
| 저장 경로 | 개발 전용 데이터 폴더(임시/`GitViz-Dev`) | 임시 폴더 | `%LOCALAPPDATA%\GitViz\Workspace\…` |
| 세션 쿠키 | `SESSION_COOKIE_SECURE=0` 허용 | 테스트용 | 반드시 `1`, `https_only` |
| AI | 기본 꺼짐, 필요 시 로컬 모델 | 가짜 제공자(`ai_fake_provider.py`) | 정책에 따름(`AI_ALLOW_EXTERNAL_TRANSFER` 명시) |
| 데스크톱 인증 | 선택 | 선택 | `DESKTOP_AUTH_ENABLED=1` + 응답 키 설정 필수 |
| 업데이트 | 비활성 | 비활성 | 현재 비활성(수동 검증만) |

## 4. 환경별 디렉터리 구조

### 4.1 저장소(개발·빌드) — 요청서 구조와 현재 구조의 조정

요청서의 참고 구조(`backend/ frontend/ desktop/ shared/ tests/ docs/ infrastructure/ tools/ scripts/ packaging/ build-output/`)를 현재 코드에 맞춰 평가한다.

| 요청서 폴더 | 판단 | 현재 대응 / 충돌과 대응 방안 |
|---|---|---|
| `backend/` | **보류 (이름 변경하지 않음)** | `app/`. 패키지명 `app`을 `import app.*`가 전 영역(스크립트 366, 테스트 150, Alembic, Celery 설정, spec)에서 쓴다. 이름만 바꾸는 이득보다 위험이 크다. 문서상 "backend = `app/`"로 정의하고, 필요하면 `backend/app/`로 한 단계 내리는 안을 3차에서 재평가 |
| `frontend/` | 유지 | `frontend/` (그대로) |
| `desktop/` | 유지 | `desktop/` (그대로). `build.mjs`·`forge.config.ts`의 상대 경로 갱신이 필요한 경우만 변경 |
| `shared/` | 선택 | `contracts/`가 이미 공유 계약. 공용 AI 코어(`app/services/ai` 7개 모듈)는 1차에서 선택 사항으로 보류 |
| `tests/` | 채택 | `tests/{server,agent,frontend}` (1차 제안 B단계) |
| `docs/` | 유지 | `docs/` + 이번에 `docs/development/`, `docs/deployment/` 추가 |
| `infrastructure/` | 부분 채택 | `docker-compose.yml`은 기본 탐색 위치(루트)에서 `docker compose`가 바로 인식하므로 이동 시 `-f` 지정이 필요. 서버 배포 정의가 생기면 그때 `infrastructure/`를 만든다 |
| `tools/` | 채택 | 1차의 `tools/{build,ops,validation,vm}`. `scripts/`는 별도로 두지 않고 `tools/`로 통합 |
| `scripts/` | `tools/`로 흡수 | 요청서는 `tools/`와 `scripts/`를 모두 둔다. 둘의 구분 기준이 없어 혼란을 키우므로 하나로 통합 권장 |
| `packaging/` | 채택(점진) | `workspace_agent.spec`, 설치 프로그램 자산(아이콘·채널 설정), 버전 정보. `desktop/forge.config.ts`는 `package.json`의 `config.forge`가 가리키므로 desktop에 남기고 값만 `packaging/`에서 읽게 할 수 있음 |
| `build-output/` | 채택 | 현재 `build/`, `dist/`, `desktop/dist`, `desktop/out`이 흩어져 있다. 한 폴더 아래로 모으는 안(§4.3). spec·forge·스크립트·CI·`.gitignore` 동시 변경 필요 |

개발 도구와 가상환경은 운영 코드와 구분한다:

```
gitviz/
├─ app/ desktop/ frontend/ contracts/ migrations/ …       (운영 코드 — 변경 없음)
├─ tools/{build,ops,validation,vm}/                        (개발·릴리스 도구)
├─ packaging/                                              (배포 입력: spec, 채널 설정, 자산)
├─ build-output/                                           (생성물 — Git 제외)
│   ├─ agent/        ← dist/workspace-agent + build/workspace-agent
│   ├─ desktop/      ← desktop/dist, desktop/out
│   └─ release/<version>/   ← Setup.exe, nupkg, RELEASES, 해시, 빌드 정보
└─ .local/                                                 (개발자 개인 — Git 제외, 저장소 밖 권장)
    ├─ venv/ (개발)   ├─ venv-build-agent/ (빌드 전용)   └─ validation/ (VM·증거)
```

`.validation/`(58 GB)와 가상환경은 저장소 폴더 안에 두는 현재 방식 대신 저장소 밖(예: 별도 드라이브)을 권장한다. 도구가 경로를 고정하고 있어(`pyright-workspace.json`의 `venvPath: .validation`, CI의 `.validation/venv-workspace`) 이동은 3차에서 함께 처리한다.

### 4.2 사용자 PC (Production 클라이언트)

```
%LOCALAPPDATA%\GitVizWorkspace\            ← Squirrel 관리. 언제든 삭제·재생성 가능한 "프로그램"
   Update.exe  packages\  app-0.7.x\{GitVizWorkspace.exe, resources\{app.asar, agent\…}}
%LOCALAPPDATA%\GitViz\Workspace\           ← 사용자 데이터 (설치 폴더와 별개, 제거 정리 대상 아님)
   data\{workspace.sqlite3(+wal/shm), key.dpapi, desktop-session.dpapi, secret-check.bin, writer.lock}
   cache\        (Chromium sessionData)
   logs\         (제안: 순환 로그, 민감정보 제외)
   backups\      (마이그레이션 직전 백업, 보존 기간 적용)
%APPDATA%\gitviz-workspace\                 ← 현재 위치. 이전 후 읽기 전용 원본으로만 유지, 확인 후 삭제 안내
사용자 프로젝트 폴더(사용자가 선택)           ← 사용자가 만든 파일. 변경 제안 적용 대상
%TEMP%\GitVizWorkspace-uninstall-cleanup.{log,ps1}   ← 제거 시에만 생성, 스스로 삭제(로그는 남음)
```

### 4.3 빌드 산출물 위치 이전 시 함께 바꿀 항목 (**3차에서 결정**)

`workspace_agent.spec`(출력 경로 문자열 없음, `build_workspace_agent.py`가 지정), `scripts/build_workspace_agent.py`(`--distpath`/`--workpath`), `desktop/forge.config.ts`(`extraResource`), `desktop/scripts/build.mjs`, `.gitignore`, 세 워크플로의 아티팩트 경로(`desktop/out/…`), 검증 스크립트(`validate_workspace_windows.ps1`), 문서 인용.

## 5. 환경별 실행 경로 (제안과 현재 구분)

| 구분 | Development | Test | Production(클라이언트) |
|---|---|---|---|
| 서버 | `python -m uvicorn app.main:app --host 127.0.0.1 --port 8000` | 테스트가 앱 객체를 직접 사용 | 운영 인프라(범위 밖) |
| 워커 | `python -m celery -A app.core.celery_app:celery worker …` | CI `ai-queue` 작업이 별도 프로세스로 실행 | 운영 인프라 |
| 웹 UI | 서버가 `frontend/` 서빙 | `node scripts/test_*.cjs` | 서버가 서빙 |
| 데스크톱 | `npm --prefix desktop run build && npm start` — **제안:** `GITVIZ_USER_DATA_DIR`(개발 빌드에서만 유효)로 개발 전용 폴더 사용 | `electron dist/tests/*.e2e.js` (임시 프로필) | 설치된 `GitVizWorkspace.exe` |
| 에이전트 | 개발 중에는 데스크톱이 `resources\agent\…exe`를 요구하므로 먼저 빌드해야 함(**확인**) | `python -m unittest discover -s tests/workspace` | `resources\agent\gitviz-workspace-agent.exe`(고정 경로) |

**제안:** 개발 빌드에서도 에이전트를 빌드 없이 실행할 수 있게 `GITVIZ_AGENT_COMMAND`(개발 빌드에서만 유효)로 `python -m app.workspace`를 지정하는 경로를 허용한다. 포장된 빌드(`app.isPackaged`)는 이 변수를 **무시**한다. 현재 코드는 에이전트 경로를 고정하고 렌더러·설정 입력을 받지 않는 보안 속성을 가지므로, 개발 편의가 이 속성을 약화시키지 않는지 3차에서 검토한다.

## 6. 보안·권한 정책

- **권한:** 설치·실행·업데이트·제거 모두 일반 사용자 권한. 관리자 권한을 요구하는 단계를 만들지 않는다(§3.1).
- **데이터 접근:** `data\`는 사용자 계정만 접근하도록 ACL을 지정한다. 에이전트는 이미 `secure_path`로 파일 권한을 설정하고, 링크(심볼릭·하드링크)를 거부한다(`reject_links`). 이전(마이그레이션) 코드도 같은 보호를 사용해야 한다.
- **비밀:** §3.4. 포장된 앱은 환경변수로 접속 원점을 덮어쓸 수 없게 하는 것을 권장한다(사용자 환경변수로 원점이 바뀌는 경로 차단). 스테이징/QA는 별도 채널 빌드로 다룬다.
- **네트워크:** 클라이언트는 HTTPS 원점만 허용(`httpsClient`가 프로토콜·사용자정보 검사), 리디렉션 오류 처리, 쿠키 미사용, 요청 헤더 허용 목록.
- **서명:** 현재 없음. Smart App Control 강제 모드와 다운로드 표시(MOTW) 환경에서의 실행은 미검증. 인증서가 정해지면 Setup.exe·`Update.exe`·앱 exe·에이전트 exe의 서명 범위를 검증한다(**미확인**: maker-squirrel의 `certificateFile`이 서명하는 대상 범위).
- **진단 정보:** 진단 내보내기는 "버전과 구성요소 상태만, 계정·토큰·URL·경로·소스·DB·원본 로그 제외"를 명시한다(`diagnostics.ts`). 로그 도입 시 같은 규칙을 적용한다.

## 7. 환경별 테스트 방법

| 환경 | 무엇을 | 어떻게 |
|---|---|---|
| Development | 빠른 피드백 | `npm --prefix desktop run typecheck/lint/test`, `python -m unittest discover -s tests/workspace`, 서버 `python scripts/test_*.py`(로컬 `gitviz_test` DB) |
| Test(CI) | 회귀 | `backend-validation.yml`(Postgres·Redis 서비스 컨테이너), `windows-desktop.yml`(고정 픽스처 프로필) |
| Build | 산출물 검증 | `scripts/audit_workspace_agent.py`(금지 모듈·필수 모듈·SHA-256 목록), `desktop/scripts/audit-bundle.mjs`(ASAR 검사), 번들 파일 목록 비교(**제안**: 허용 목록 대비 초과 파일 실패) |
| Production 후보 | 설치·제거·업그레이드 | 클린 Hyper-V VM: `desktop/tests/run-clean-vm.ps1`, 설치·제거(사용자 데이터 보존, 잔여 정리), **미실시:** 0.7.0→0.7.1 업그레이드, 다운로드 표시(MOTW), Smart App Control |
| 운영 데이터 보호 | 가드 | 가드 모듈 단위 테스트: 운영 호스트 URL을 주면 모든 진입점이 거부하는지(**제안**) |

## 8. 실행 시나리오 A–J

각 시나리오는 "현재"(확인된 동작)와 "목표"(제안)를 구분한다. 개발 PC 경로의 `<repo>`는 저장소 루트다.

**A. 처음 내려받았을 때**
- 현재: `git clone` → README(Python 3.12, Git, Docker). README는 `.venv`를 안내하지만 이 PC의 실제 가상환경은 `venv/`다. `.env.example`을 `.env`로 복사.
- 목표: `tools/dev/bootstrap.ps1`(제안)이 도구 버전(Python 3.12.10, Node 24.20)을 검사하고 `.env.development`를 `.env.example`에서 생성(비밀은 빈칸), 운영 DB 주소가 들어 있으면 중단.

**B. 개발 환경 구성**
- 현재: `python -m venv` → `pip install -r requirements.txt` → `docker compose up -d db redis` → `python -m alembic upgrade head`. 에이전트 개발은 `pip install -r requirements-workspace.lock` 별도 venv, 데스크톱은 `npm --prefix desktop ci`.
- 목표: 가상환경 3종(서버 개발 / 에이전트 빌드 전용 / 검증)을 `.local/` 아래에 이름으로 구분. 개발 DB 이름 `gitviz_dev`, 테스트 DB `gitviz_test`를 compose가 모두 생성.

**C. 테스트 실행**
- 현재: 서버는 스크립트를 하나씩 실행(CI와 동일 36줄), 에이전트는 `unittest discover`, 데스크톱은 `npm test`와 E2E. 서버 테스트는 `GITVIZ_TEST_DATABASE_URL` 필요.
- 목표: `tools/dev/test.ps1 [server|agent|desktop|all]`(제안)가 `GITVIZ_ENV=test`로 실행하고 임시 사용자 데이터 폴더를 사용.

**D. 릴리스 빌드 생성 (개발자)**
- 현재: 위 Build 절차를 수동 실행. CI `windows-release.yml`(수동 트리거)이 같은 순서를 서명 없이 수행.
- 목표: 기준 빌드는 CI. 개발 PC 빌드는 검증용으로 표시하고 `build-output/`의 빌드 정보에 "local"로 기록.

**E. Setup.exe 생성**
- 현재: `npm --prefix desktop run make`. 산출물: `GitVizWorkspaceSetup.exe`(약 180 MB), `GitVizWorkspace-0.7.0-full.nupkg`, `RELEASES`.
- 목표: 생성 직후 해시 기록, 에이전트 exe 시간 > 에이전트 소스 시간 검증(재빌드 누락 방지), `asar` 허용 목록 검사, 클린 VM 스모크 테스트 통과 후에만 `release/` 폴더로 승격.

**F. 사용자 첫 설치**
- 현재(확인): Setup.exe 실행 → 관리자 권한 불필요 → `%LOCALAPPDATA%\GitVizWorkspace\app-0.7.0\` 설치 → 바로가기 2개 → 설치 후 첫 실행. 서명이 없어 SmartScreen 경고 가능(미검증).
- 목표: 첫 실행에서 데이터 폴더 `%LOCALAPPDATA%\GitViz\Workspace\data\` 생성, 접속 원점은 내장 설정.

**G. 프로그램 실행**
- 현재(확인): 단일 인스턴스 잠금 → 창 표시 → 에이전트 시작 → `ready`(UI가 최대 약 15초 폴링). 서버 원점 환경변수가 없으면 로컬 전용.
- 목표: 시작 시 구 데이터 폴더(`%APPDATA%\gitviz-workspace`)가 있고 새 폴더가 비어 있으면 §9의 이전 절차 수행.

**H. 업데이트**
- 현재: 자동 업데이트 없음. 사용자가 새 Setup.exe를 실행하면 Squirrel이 새 `app-x.y.z`를 설치. 에이전트가 시작될 때 SQLite 스키마를 필요 시 올리고 직전에 백업(`pre-v8-*.sqlite3`)을 만든다. 더 높은 스키마의 DB를 만나면 `UNSUPPORTED_PROTOCOL`로 멈춰 데이터를 보호한다(다운그레이드 차단).
- 목표: 업그레이드 시나리오(0.7.0→0.7.1) VM 검증, 백업 보존 정책 적용, 서명된 Setup.exe + 해시 매니페스트 검증(`update.ts`의 정책 구현 활성화는 별도 결정).

**I. 제거**
- 현재(확인): 제거 → 바로가기·`HKCU` 항목 삭제, 사용자 데이터 보존, 설치 폴더 잔여물은 일회성 예약 작업으로 삭제(대화형 세션). 로그온 세션 없는 무인 제거에서는 `.dead`·`Update.exe` 등이 남음.
- 목표: 데이터가 설치 루트 밖이므로 정리가 데이터를 건드릴 수 없음을 테스트로 고정. "데이터도 삭제" 옵션은 기본 꺼짐.

**J. 오류 후 데이터 복구**
- 현재: 에이전트가 `RECOVERY_REQUIRED`/`UNSUPPORTED_PROTOCOL`/`STORAGE_UNAVAILABLE` 상태를 보고하고 UI가 안내 문구를 표시. 변경 적용 중 중단된 작업은 DB 안의 `apply_journal`(암호화)로 **명시적 복구**만 허용(자동 롤백 없음). DB 손상 시 복구 수단은 `pre-v*.sqlite3` 백업 수동 교체뿐이고 UI가 없다. 키가 없어졌거나 다른 계정에서 열면 복호화에 실패해 `RECOVERY_REQUIRED`.
- 목표: `logs\`와 `backups\`를 진단 화면에서 안내(경로 노출은 사용자 자신의 화면에서만), 백업 선택 복원 도구(안전한 파일 교체 + 키 일치 검사), 백업 보존 정책.

## 9. 기존 구조에서 바뀌어야 하는 항목

| # | 항목 | 현재 | 변경 | 영향 |
|---|---|---|---|---|
| 1 | 사용자 데이터 경로 | `app.getPath('userData')` = Roaming | `app.setPath('userData'/'sessionData', Local 경로)` + 1회 이전 | `desktop/src/main.ts`, E2E 프로필, VM 검증 스크립트의 경로 상수, 문서 |
| 2 | 개발 실행 데이터 분리 | 설치 앱과 공유 | 개발 빌드 전용 폴더 | `main.ts` |
| 3 | 접속 원점 | 환경변수 | 빌드 채널 설정 + 포장 빌드는 환경변수 무시 | `main.ts`, `forge.config.ts`/`build.mjs`, VM·스테이징 스크립트 |
| 4 | 패키지 내용 | `typescript`, `dist/tests` 포함 | `ignore`에서 `dist/tests`·소스맵·`.d.ts` 제외 (`typescript`는 런타임 의존이라 유지 — 5차 정정) | `desktop/package.json`, `forge.config.ts`. **E2E가 `dist/tests`를 포장 앱에서 실행하는 검증 패키지 변형은 별도 설정으로 분리 필요** |
| 5 | 빌드 재현성 | 임의 Python | 잠금 venv 검증, 에이전트 빌드 시각·해시 확인 | `build_workspace_agent.py`, `forge.config.ts` |
| 6 | DB 가드 | 파일별 부분 | 단일 모듈 + `GITVIZ_ENV` | `app/core/database.py`, `app/database.py`, `migrations/env.py`, 스크립트, 테스트 지원 |
| 7 | 로그 | 서버 5개 파일만 `logging`, 클라이언트 없음 | 클라이언트 순환 로그, 서버 전역 구성 | 새 모듈 |
| 8 | 백업 보존 | 무제한 누적 | 개수·기간 제한 | `app/workspace/store.py` |
| 9 | 서버 시크릿 | `.env` 단일 | 환경별 파일 + 운영은 시크릿 저장소 | 문서, compose, 배포 |
| 10 | 가상환경·증거 위치 | 저장소 내부 | 저장소 밖 권장 | `pyright-workspace.json`, CI, 문서 |

## 10. 잠재적 위험

1. **데이터 이전 위험(높음):** 키와 DB는 쌍이다. 이전은 "복사 → 새 위치에서 열기 → 검증 → 원본 보존"의 순서여야 하고, 중간 실패 시 원본이 그대로여야 한다. 첫 공개 릴리스 이후에는 되돌리기 어렵다.
2. **제거 정리와 데이터 경로 충돌(높음):** §3.2. 정리 대상 이름·부모 검사가 바뀌면 데이터 보존 테스트도 함께 바꿔야 한다.
3. **검증 패키지 변형 분리(중간):** `dist/tests`를 제품 패키지에서 빼면 `GITVIZ_VERIFY_*` 변형이 같은 설정을 쓰고 있어 깨질 수 있다.
4. **포장 앱의 환경변수 무시(중간):** 스테이징 검증(VM에서 User 환경변수로 원점 지정)이 현재 이 방식에 의존한다. 채널 빌드 도입과 함께 바꿔야 한다.
5. **무인 제거 시 잔여물(낮음~중간):** 예약 작업은 대화형 세션이 필요하다. 보완책(WMI 등)은 별도 검증이 필요하다.
6. **서명 부재(중간):** 모든 환경 설계가 "서명 없음"을 전제로 한다. 서명이 생기면 업데이트 검증·SmartScreen 동작이 달라진다.
7. **서버 운영 정의 부재(중간):** 클라이언트 설계는 서버 배포·백업·복구 정책 없이는 완결되지 않는다.

## 11. 3차 통합 단계에서 풀어야 할 충돌

| 충돌 | 1차 제안 | 2차 제안 | 해결해야 할 질문 |
|---|---|---|---|
| 폴더 이름 | `scripts/` → `tools/` | `tools/`와 `scripts/` 모두 두자는 요청서 구조 | 하나로 통합(권장)할지 |
| 에이전트 분리 | `agent/` 분리는 보류 권장 | 빌드 입력 격리는 `packaging/`·`build-output/`으로도 달성 가능 | 물리 분리 없이 빌드 격리만 할지 |
| 빌드 산출물 | 위치 유지 | `build-output/` 통합 | 이전 시 spec·forge·CI·검증 스크립트를 한 번에 바꿀 일정 |
| `backend/` | `app/` 유지 | 요청서는 `backend/` | 이름 변경 여부(권장: 변경하지 않음) |
| 사용자 데이터 위치 | 언급 없음 | Local로 이전 | 지금 이전할지, 릴리스 후로 미룰지 |
| DB 통합 | 동기/비동기 통합은 보류 | 가드는 두 엔진 모두에 필요 | 가드를 두 엔진 위에서 한 모듈로 구현 |
| 제거 정리 | 코드 변경 없음 | 데이터 경로와 충돌 | 정리 대상 이름·검사 조건 유지 방식 |
| 문서 CI | `docs/index.md` 링크 미연결 | 신규 문서 5개 추가 | 인덱스를 어떻게 연결할지(두 차수 문서 일괄 연결 권장) |
