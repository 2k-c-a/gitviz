# 4차 구현 기록

기준: [최종 이동·변경 계획](FINAL_MIGRATION_PLAN.md) · 브랜치 `refactor/p11-structure` (시작점 `65f1b62`) · 이동 이력은 [MIGRATION_HISTORY.md](MIGRATION_HISTORY.md)

각 단위: 일시, 목적, 변경, 테스트 결과, 오류와 해결. 일시는 2026-10-30 로컬 시각 기준 날짜만 적는다.

## S0 기준선 (변경 없음)
- **목적:** 변경 전 정상 상태 확보.
- **백업(저장소 밖):** `C:\Users\user\gitviz-p11-backup\` — `userdata\`(사용자 데이터 5개 파일 복사본), `baseline\`(Setup.exe 사본, 추적 파일 목록, 에이전트 파일 목록·해시, asar 항목 985개, 테스트 출력).
- **사용자 데이터 위치 실측:** 에이전트 파일은 `%APPDATA%\gitviz-workspace\workspace\` 하위(상위에는 Chromium 생성물). 3차 문서 일부가 상위 폴더로 적었던 것을 정정함(S1).
- **테스트 DB:** 저장소 개발 DB(5432)를 건드리지 않고 일회용 컨테이너 `gitviz-p11-testdb`(postgres:16-alpine, 55432)와 `gitviz-p11-testredis`(56379)를 사용.
- **결과:**

| 항목 | 결과 |
|---|---|
| B1 문서 검사 | 실패 16건 = 모두 "docs/index.md에서 연결 안 됨"(S1에서 해소) |
| B2 ruff | 통과 |
| B3 `tests/workspace` | 134개 통과 (`.validation/venv-workspace`). 개발용 `venv`로는 `jsonschema` 부재로 17개 오류 → 기준 환경은 에이전트 venv |
| B4 데스크톱 | 빌드 성공, 55개 통과 |
| B5 서버 테스트 | 38줄 중 36 통과, `test_ai_queue_integration`·`verify_ai_mvp_integration`은 `GITVIZ_TEST_REDIS_URL` 필요(미설정 시 실패). 설정 후 둘 다 통과 → 실질 38/38 |
| B6 Alembic head | `b7e1d9c3a502` |
| B7 에이전트 | 파일 134개, `gitviz-workspace-agent.exe` SHA-256 `ad5fadf1…a7dfa` (기존 빌드 산출물 기준) |
| B8 asar | 985개 항목 |

- **발견:** 서버 통합 테스트는 `GITVIZ_TEST_REDIS_URL`(전용 Redis, DB를 비움)이 필요하다. 개발 문서에 반영 필요(S1 이후 DEVELOPMENT_SETUP).

## S1 문서·무시 규칙
- **변경:** `docs/index.md`에 1·2·3·4차 문서 연결, `.gitignore`에 `Claude outputs/`·`/build-output/` 추가, 추적 중이던 `Claude outputs` 1개 추적 해제(디스크 파일 유지), `FILE_MIGRATION_CANDIDATES.md`에 3차 대체 안내, 데이터 경로 정정, 1~3차 문서 15개 추가.
- **테스트:** 문서 검사 전부 통과(도달 가능 페이지 104).

## S2 운영 도구 이동 (`scripts/` → `tools/ops`, `tools/ops/legacy`)
- **변경:** 29개 파일 `git mv`(운영 18, legacy 11). 루트 계산(`dirname` 중첩/`parents[N]`)을 깊이에 맞게 수정, 사용법 문구의 경로 갱신. `test_ai_ops.py`는 `tools/ops`를 `sys.path`에 추가. 참조 수정: `docker-compose.yml` 주석, `docs/operations/ai.md`, `docs/architecture/repository-access-and-snapshots.md`, `app/tasks/analyze_repository.py` 주석.
- **테스트:** 29개 전부 컴파일 OK(실행 없음, 운영 DB 대상 도구 포함), 루트 계산 결과가 저장소 루트와 일치, `tools/ops/ai_ops.py status`를 일회용 테스트 DB에 실행(읽기 전용) 정상, `test_ai_ops` 7개 통과, 문서 검사 통과.
- **오류:** 없음.

## S3 VM 도구 이동 (`scripts/` → `tools/vm`)
- **변경:** 9개 `git mv`. 7개 ps1의 저장소 루트 계산(`Split-Path $PSScriptRoot -Parent`)을 한 단계 더 올림, `staging_vm.ps1` 안내 문구 경로 갱신. 서로 호출하는 `$PSScriptRoot` 상대 호출(`install_p01_vm` → `p01_boot_repair_iso`)과 `enable_p01_hyperv.bat`의 `%~dp0`는 같은 폴더 이동이라 수정 불필요.
- **테스트:** PowerShell 파서로 7개 ps1 구문 오류 0(실행하지 않음, VM 미조작), 문서 검사 통과.
- **정정:** 기존 문서(`docs/operations/windows-desktop.md`, `p10-partial`)는 0.7.0→0.7.1 업그레이드를 clean VM에서 확인했다고 기록한다. 3차 문서가 "미검증"이라 적은 것을 "마커 파일 수준 검증됨, 실제 DB 상태는 미검증"으로 정정(`INSTALLATION_GUIDE`, `RELEASE_MIGRATION_PLAN`, `FINAL_ARCHITECTURE`).
- **참고:** `docs/migration/validation/*`의 과거 증거 문서가 인용하는 `scripts/…` 경로 텍스트는 당시 실행 기록이므로 수정하지 않았다(링크 아님, 검사 통과).

## S4 에이전트 빌드 입력·도구 (`packaging/agent`, `tools/build`)
- **변경:** `workspace_agent.spec`, `requirements-workspace{,-build}.txt`, `requirements-workspace.lock`을 `packaging/agent/`로, `build_workspace_agent.py`·`audit_workspace_agent.py`를 `tools/build/`로 `git mv`. spec의 `root = Path(SPECPATH).parents[1]`, 빌드 스크립트의 `parents[2]`와 spec 경로, `pyright-workspace.json`, `validate_workspace_windows.ps1`(ruff 대상·agent-freeze), 두 CI 워크플로(경로 필터 `packaging/agent/**`, 잠금 설치 경로), 문서 링크(`docs/migration/validation/p01-2026-10-24.md`, `docs/ux-redesign/phase-1-c459f29/project-audit.md`) 갱신.
- **테스트:** 새 위치에서 에이전트 동결 성공(18초), 산출물 파일 134개 목록이 기준선(B7)과 **동일**, `audit_workspace_agent.py` 통과(module 623, 서버 모듈·비공개 파일 없음), `.lock` SHA-256 불변(`09ac9377…`), ruff 통과, pyright 0 오류, `tests/workspace` 134개 통과, 문서 검사 통과.
- **참고:** exe의 SHA-256은 재빌드로 달라졌다(빌드 시각 포함, 예상된 변화). 파일 구성·크기 수준은 동일. 잠금 설치(`--require-hashes`)는 새 가상환경이 필요해 실행하지 않았다(파일 내용 불변, `-r` 상대 경로는 같은 폴더).

## S5 검증 도구 이동 (`scripts/` → `tools/validation`)
- **계획 수정:** `smoke_ai_live.py`는 `test_ai_smoke.py`가, `ai_integration_stack.py`는 `verify_ai_mvp_integration.py`를 형제 import하므로 `tools/validation`이 아니라 `tests/server`(S6)로 확정. `tools/validation`은 11개(validate_*.ps1 10 + `ai_ui_mock_server.py`).
- **변경:** `git mv` 11개. ps1의 루트 계산 `Join-Path $PSScriptRoot '..'` → `'../..'`, `ai_ui_mock_server.py`의 `parents[2]`, 두 워크플로의 게이트 호출·경로 필터, 로컬 `.claude/launch.json`(미추적)의 목업 서버 경로. 이 ps1들이 부르는 `scripts\test_*`·`scripts/*.cjs` 경로는 S6에서 테스트와 함께 바꾼다(그때까지 유효).
- **테스트:** 10개 ps1 구문 오류 0, 문서 검사 통과, **`tools/validation/validate_workspace_windows.ps1` 전체 게이트 통과**(23단계, 실패 0, ruff·desktop-build·agent-freeze·host/workspace 단위·Electron E2E 포함, 작업 트리 변경 없음, 증거 `gitviz-p11-backup\S5-gate`).
- **오류:** 증거 폴더를 미리 만들어 게이트가 "Evidence directory must be new"로 거부 → 새 경로로 재실행(코드 문제 아님).

## S6 테스트·지원 모듈 이동 (`scripts/` → `tests/server`, `tests/frontend`)
- **계획 수정:** `smoke_ai_live.py`·`ai_integration_stack.py`는 형제 import 때문에 `tests/server`로 확정(S5 기록). `scripts/`는 비어서 제거(생성물 `__pycache__`만 남아 있던 디렉터리 삭제).
- **변경:** `git mv` 62개(서버 52 + 픽스처 6 + 프런트 4). 루트 계산(`parents[N]`, `dirname` 중첩)을 깊이에 맞게 수정, 모듈 경로 `scripts.*` → `tests.server.*`(`AI_HANDLER_MODULES`, `from scripts import validation_reports` 등), 픽스처 경로 `scripts/fixtures/ai` → `tests/server/fixtures/ai`, 하위 프로세스 스폰 경로, `.cjs`의 `frontend` 상대 경로(`../../frontend`), `test_desktop_auth.py`의 `ROOT/"scripts"` → `ROOT/"tests"/"server"`. `backend-validation.yml` 36줄·`windows-desktop.yml` 필터·게이트의 web 테스트 경로·`validate_p03/p06_postgres.ps1`·`validate_p09_services.ps1` 갱신. 현행 문서 11개의 링크·명령 경로 갱신(과거 증거 문서 `docs/migration/validation/**`는 링크 대상만).
- **오류와 해결:** 전체 게이트의 `workspace-unit`에서 `tests/workspace/test_ai_core.py`가 `scripts/test_ai_output_validation.py`를 직접 읽는 것을 발견(이동 전 스캔이 `scripts/` 내부 파일만 대상이어서 놓침). 경로를 `tests/server/…`로 수정하고 재검증. 교훈: 참조 검색은 저장소 전체(`git grep`)로 한다.
- **테스트:** 서버·프런트 CI 38줄 전부 통과(일회용 테스트 DB/Redis, 새 경로), `tests/server` 52개 컴파일 OK, 문서 검사 통과, **전체 게이트 통과**(재실행; 첫 실행은 위 오류로 실패), 작업 트리 변경 없음.
- **참고:** `docs/migration/12-testing-strategy.md` 등 과거 계획 문서의 명령 텍스트 `scripts/…`는 당시 기록으로 유지.

## S7 인증 라우터 이동 (`app/routers/auth.py` → `app/api/auth.py`)
- **변경:** `git mv`, 빈 `app/routers/` 패키지 제거. import 갱신: `app/main.py`, `app/api/{ai/dependencies,desktop/repositories,repositories,snapshot_route,snapshots}.py`, `tests/server/*`(import와 `patch("app.api.auth.…")` 문자열), 문서 링크 2곳. 코드 본문과 `_get_session_user_id` 이름은 변경 없음.
- **테스트:** OpenAPI 라우트 67개(경로+메서드) 이동 전후 **동일**, 서버 CI 38줄 전부 통과, 잔존 `app.routers` 참조 0건(설계 문서 제외), 문서 검사 통과.
- **오류:** 없음. 참고: `app.main` 임포트 시 로컬 `.env`의 `DATABASE_URL_ASYNC`가 적용되어 설정 검증이 실패하므로(테스트 DB와 불일치), 검증은 `DATABASE_URL_ASYNC`를 테스트 DB로 명시해 실행했다(연결은 하지 않음).

## S8 동반 변경 파일 계산 분리 (`app/api/analytics.py` → `app/services/coupled_files.py`)
- **변경:** `COUPLED_SORT_KEYS`, `_coupled_files_for`(본문 불변, 35줄)를 새 모듈로 이동. `analytics.py`는 같은 이름을 import(기존 `from app.api.analytics import _coupled_files_for` 사용처 호환), `analysis_snapshots.py`·`history_context.py`는 서비스 모듈에서 직접 import. `tests/server/test_metric_policy.py`는 `analytics.py`에서 AST로 함수를 추출해 실행하므로 새 모듈에서도 추출하도록 로더 4줄 추가.
- **테스트:** OpenAPI 라우트 67개 동일, `test_metric_policy`·`test_repository_snapshots`·`test_analytics_calculations`·`test_ai_history_context`·`test_backend_improvements` 통과(스냅샷 해시 포함), 서버 CI 38줄 전부 통과.
- **성공 기준 조정:** 계획의 "services→api import 0건"은 달성하지 못했다. `app/services/analysis_snapshots.py:207`이 `app.api.analytics`의 다른 계산 함수(`_compute_coupling` 등)를 함수 안에서 import한다. 이 의존 제거는 계산 코드 전체 이동이 필요해 승인된 범위(이번 함수 2개) 밖이므로 하지 않고 남은 문제로 기록한다.
