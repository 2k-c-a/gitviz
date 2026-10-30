# 최종 이동·변경 계획 (3차 확정 · 4차 작업의 단일 기준)

작성일: 2026-10-30 · 기준 커밋: `65f1b62` · 상태: **확정 설계. 어떤 파일도 변경하지 않았다.** 4차는 사용자의 명시적 승인 후에만 시작한다.

함께 읽기: [최종 아키텍처](FINAL_ARCHITECTURE.md) · [디렉터리 구조](FINAL_DIRECTORY_TREE.md) · [결정 기록](ARCHITECTURE_DECISIONS.md) · [환경 매트릭스](../deployment/FINAL_ENVIRONMENT_MATRIX.md) · [릴리스 이행 계획](../deployment/RELEASE_MIGRATION_PLAN.md) · 1차 [이동 후보](FILE_MIGRATION_CANDIDATES.md)(이 문서가 대체)

## 0. 4차 작업 공통 규칙

1. 작업 브랜치에서 **S# 하나당 커밋 하나**. 이동은 `git mv`(이력 보존). 이동과 그 참조 수정은 **같은 커밋**.
2. 커밋·푸시·의존성 설치는 사용자 승인이 필요하다. `.env` 읽기, `reset --hard`, `clean` 금지. `Claude outputs/`는 커밋하지 않는다. 문서 보관소 `docs/archive/**`는 수정하지 않는다.
3. 각 작업 시작 전 [S0 기준선]과 같은 검증을 실행하고, 끝난 뒤 **같은 결과**여야 성공이다.
4. 판정이 모호하거나 참조를 못 찾으면 **이동하지 않고 보존**한다.
5. 루트 계산 점검: 이동 대상의 `Path(__file__)…parents[N]`, `os.path.dirname(__file__)`, 상대 `require`, `sys.path.insert`를 이동 전에 모두 나열한다(`scripts/*.py`에 `__file__` 구문 77곳).
6. 성공 판정의 공통 항목: `python -B -I docs/audit/check_documents.py`가 (S1 이후) 0건 실패, 변경한 경로를 가리키는 잔존 참조 0건(`git grep`).

## 1. 이동 매핑 (전체)

변경 유형: 이동, 이름 변경, 유지. 위험도는 깨질 때의 영향과 참조 수 기준이다.

### 1.1 루트·빌드 입력

| 기존 경로 | 신규 경로 | 유형 | 수정할 참조 | 위험도 | 검증 방법 | 작업 |
|---|---|---|---|---|---|---|
| `workspace_agent.spec` | `packaging/agent/workspace_agent.spec` | 이동 | spec 내부 `root = Path(SPECPATH)` → `.parents[1]`; shim 경로; `build_workspace_agent.py`; 문서 링크(`docs/migration/validation/p01-2026-10-24.md`) | 높음 | `tools/build/build_workspace_agent.py` 실행 → `agent` onedir 생성, `audit_workspace_agent.py` 통과, 산출물 파일 목록이 이전 빌드와 동일 | S4 |
| `requirements-workspace.txt` | `packaging/agent/requirements-workspace.txt` | 이동 | `windows-desktop.yml`(경로 필터 `requirements-workspace*`와 설치 줄), `windows-release.yml`, 문서 | 중간 | 잠금 설치 `pip install --require-hashes --only-binary=:all: -r …lock`가 성공 | S4 |
| `requirements-workspace-build.txt` | `packaging/agent/requirements-workspace-build.txt` | 이동 | 내부 `-r requirements-workspace.txt`(같은 폴더라 유효), CI/문서 | 중간 | 위와 동일 | S4 |
| `requirements-workspace.lock` | `packaging/agent/requirements-workspace.lock` | 이동 | CI 설치 줄, 문서. **내용 불변(해시)** | 중간 | 파일 SHA-256 이동 전후 동일 | S4 |
| `requirements.txt` | 유지 | 유지 | — | — | — | — |
| `ruff-workspace.toml`, `pyright-workspace.json` | 유지 | 유지 | `pyright-workspace.json`의 `include`(에이전트 소스 경로 불변이므로 변경 없음, 테스트 경로 `tests/workspace`도 불변) | — | `npm --prefix desktop run typecheck:agent` | — |
| `docker-compose.yml`, `alembic.ini`, `.env.example` | 유지 | 유지 | compose 주석의 `scripts/ai_ops.py` 문구 | 낮음 | `docker compose config`(실행 아님, 문법) | S2 |
| `.gitignore` | 유지(내용 갱신) | 이름 유지 | `/build-output/`, `Claude outputs/` 추가; `dist/`, `build/`는 S9 후 유지(빌드 도구가 만들 수 있음); 존재하지 않는 `arduino/`·`app/api/iot.py`·`.frontend_vite_backup.zip`은 **S10까지 보존** | 낮음 | `git status`로 추적/제외 의도 확인 | S1, S9 |

### 1.2 `app/` (서버·에이전트)

| 기존 경로 | 신규 경로 | 유형 | 수정할 참조 | 위험도 | 검증 방법 | 작업 |
|---|---|---|---|---|---|---|
| `app/routers/auth.py` (+ `__init__.py`) | `app/api/auth.py` | 이동 | `app/main.py`, `app/api/ai/dependencies.py`, `app/api/desktop/repositories.py`, `app/api/repositories.py`, `app/api/snapshot_route.py`; 테스트·스크립트의 `app.routers.auth`; 문서 링크. `_get_session_user_id`는 이름 유지(공개화는 후속) | 중간 | `git grep "app.routers"` 0건, 서버 테스트(`test_desktop_auth`, `test_backend_improvements`) 동일 결과, OpenAPI 라우트 목록 이전 후 동일 | S7 |
| `app/api/analytics.py` 의 `_coupled_files_for`, `COUPLED_SORT_KEYS` | `app/services/coupled_files.py`(신규 모듈) | 이동(함수) | `app/services/ai/history_context.py`, `app/services/analysis_snapshots.py`, `analytics.py` 자체 | 중간 | `test_analytics_calculations`, `test_repository_snapshots`(스냅샷 해시) 동일 결과, 호출 그래프에서 `services→api` import 0건 | S8 |
| `app/workspace/**` | 유지 | 유지 | — | — | `tests/workspace`, `test_import_boundary` | — |
| `app/core/database.py`, `app/database.py` | 유지 | 유지 | — (AD-19) | — | — | — |
| `app/main.py`, 나머지 `app/**` | 유지 | 유지 | — | — | — | — |

### 1.3 `desktop/`, `frontend/`, `contracts/`, `migrations/`, `tests/workspace`

| 기존 경로 | 신규 경로 | 유형 | 수정할 참조 | 위험도 | 검증 방법 | 작업 |
|---|---|---|---|---|---|---|
| `desktop/forge.config.ts` | 유지 | 유지(내용) | `extraResource`(`../dist/workspace-agent/agent` → `../build-output/agent/dist/agent`), `outDir` | 높음 | 패키지 생성 후 `resources\agent\gitviz-workspace-agent.exe` 존재, asar 항목 목록 동일 | S9 |
| `desktop/package.json`, `tsconfig*`, `src/`, `ui/`, `tests/`, `scripts/build.mjs` | 유지 | 유지 | `build.mjs`가 참조하는 `frontend/*`는 불변. 스크립트명 불변 | — | `npm --prefix desktop run build`, `npm --prefix desktop test` | — |
| `frontend/**`, `contracts/**`, `migrations/**` | 유지 | 유지 | — | — | `alembic heads` = `b7e1d9c3a502` | — |
| `tests/workspace/**` | 유지 | 유지 | `test_p06_migration.py`, `test_p07_migration.py`의 `desktop/out/…` 하드코딩 → `build-output/desktop/…`; `tests/workspace/test_*`의 `desktop/out` 외 변경 없음 | 중간 | `python -m unittest discover -s tests/workspace` 동일 결과(해당 2개는 패키지가 있을 때만 실행되는지 기존 동작과 비교) | S9 |
| `tests/__init__.py` | 유지 | 유지 | `tests/server`, `tests/frontend`에는 `__init__.py`를 만들지 않는다(스크립트 직접 실행 유지) | 낮음 | — | S6 |

### 1.4 `scripts/` 113개 → `tests/`, `tools/` (파일별)

각 파일의 *수정할 참조*는 열에 적었다. 모든 이동 파일에 공통으로 루트 계산(`Path(__file__).resolve().parents[1]` 등)을 새 깊이에 맞춰 고친다. 검증 방법은 §3의 해당 작업(S2~S6)에 정의된 명령을 사용한다.

| 기존 경로 | 신규 경로 | 유형 | 수정할 참조 | 위험도 |
|---|---|---|---|---|
| `scripts/ai_credential_support.py` | `tests/server/ai_credential_support.py` | 이동 | CI 호출 경로·`parents[1]`→`parents[2]` (형제 import는 평면 이동으로 유지) | 높음 |
| `scripts/ai_fake_provider.py` | `tests/server/ai_fake_provider.py` | 이동 | CI 호출 경로·`parents[1]`→`parents[2]` (형제 import는 평면 이동으로 유지) | 높음 |
| `scripts/ai_git_fixture.py` | `tests/server/ai_git_fixture.py` | 이동 | CI 호출 경로·`parents[1]`→`parents[2]` (형제 import는 평면 이동으로 유지) | 높음 |
| `scripts/ai_input_support.py` | `tests/server/ai_input_support.py` | 이동 | CI 호출 경로·`parents[1]`→`parents[2]` (형제 import는 평면 이동으로 유지) | 높음 |
| `scripts/ai_integration_app.py` | `tests/server/ai_integration_app.py` | 이동 | CI 호출 경로·`parents[1]`→`parents[2]` (형제 import는 평면 이동으로 유지) | 높음 |
| `scripts/ai_integration_patches.py` | `tests/server/ai_integration_patches.py` | 이동 | CI 호출 경로·`parents[1]`→`parents[2]` (형제 import는 평면 이동으로 유지) | 높음 |
| `scripts/ai_integration_stack.py` | `tools/validation/ai_integration_stack.py` | 이동 | 내부 루트 계산, 문서 인용 | 중간 |
| `scripts/ai_ops.py` | `tools/ops/ai_ops.py` | 이동 | `test_ai_ops.py`의 `sys.path`/import, `docker-compose.yml` 주석, `docs/operations/ai.md` | 높음 |
| `scripts/ai_test_handlers.py` | `tests/server/ai_test_handlers.py` | 이동 | CI 호출 경로·`parents[1]`→`parents[2]` (형제 import는 평면 이동으로 유지) | 높음 |
| `scripts/ai_test_support.py` | `tests/server/ai_test_support.py` | 이동 | CI 호출 경로·`parents[1]`→`parents[2]` (형제 import는 평면 이동으로 유지) | 높음 |
| `scripts/ai_ui_mock_server.py` | `tools/validation/ai_ui_mock_server.py` | 이동 | 내부 루트 계산, 문서 인용 | 중간 |
| `scripts/audit_workspace_agent.py` | `tools/build/audit_workspace_agent.py` | 이동 | `parents[1]`→`parents[2]`, spec·CI·`validate_workspace_windows.ps1` | 높음 |
| `scripts/backfill_analysis_snapshots.py` | `tools/ops/backfill_analysis_snapshots.py` | 이동 | 루트 계산(`__file__`) | 낮음 |
| `scripts/backfill_commit_type.py` | `tools/ops/backfill_commit_type.py` | 이동 | 루트 계산(`__file__`) | 낮음 |
| `scripts/backfill_pr_author_login.py` | `tools/ops/backfill_pr_author_login.py` | 이동 | 루트 계산(`__file__`) | 낮음 |
| `scripts/backfill_pr_large_flag.py` | `tools/ops/backfill_pr_large_flag.py` | 이동 | 루트 계산(`__file__`) | 낮음 |
| `scripts/build_workspace_agent.py` | `tools/build/build_workspace_agent.py` | 이동 | `parents[1]`→`parents[2]`, spec·CI·`validate_workspace_windows.ps1` | 높음 |
| `scripts/check_connections.py` | `tools/ops/check_connections.py` | 이동 | 루트 계산(`__file__`) | 낮음 |
| `scripts/check_date_formats.py` | `tools/ops/check_date_formats.py` | 이동 | 루트 계산(`__file__`) | 낮음 |
| `scripts/check_job.py` | `tools/ops/check_job.py` | 이동 | 루트 계산(`__file__`) | 낮음 |
| `scripts/check_resolution_progress.py` | `tools/ops/check_resolution_progress.py` | 이동 | 루트 계산(`__file__`) | 낮음 |
| `scripts/cleanup_duplicates.py` | `tools/ops/cleanup_duplicates.py` | 이동 | 루트 계산(`__file__`) | 낮음 |
| `scripts/cleanup_repo10.py` | `tools/ops/cleanup_repo10.py` | 이동 | 루트 계산(`__file__`) | 낮음 |
| `scripts/cleanup_test_repo.py` | `tools/ops/cleanup_test_repo.py` | 이동 | 루트 계산(`__file__`) | 낮음 |
| `scripts/demo_incremental_read.py` | `tools/ops/demo_incremental_read.py` | 이동 | 루트 계산(`__file__`) | 낮음 |
| `scripts/enable_p01_hyperv.bat` | `tools/vm/enable_p01_hyperv.bat` | 이동 | 스크립트 간 상대 호출·`.validation/p01-vm` 경로 문자열 | 중간 |
| `scripts/enable_p01_hyperv.ps1` | `tools/vm/enable_p01_hyperv.ps1` | 이동 | 스크립트 간 상대 호출·`.validation/p01-vm` 경로 문자열 | 중간 |
| `scripts/evaluate_ai_review.py` | `tests/server/evaluate_ai_review.py` | 이동 | CI 호출 경로·`parents[1]`→`parents[2]` (형제 import는 평면 이동으로 유지) | 중간 |
| `scripts/fixtures/ai/evaluation_manifest.json` | `tests/server/fixtures/ai/evaluation_manifest.json` | 이동 | `ai_test_support`·`evaluate_ai_review`의 fixtures 경로(`__file__` 기준 형제) | 낮음 |
| `scripts/fixtures/ai/injection_payloads.json` | `tests/server/fixtures/ai/injection_payloads.json` | 이동 | `ai_test_support`·`evaluate_ai_review`의 fixtures 경로(`__file__` 기준 형제) | 낮음 |
| `scripts/fixtures/ai/invalid_findings.json` | `tests/server/fixtures/ai/invalid_findings.json` | 이동 | `ai_test_support`·`evaluate_ai_review`의 fixtures 경로(`__file__` 기준 형제) | 낮음 |
| `scripts/fixtures/ai/line_maps.json` | `tests/server/fixtures/ai/line_maps.json` | 이동 | `ai_test_support`·`evaluate_ai_review`의 fixtures 경로(`__file__` 기준 형제) | 낮음 |
| `scripts/fixtures/ai/sample.diff` | `tests/server/fixtures/ai/sample.diff` | 이동 | `ai_test_support`·`evaluate_ai_review`의 fixtures 경로(`__file__` 기준 형제) | 낮음 |
| `scripts/fixtures/ai/valid_findings.json` | `tests/server/fixtures/ai/valid_findings.json` | 이동 | `ai_test_support`·`evaluate_ai_review`의 fixtures 경로(`__file__` 기준 형제) | 낮음 |
| `scripts/inspect_duplicates.py` | `tools/ops/inspect_duplicates.py` | 이동 | 루트 계산(`__file__`) | 낮음 |
| `scripts/install_p01_vm.ps1` | `tools/vm/install_p01_vm.ps1` | 이동 | 스크립트 간 상대 호출·`.validation/p01-vm` 경로 문자열 | 중간 |
| `scripts/migrate_analysis_job_user_token.py` | `tools/ops/legacy/migrate_analysis_job_user_token.py` | 이동 | 루트 계산(`__file__`), 주석 내 상호 언급 | 낮음 |
| `scripts/migrate_commit_on_default_branch.py` | `tools/ops/legacy/migrate_commit_on_default_branch.py` | 이동 | 루트 계산(`__file__`), 주석 내 상호 언급 | 낮음 |
| `scripts/migrate_commit_type_columns.py` | `tools/ops/legacy/migrate_commit_type_columns.py` | 이동 | 루트 계산(`__file__`), 주석 내 상호 언급 | 낮음 |
| `scripts/migrate_contributor_aliases_unique_index.py` | `tools/ops/legacy/migrate_contributor_aliases_unique_index.py` | 이동 | 루트 계산(`__file__`), 주석 내 상호 언급 | 낮음 |
| `scripts/migrate_identity_search_attempted.py` | `tools/ops/legacy/migrate_identity_search_attempted.py` | 이동 | 루트 계산(`__file__`), 주석 내 상호 언급 | 낮음 |
| `scripts/migrate_progress_detail.py` | `tools/ops/legacy/migrate_progress_detail.py` | 이동 | 루트 계산(`__file__`), 주석 내 상호 언급 | 낮음 |
| `scripts/migrate_resolution_columns.py` | `tools/ops/legacy/migrate_resolution_columns.py` | 이동 | 루트 계산(`__file__`), 주석 내 상호 언급 | 낮음 |
| `scripts/migrate_schema_v2.py` | `tools/ops/legacy/migrate_schema_v2.py` | 이동 | 루트 계산(`__file__`), 주석 내 상호 언급 | 낮음 |
| `scripts/p01_boot_repair_iso.ps1` | `tools/vm/p01_boot_repair_iso.ps1` | 이동 | 스크립트 간 상대 호출·`.validation/p01-vm` 경로 문자열 | 중간 |
| `scripts/prepare_p01_vm.ps1` | `tools/vm/prepare_p01_vm.ps1` | 이동 | 스크립트 간 상대 호출·`.validation/p01-vm` 경로 문자열 | 중간 |
| `scripts/run_identity_resolution.py` | `tools/ops/run_identity_resolution.py` | 이동 | 루트 계산(`__file__`) | 낮음 |
| `scripts/run_pr_authorship.py` | `tools/ops/run_pr_authorship.py` | 이동 | 루트 계산(`__file__`) | 낮음 |
| `scripts/serve_readonly_validation.py` | `tests/server/serve_readonly_validation.py` | 이동 | CI 호출 경로·`parents[1]`→`parents[2]` (형제 import는 평면 이동으로 유지) | 중간 |
| `scripts/show_summary.py` | `tools/ops/show_summary.py` | 이동 | 루트 계산(`__file__`) | 낮음 |
| `scripts/smoke_ai_live.py` | `tools/validation/smoke_ai_live.py` | 이동 | 내부 루트 계산, 문서 인용 | 중간 |
| `scripts/staging_stack.ps1` | `tools/vm/staging_stack.ps1` | 이동 | 스크립트 간 상대 호출·`.validation/p01-vm` 경로 문자열 | 중간 |
| `scripts/staging_vm.ps1` | `tools/vm/staging_vm.ps1` | 이동 | 스크립트 간 상대 호출·`.validation/p01-vm` 경로 문자열 | 중간 |
| `scripts/test_ai_api.py` | `tests/server/test_ai_api.py` | 이동 | CI 호출 경로·`parents[1]`→`parents[2]` (형제 import는 평면 이동으로 유지) | 높음 |
| `scripts/test_ai_commit_collection.py` | `tests/server/test_ai_commit_collection.py` | 이동 | CI 호출 경로·`parents[1]`→`parents[2]` (형제 import는 평면 이동으로 유지) | 높음 |
| `scripts/test_ai_contracts.py` | `tests/server/test_ai_contracts.py` | 이동 | CI 호출 경로·`parents[1]`→`parents[2]` (형제 import는 평면 이동으로 유지) | 높음 |
| `scripts/test_ai_credential_api.py` | `tests/server/test_ai_credential_api.py` | 이동 | CI 호출 경로·`parents[1]`→`parents[2]` (형제 import는 평면 이동으로 유지) | 높음 |
| `scripts/test_ai_credentials.py` | `tests/server/test_ai_credentials.py` | 이동 | CI 호출 경로·`parents[1]`→`parents[2]` (형제 import는 평면 이동으로 유지) | 높음 |
| `scripts/test_ai_evaluation.py` | `tests/server/test_ai_evaluation.py` | 이동 | CI 호출 경로·`parents[1]`→`parents[2]` (형제 import는 평면 이동으로 유지) | 높음 |
| `scripts/test_ai_frontend.cjs` | `tests/frontend/test_ai_frontend.cjs` | 이동 | CI `backend-validation.yml`/`windows-desktop.yml`, 상대 require 경로 | 중간 |
| `scripts/test_ai_generation.py` | `tests/server/test_ai_generation.py` | 이동 | CI 호출 경로·`parents[1]`→`parents[2]` (형제 import는 평면 이동으로 유지) | 높음 |
| `scripts/test_ai_history_context.py` | `tests/server/test_ai_history_context.py` | 이동 | CI 호출 경로·`parents[1]`→`parents[2]` (형제 import는 평면 이동으로 유지) | 높음 |
| `scripts/test_ai_input_api.py` | `tests/server/test_ai_input_api.py` | 이동 | CI 호출 경로·`parents[1]`→`parents[2]` (형제 import는 평면 이동으로 유지) | 높음 |
| `scripts/test_ai_inputs.py` | `tests/server/test_ai_inputs.py` | 이동 | CI 호출 경로·`parents[1]`→`parents[2]` (형제 import는 평면 이동으로 유지) | 높음 |
| `scripts/test_ai_job_lifecycle.py` | `tests/server/test_ai_job_lifecycle.py` | 이동 | CI 호출 경로·`parents[1]`→`parents[2]` (형제 import는 평면 이동으로 유지) | 높음 |
| `scripts/test_ai_multi_provider.py` | `tests/server/test_ai_multi_provider.py` | 이동 | CI 호출 경로·`parents[1]`→`parents[2]` (형제 import는 평면 이동으로 유지) | 높음 |
| `scripts/test_ai_multi_provider_api.py` | `tests/server/test_ai_multi_provider_api.py` | 이동 | CI 호출 경로·`parents[1]`→`parents[2]` (형제 import는 평면 이동으로 유지) | 높음 |
| `scripts/test_ai_ops.py` | `tests/server/test_ai_ops.py` | 이동 | CI 호출 경로·`parents[1]`→`parents[2]` (형제 import는 평면 이동으로 유지) | 높음 |
| `scripts/test_ai_output_limits.py` | `tests/server/test_ai_output_limits.py` | 이동 | CI 호출 경로·`parents[1]`→`parents[2]` (형제 import는 평면 이동으로 유지) | 높음 |
| `scripts/test_ai_output_validation.py` | `tests/server/test_ai_output_validation.py` | 이동 | CI 호출 경로·`parents[1]`→`parents[2]` (형제 import는 평면 이동으로 유지) | 높음 |
| `scripts/test_ai_pr_collection.py` | `tests/server/test_ai_pr_collection.py` | 이동 | CI 호출 경로·`parents[1]`→`parents[2]` (형제 import는 평면 이동으로 유지) | 높음 |
| `scripts/test_ai_provider.py` | `tests/server/test_ai_provider.py` | 이동 | CI 호출 경로·`parents[1]`→`parents[2]` (형제 import는 평면 이동으로 유지) | 높음 |
| `scripts/test_ai_pulls_api.py` | `tests/server/test_ai_pulls_api.py` | 이동 | CI 호출 경로·`parents[1]`→`parents[2]` (형제 import는 평면 이동으로 유지) | 높음 |
| `scripts/test_ai_queue_integration.py` | `tests/server/test_ai_queue_integration.py` | 이동 | CI 호출 경로·`parents[1]`→`parents[2]` (형제 import는 평면 이동으로 유지) | 높음 |
| `scripts/test_ai_schema.py` | `tests/server/test_ai_schema.py` | 이동 | CI 호출 경로·`parents[1]`→`parents[2]` (형제 import는 평면 이동으로 유지) | 높음 |
| `scripts/test_ai_security.py` | `tests/server/test_ai_security.py` | 이동 | CI 호출 경로·`parents[1]`→`parents[2]` (형제 import는 평면 이동으로 유지) | 높음 |
| `scripts/test_ai_smoke.py` | `tests/server/test_ai_smoke.py` | 이동 | CI 호출 경로·`parents[1]`→`parents[2]` (형제 import는 평면 이동으로 유지) | 높음 |
| `scripts/test_analytics_calculations.py` | `tests/server/test_analytics_calculations.py` | 이동 | CI 호출 경로·`parents[1]`→`parents[2]` (형제 import는 평면 이동으로 유지) | 높음 |
| `scripts/test_backend_improvements.py` | `tests/server/test_backend_improvements.py` | 이동 | CI 호출 경로·`parents[1]`→`parents[2]` (형제 import는 평면 이동으로 유지) | 높음 |
| `scripts/test_desktop_api_compat.py` | `tests/server/test_desktop_api_compat.py` | 이동 | CI 호출 경로·`parents[1]`→`parents[2]` (형제 import는 평면 이동으로 유지) | 높음 |
| `scripts/test_desktop_auth.py` | `tests/server/test_desktop_auth.py` | 이동 | CI 호출 경로·`parents[1]`→`parents[2]` (형제 import는 평면 이동으로 유지) | 높음 |
| `scripts/test_desktop_migration.py` | `tests/server/test_desktop_migration.py` | 이동 | CI 호출 경로·`parents[1]`→`parents[2]` (형제 import는 평면 이동으로 유지) | 높음 |
| `scripts/test_desktop_repository_identity.py` | `tests/server/test_desktop_repository_identity.py` | 이동 | CI 호출 경로·`parents[1]`→`parents[2]` (형제 import는 평면 이동으로 유지) | 높음 |
| `scripts/test_installer_vm.ps1` | `tools/vm/test_installer_vm.ps1` | 이동 | 스크립트 간 상대 호출·`.validation/p01-vm` 경로 문자열 | 중간 |
| `scripts/test_lightweight_score.py` | `tests/server/test_lightweight_score.py` | 이동 | CI 호출 경로·`parents[1]`→`parents[2]` (형제 import는 평면 이동으로 유지) | 높음 |
| `scripts/test_metric_display.cjs` | `tests/frontend/test_metric_display.cjs` | 이동 | CI `backend-validation.yml`/`windows-desktop.yml`, 상대 require 경로 | 중간 |
| `scripts/test_metric_policy.py` | `tests/server/test_metric_policy.py` | 이동 | CI 호출 경로·`parents[1]`→`parents[2]` (형제 import는 평면 이동으로 유지) | 높음 |
| `scripts/test_pr_detail_collection.py` | `tests/server/test_pr_detail_collection.py` | 이동 | CI 호출 경로·`parents[1]`→`parents[2]` (형제 import는 평면 이동으로 유지) | 높음 |
| `scripts/test_progress_reporter.py` | `tests/server/test_progress_reporter.py` | 이동 | CI 호출 경로·`parents[1]`→`parents[2]` (형제 import는 평면 이동으로 유지) | 높음 |
| `scripts/test_repository_snapshots.py` | `tests/server/test_repository_snapshots.py` | 이동 | CI 호출 경로·`parents[1]`→`parents[2]` (형제 import는 평면 이동으로 유지) | 높음 |
| `scripts/test_validation_reports.py` | `tests/server/test_validation_reports.py` | 이동 | CI 호출 경로·`parents[1]`→`parents[2]` (형제 import는 평면 이동으로 유지) | 높음 |
| `scripts/test_viz_modules.cjs` | `tests/frontend/test_viz_modules.cjs` | 이동 | CI `backend-validation.yml`/`windows-desktop.yml`, 상대 require 경로 | 중간 |
| `scripts/test_workspace_navigation.cjs` | `tests/frontend/test_workspace_navigation.cjs` | 이동 | CI `backend-validation.yml`/`windows-desktop.yml`, 상대 require 경로 | 중간 |
| `scripts/trigger_async.py` | `tools/ops/trigger_async.py` | 이동 | 루트 계산(`__file__`) | 낮음 |
| `scripts/validate_p01_full_disk.ps1` | `tools/vm/validate_p01_full_disk.ps1` | 이동 | 스크립트 간 상대 호출·`.validation/p01-vm` 경로 문자열 | 중간 |
| `scripts/validate_p03_package.ps1` | `tools/validation/validate_p03_package.ps1` | 이동 | 내부 루트 계산, 문서 인용 | 중간 |
| `scripts/validate_p03_postgres.ps1` | `tools/validation/validate_p03_postgres.ps1` | 이동 | 내부 루트 계산, 문서 인용 | 중간 |
| `scripts/validate_p04_package.ps1` | `tools/validation/validate_p04_package.ps1` | 이동 | 내부 루트 계산, 문서 인용 | 중간 |
| `scripts/validate_p05_package.ps1` | `tools/validation/validate_p05_package.ps1` | 이동 | 내부 루트 계산, 문서 인용 | 중간 |
| `scripts/validate_p06_package.ps1` | `tools/validation/validate_p06_package.ps1` | 이동 | 내부 루트 계산, 문서 인용 | 중간 |
| `scripts/validate_p06_postgres.ps1` | `tools/validation/validate_p06_postgres.ps1` | 이동 | 내부 루트 계산, 문서 인용 | 중간 |
| `scripts/validate_p07_package.ps1` | `tools/validation/validate_p07_package.ps1` | 이동 | 내부 루트 계산, 문서 인용 | 중간 |
| `scripts/validate_p08b_package.ps1` | `tools/validation/validate_p08b_package.ps1` | 이동 | 내부 루트 계산, 문서 인용 | 중간 |
| `scripts/validate_p09_services.ps1` | `tools/validation/validate_p09_services.ps1` | 이동 | 내부 루트 계산, 문서 인용 | 중간 |
| `scripts/validate_workspace_windows.ps1` | `tools/validation/validate_workspace_windows.ps1` | 이동 | `windows-desktop.yml`·`windows-release.yml`, 문서 인용 | 높음 |
| `scripts/validation_reports.py` | `tests/server/validation_reports.py` | 이동 | CI 호출 경로·`parents[1]`→`parents[2]` (형제 import는 평면 이동으로 유지) | 중간 |
| `scripts/verify_ai_mvp_integration.py` | `tests/server/verify_ai_mvp_integration.py` | 이동 | CI 호출 경로·`parents[1]`→`parents[2]` (형제 import는 평면 이동으로 유지) | 중간 |
| `scripts/verify_analysis_sql.py` | `tools/ops/legacy/verify_analysis_sql.py` | 이동 | 루트 계산(`__file__`), 주석 내 상호 언급 | 낮음 |
| `scripts/verify_live_repository.py` | `tests/server/verify_live_repository.py` | 이동 | CI 호출 경로·`parents[1]`→`parents[2]` (형제 import는 평면 이동으로 유지) | 중간 |
| `scripts/verify_other_repo.py` | `tools/ops/legacy/verify_other_repo.py` | 이동 | 루트 계산(`__file__`), 주석 내 상호 언급 | 낮음 |
| `scripts/verify_production_integration.py` | `tests/server/verify_production_integration.py` | 이동 | CI 호출 경로·`parents[1]`→`parents[2]` (형제 import는 평면 이동으로 유지) | 중간 |
| `scripts/verify_schema_v2.py` | `tools/ops/legacy/verify_schema_v2.py` | 이동 | 루트 계산(`__file__`), 주석 내 상호 언급 | 낮음 |
| `scripts/verify_snapshot_integration.py` | `tests/server/verify_snapshot_integration.py` | 이동 | CI 호출 경로·`parents[1]`→`parents[2]` (형제 import는 평면 이동으로 유지) | 중간 |

`tests/server`로 가는 파일은 서로 **형제 import**(`import ai_test_support as support` 등)를 하므로 하위 폴더 없이 평면으로 이동한다. `tools/ops/ai_ops.py`는 `test_ai_ops.py`가 import하므로 테스트에서 `tools/ops`를 `sys.path`에 추가하는 한 줄을 같은 커밋에서 넣는다. `serve_readonly_validation`·`validation_reports`·`verify_*`(통합) 6개는 지원 모듈 `validation_reports`를 공유하므로 `tests/server`에 같이 둔다.

### 1.5 문서·기타

| 기존 경로 | 신규 경로 | 유형 | 수정할 참조 | 위험도 | 검증 방법 | 작업 |
|---|---|---|---|---|---|---|
| `docs/**` (비보관) | 유지 | 유지 | 이동한 파일을 가리키는 링크(`docs/migration/**`, `docs/operations/ai.md`, `docs/validation/**`, `docs/development/**`, `docs/deployment/**`). `docs/archive/**`는 수정 금지 | 중간 | `python -B -I docs/audit/check_documents.py` 0건 | S1~S9 각 단계 |
| `docs/index.md` | 유지 | 유지(내용) | 1·2·3차 문서 15개 연결 | 낮음 | 위와 동일("unreachable" 0건) | S1 |
| `Claude outputs/` (추적 1개) | 유지(디스크) | 추적 해제 | `git rm --cached`만. 파일 삭제 금지 | 낮음 | `git ls-files "Claude outputs"` 빈 결과, 파일은 디스크에 존재 | S1 |
| `.github/workflows/*.yml` | 유지 | 유지(내용) | 호출 경로 40곳(`scripts/` → `tests/`·`tools/`), 경로 필터, `desktop/out`, 가상환경 경로 | 높음 | 로컬에서 각 `run:` 줄을 같은 경로로 실행; 푸시 승인 후 CI 3개 통과 | S4~S9 각 단계 |
| `.claude/launch.json` (로컬·미추적) | 유지 | 로컬 수정 | `scripts/ai_ui_mock_server.py` → `tools/validation/ai_ui_mock_server.py`, 가상환경 경로. Git 대상 아님 | 낮음 | 사용자 확인 | S5 |
| `desktop/out/**`, `dist/**`, `build/**` | `build-output/**` | 이동(생성물) | 위 1.1~1.3 참조 | 중간 | 재빌드로 생성(기존 폴더는 보존) | S9 |
| `desktop/out/make-0.7.0-*` (4개, 약 4 GB) | `build-output/archive/` 또는 저장소 밖 | 보존 | **자동 이동·삭제하지 않는다**(복구 불가·대용량) — 사용자가 위치를 정하는 별도 승인 | 낮음 | 해시 목록 유지 | S9 이후 |
| `.validation/**` (58 GB) | 유지 | 유지 | CI의 `.validation/venv-workspace` → `build-output/venv-agent` (S9), 문서가 인용하는 증거 경로 불변 | 낮음 | — | S9 |
| `outputs/*.pptx` | 유지 | 유지 | 이미 Git 제외 | — | — | — |

## 2. 삭제 후보 (이동과 별개 · 4차에서 삭제하지 않음)

삭제는 이동 릴리스가 한 번 나간 뒤 **S10**에서 사용자 확인 후 수행한다. 불확실한 항목은 이 목록에 넣지 않고 보존한다.

| 대상 | 근거 | 확인 방법 | 위험 |
|---|---|---|---|
| `app/services/github_service.py` | 정의 한 줄 외 참조 없음 | `git grep GitHubService` 0건 재확인 | 낮음 |
| `app/services/ai/markdown_safety.py` | 소스 참조 없음(과거 사용처 제거 흔적) | `git grep markdown_safety` 0건 재확인 | 낮음 |
| `reports/`, `test-react/` | 빈 폴더 | 파일 0개 확인 | 매우 낮음 |
| 소스 없는 `__pycache__` 디렉터리 | 대응 `.py` 없음, 생성물 | 대응 소스 부재 재확인 | 매우 낮음 |
| `.gitignore`의 `arduino/`, `app/api/iot.py`, `.frontend_vite_backup.zip` | 디스크에 없음 | 다른 PC의 로컬 전용 파일 여부 사용자 확인 | 낮음 |
| `app/routers/__init__.py` | S7 이후 빈 패키지 | S7에서 함께 제거(삭제 후보가 아닌 이동의 일부) | 낮음 |

**삭제하지 않고 보존하는 것(1차에서 후보였으나 확정에서 제외):** `tools/ops/legacy`의 `migrate_*`·`verify_*`(스키마 대조 미완료), `cleanup_*`·`backfill_*`(운영 DB 대상일 수 있음), `test_pr_detail_collection.py`(CI에 없으나 테스트 — `tests/server`로 이동만; CI 편입 여부는 후속 결정), `staging_vm.ps1`(수동 사용 이력), `tests/workspace/*` 전부, `docs/archive/**`, `.validation/**`, `desktop/out/make-0.7.0-*`.

## 3. 단계별 변경 계획 (4차)

의존 관계: S0 → S1 → {S2, S3} → S4 → S5 → S6 → S7 → S8 → S9 → (릴리스 R1~) → S10.
(S2·S3은 서로 독립, S4는 `validate_workspace_windows.ps1`이 `scripts/`에 있는 동안 그 안의 호출 경로만 고치고, S5가 그 파일을 옮긴다.)

공통 롤백: 해당 작업의 단일 커밋을 `git revert`(새 커밋). 커밋 전이라면 작업 트리의 해당 `git mv`를 되돌린다(`git mv` 역방향). 생성물은 재생성 가능하므로 롤백 대상이 아니다.

**기준선 명령(S0에서 기록, 이후 매 작업의 검증에 동일하게 사용)**

| ID | 명령 | 출력 기록 |
|---|---|---|
| B1 | `python -B -I docs/audit/check_documents.py` | 실패 목록 |
| B2 | `python -m ruff check --config ruff-workspace.toml <기존 대상>` (명령은 `validate_workspace_windows.ps1`의 ruff 단계 그대로) | 종료 코드 |
| B3 | `python -m unittest discover -s tests/workspace` | 테스트 수·결과 |
| B4 | `npm --prefix desktop run build` + `npm --prefix desktop test` | 통과 수(현재 `squirrel.test.ts` 55개 포함) |
| B5 | 서버 테스트: `backend-validation.yml`의 `python scripts/test_*.py` 36줄을 동일 순서로 실행(테스트 DB 필요) | 줄별 종료 코드 |
| B6 | `python -m alembic heads` | `b7e1d9c3a502` |
| B7 | 에이전트 빌드 + 패키지: 파일 목록·SHA-256 목록 | 목록 파일 |
| B8 | `app.asar` 항목 목록(`listPackage`, 읽기 전용) | 985개 항목 목록 |
| B9 | `git ls-files | wc -l` | 파일 수 |

| ID | 변경 대상 | 사전 조건 | 변경 내용 | 영향받는 모듈 | 위험 | 롤백 | 검증 명령 | 성공 판정 |
|---|---|---|---|---|---|---|---|---|
| **S0** | (변경 없음) 기준선 | 작업 브랜치 생성 승인 | B1~B9를 실행해 결과를 `.validation/p11-baseline/`에 저장(Git 제외 영역). 서버 테스트용 테스트 DB 준비 확인 | — | 낮음 | 폴더 삭제 | B1~B9 | 모든 기준선이 기록됨. 기존 실패가 있으면 *기존 실패*로 표시 |
| **S1** | `docs/index.md`, `.gitignore`, 문서 15개, `Claude outputs` 추적 | S0 | 새 문서를 `docs/index.md`에 연결, 1차 `FILE_MIGRATION_CANDIDATES.md`에 "3차 계획으로 대체" 안내와 `ai_ui_mock_server.py` 정정 반영, `.gitignore`에 `/build-output/`·`Claude outputs/` 추가, 추적된 `Claude outputs` 1개를 `git rm --cached` | 문서 CI | 낮음 | revert | B1 | B1 실패 0건, `git ls-files "Claude outputs"` 빈 결과, 디스크 파일 존재 |
| **S2** | `scripts/` 운영 도구 29개 → `tools/ops`(18)·`tools/ops/legacy`(11) | S1 | 매핑 §1.4의 해당 행 이동. `test_ai_ops.py`에 `tools/ops` sys.path 추가. `docker-compose.yml` 주석·`docs/operations/ai.md`의 `scripts/ai_ops.py` 갱신 | `ai_ops`, 점검/백필 스크립트, `docs/operations/ai.md` | 낮음~중간 | revert | B1; `python -B -m py_compile tools/ops/*.py tools/ops/legacy/*.py`(문법만, 실행 금지); `python scripts/test_ai_ops.py` → 이동 후 경로로 실행 | 컴파일 오류 0, B1 통과, `test_ai_ops` 이전과 동일 결과, 운영 스크립트는 **실행하지 않음** |
| **S3** | `scripts/` VM 도구 9개 → `tools/vm` | S1 | 이동. 스크립트 간 호출·`.validation/p01-vm` 경로 문자열 점검. `.claude/launch.json`·문서 갱신 | `staging_*`, `p01_*`, `install_p01_vm`, `test_installer_vm` | 중간 | revert | PowerShell 구문 검사(`[System.Management.Automation.Language.Parser]::ParseFile`)로 9개 파싱; 내부 경로 문자열 `git grep "scripts[/\\\\]"` | 파싱 오류 0, 잔존 `scripts/` 참조 0, VM은 **실행하지 않음** |
| **S4** | 에이전트 빌드 입력·도구: `tools/build`(2), `packaging/agent`(spec+requirements 3) | S2·S3 | `git mv` 후: spec `root`와 shim 경로 수정, `build_workspace_agent.py`의 `parents[2]`와 spec 경로, `audit_workspace_agent.py` 루트 계산, `validate_workspace_windows.ps1`(여전히 `scripts/`)·CI 두 워크플로의 호출·경로 필터·설치 줄, 문서 링크 | 에이전트 동결, CI, `validate_workspace_windows.ps1`, `docs/migration/**` | **높음** | revert | B7 비교: 에이전트 빌드 실행 후 `dist` 파일 목록·`audit_workspace_agent.py` 결과; B3; 잠금 설치(`--require-hashes`)는 **새 가상환경이 필요하면 사용자 승인 후** | 에이전트 onedir 파일 목록이 B7과 동일(타임스탬프 제외), 감사 통과, 해시 파일 SHA 불변 |
| **S5** | Windows 게이트·단계별 검증·AI 목업 13개 → `tools/validation` | S4 | 이동, 각 스크립트의 루트 계산·`Join-Path` 수정, `windows-desktop.yml`·`windows-release.yml`의 호출과 경로 필터, 문서·`.claude/launch.json` 갱신 | `validate_workspace_windows.ps1`, CI | **높음** | revert | `pwsh tools/validation/validate_workspace_windows.ps1`의 단계(ruff, desktop-build, agent-freeze, host-unit, workspace-unit, web-navigation, web-regression, documents)를 B2~B4와 비교 | 모든 단계가 기준선과 동일 결과 |
| **S6** | 서버·프런트 테스트와 지원 모듈 → `tests/server`(50 파일 + 픽스처 6), `tests/frontend`(4) | S5 | 평면 이동, `parents[1]`→`parents[2]`(또는 해당 깊이), 픽스처 경로, `sys.path.insert` 5곳, CI `backend-validation.yml` 36줄·`windows-desktop.yml` 필터, 문서 인용 | 서버 테스트 전체, 프런트 테스트, CI | **높음** | revert | B5(경로만 치환해 36줄 동일 순서 실행), `node tests/frontend/test_*.cjs` 4개 | B5 줄별 종료 코드가 기준선과 동일, 프런트 4개 통과 |
| **S7** | `app/routers/auth.py` → `app/api/auth.py` | S6 | 이동, import 5개 모듈 + 테스트·스크립트·문서 갱신, `app/routers/` 제거 | 인증, 데스크톱 API | 중간 | revert | `git grep "app.routers"` 0건, B5의 인증·API 테스트, 라우트 목록 비교(`app.openapi()` 경로·메서드 집합) | 라우트 집합 동일, 테스트 동일 |
| **S8** | `_coupled_files_for`, `COUPLED_SORT_KEYS` → `app/services/coupled_files.py` | S7 | 함수 이동(본문 불변), `analytics.py`·`history_context.py`·`analysis_snapshots.py` import 갱신 | 분석 API, 스냅샷, AI 컨텍스트 | 중간 | revert | B5의 `test_analytics_calculations`·`test_repository_snapshots`·`test_ai_history_context`; 스냅샷 해시 동일 | 결과·해시 동일, `services→api` import 0건 |
| **S9** | 생성물 경로 통합 → `build-output/` | S8, 최신 안정 상태 | `build_workspace_agent.py`의 `--distpath/--workpath`, spec shim 경로, `forge.config.ts`(`extraResource`, `outDir`), CI의 `dist`·`desktop/out`·가상환경 경로, `tests/workspace/test_p06/p07_migration.py` 하드코딩, `validate_p08b_package.ps1`, `.gitignore` | 빌드 전체, CI, 일부 테스트 | **높음** | revert, 기존 `dist/`·`desktop/out/`는 삭제하지 않음 | 에이전트 빌드 → `npm run make` → 클린 VM 설치·실행·제거(cleanup 포함) → B7·B8 비교; V8 `git grep` 0건 | 설치 파일 생성, **asar 항목 목록과 `resources\agent` 구성이 B7·B8과 동일**, VM 설치·제거 통과 |
| **S10** | 삭제 후보 §2 | 이동 릴리스 1회 경과, 사용자 확인 | 확인된 항목만 삭제 | 해당 없음 | 낮음 | revert(이력에서 복원) | 각 항목 `git grep` 0건, B1~B6 | 기준선 동일 |

### 3.1 작업 순서의 이유
- 참조가 적고 되돌리기 쉬운 것(문서, 운영 도구, VM 도구)부터 시작해 절차를 검증한다.
- S4(빌드 입력)를 S5/S6(게이트·테스트)보다 먼저 한다: 게이트가 빌드 도구를 호출하기 때문이다.
- S7/S8은 코드 변경이므로 테스트 위치가 확정된 뒤(S6)에 한다.
- S9는 가장 넓게 걸치므로 마지막이며, 설치 파일 비교와 VM 검증을 반드시 거친다.
- 동작이 변하는 항목(R1~R4)은 S9 이후 릴리스 단위로 한다.

### 3.2 예상 테스트 범위
문서 CI 검사, ruff·pyright(에이전트), 에이전트 단위 테스트(`tests/workspace`), 서버 테스트 36줄(PostgreSQL 필요), 프런트 Node 테스트 4개, 데스크톱 빌드·단위(`squirrel.test.ts` 55개 포함)·E2E, 에이전트 동결·감사, 설치 파일 생성, 클린 VM 설치·제거. S2·S3은 정적 검사(컴파일·파싱)만으로 충분하며 운영/VM 스크립트는 실행하지 않는다.

## 4. 자체 검증에서 설계 단계에 반영한 수정
- 1차가 제안한 `tests/agent` 개명과 `tests/server/support` 하위 폴더는 **철회**했다(import 수정이 늘기만 함).
- `ai_ops.py`를 지원 모듈로 오인하지 않도록 `test_ai_ops.py`의 import 의존을 반영했다.
- 지원 모듈을 공유하는 통합 검증 6개를 `tools/validation`이 아니라 `tests/server`로 확정했다(교차 폴더 import 방지).
- `docker-compose.yml`의 `scripts/ai_ops.py`는 주석이었으므로 실행 영향은 없음을 확인했다.
- 설정 Roaming 여부(2차 미결정)를 AD-17로 닫았다.
