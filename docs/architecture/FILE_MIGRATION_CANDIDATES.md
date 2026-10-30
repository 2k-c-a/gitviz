# 파일 이동·삭제 후보 (1차 · 목록만, 실행 없음)

작성일: 2026-10-30 · 기준 커밋: `65f1b62` · [문서 안내](../index.md)

다른 문서: [구조 감사](PROJECT_STRUCTURE_AUDIT.md) · [현재 아키텍처](CURRENT_ARCHITECTURE.md) · [의존성 분석](DEPENDENCY_ANALYSIS.md) · [제안 아키텍처](PROPOSED_ARCHITECTURE.md)

> **3차 확정(2026-10-30):** 이동·삭제의 최종 위치와 순서는 [이동·변경 계획](FINAL_MIGRATION_PLAN.md)이 대체한다. 이 문서는 1차 분석 기록이다. 정정: `scripts/ai_ui_mock_server.py`는 로컬 `.claude/launch.json`(미추적)이 참조한다.

이 문서의 모든 항목은 **후보**이며 어떤 것도 실행하지 않았다. "참조 없음"은 추적 중인 텍스트 파일(문서 보관소 `docs/archive/` 제외)에서 파일 이름·모듈 이름이 발견되지 않았다는 뜻으로, 수동으로 실행하는 도구일 수 있다. 삭제는 항상 담당자 확인 후에 한다.

## 1. 삭제 검토 대상

| 대상 | 근거 | 실제 참조 확인 | 권고 | 위험 |
|---|---|---|---|---|
| `app/services/github_service.py` (28줄, `GitHubService`) | `get_commits`만 있는 초기 형태. 현재 GitHub 접근은 `app/collectors/github/*`가 담당 | `git grep GitHubService` → 정의 한 줄뿐. 코드·문서·CI 참조 없음 | 삭제 검토 | 낮음 |
| `app/services/ai/markdown_safety.py` (157줄) | 소스 참조 없음. 소스가 이미 삭제된 모듈(`handover_draft`, `handover_service`, `team_report_service`)의 `__pycache__`에서만 이름이 나타남 → 과거 사용처가 제거된 잔여로 추정 | 코드·문서·CI 참조 없음 | 삭제 검토 | 낮음 |
| `reports/`, `test-react/` (빈 폴더) | 파일 0개. Git은 빈 폴더를 추적하지 않음 | 참조 없음 | 삭제 검토 | 매우 낮음 |
| 소스 없는 `__pycache__` (`app/api/ai/__pycache__/{handover,teams}`, `app/schemas/__pycache__/ai_team`, `app/services/ai/__pycache__/{handover_draft,handover_service,team_access,team_report_service,team_service}`, 3.14 pyc) | 대응하는 `.py`가 없음 | Git 추적 대상 아님 | 삭제 가능 (생성물) | 매우 낮음 |
| `scripts/migrate_analysis_job_user_token.py`, `migrate_commit_on_default_branch.py`, `migrate_commit_type_columns.py`, `migrate_contributor_aliases_unique_index.py`, `migrate_identity_search_attempted.py`, `migrate_progress_detail.py`, `migrate_resolution_columns.py` (각 16~24줄) | 각 스크립트가 추가하는 컬럼·인덱스(`triggered_by_user_id`, `on_default_branch`, `commit_type_confidence/source`, `is_large_commit`, `uq_contributor_aliases_cid_email_name`, `identity_search_attempted_at`, `progress_detail`, `resolution_rule/status`)를 **모두 `migrations/schema_20261002.py`에서 확인**. 이 파일은 Alembic 리비전 4개가 사용 | 참조 없음(`migrate_contributor_aliases_unique_index`는 주석에서 `migrate_schema_v2.py`를 언급) | 삭제보다 `tools/ops/legacy/`로 이동 권고 | 낮음 (모두 `IF NOT EXISTS` 형태) |
| `scripts/migrate_schema_v2.py` (151줄) | 초기 수동 스키마 이관. `schema_20261002.py`가 같은 목적의 후속으로 보이나 **컬럼 단위 대조는 하지 않았다** | 다른 `migrate_*` 스크립트 주석에서 언급 | 대조 후 결정 (2차) | 중간 |
| `scripts/cleanup_repo10.py`, `cleanup_test_repo.py`, `cleanup_duplicates.py`, `inspect_duplicates.py` | 이름상 특정 저장소 ID·테스트 데이터용 일회성 정리 | 참조 없음 | 이동(`tools/ops/`) 후 담당자 확인, 삭제는 보류 | 중간 (운영 DB 대상 도구일 수 있음) |
| `scripts/backfill_commit_type.py`, `backfill_pr_author_login.py`, `backfill_pr_large_flag.py` | 데이터 백필 도구. `backfill_analysis_snapshots.py`는 문서 1곳이 참조하므로 제외 | 참조 없음 | 이동(`tools/ops/`) | 중간 |
| `scripts/check_connections.py`, `check_date_formats.py`, `check_job.py`, `check_resolution_progress.py`, `show_summary.py`, `demo_incremental_read.py`, `trigger_async.py`, `run_pr_authorship.py` | 점검·조회·데모용 수동 도구 | 참조 없음 | 이동(`tools/ops/`) | 낮음~중간 |
| `scripts/test_pr_detail_collection.py` | 이름은 테스트지만 CI 목록에 없음 | 참조 없음 | CI에 추가하거나 보관 이동. 현재는 **실행되지 않는 테스트** | 낮음 |
| `scripts/verify_analysis_sql.py`, `verify_other_repo.py`, `verify_schema_v2.py` | 일회성 검증 | 코드·문서 참조 없음, `Claude outputs`의 QA 보고서가 언급 | 이동 후 보류 | 낮음 |
| `scripts/ai_integration_stack.py`, `ai_ui_mock_server.py` | AI 통합/UI 목업 서버 | 참조 없음 | 이동(`tools/validation/`) | 낮음 |
| `scripts/validate_p04_package.ps1`, `validate_p06_package.ps1`, `validate_p06_postgres.ps1` | 단계별 패키지 검증. 같은 목적의 `GITVIZ_VERIFY_P04/P06` 패키지 변형과 데스크톱 E2E가 존재 | 참조 없음 (`p03/p05/p07/p08b/p09`는 문서 참조 있음) | 이동(`tools/validation/`), 증거 문서와의 연결 확인 | 중간 |
| `.gitignore`의 `app/api/iot.py`, `arduino/`, `.frontend_vite_backup.zip` | 세 경로 모두 디스크에 존재하지 않음 | — | 정리 가능 (단, 로컬 전용 파일이 다른 PC에 있을 수 있음) | 낮음 |
| `outputs/GitViz_캡스톤_발표_01_유스케이스수정.pptx` (15 MB) | 발표 자료, 코드 참조 없음, 이미 Git 제외 | 참조 없음 | 저장소 밖 보관 | 낮음 |

**삭제 후보에서 제외한 항목(참조 있음):** `app/tasks/ai_maintenance.py`(Celery `include` 문자열), `scripts/ai_credential_support.py`·`ai_input_support.py`(테스트 9곳이 import), `scripts/staging_vm.ps1`(추적 파일 참조는 없지만 이전 작업 세션에서 `-Mode Down`으로 수동 사용한 이력이 있음).

## 2. 이동 후보

개수는 파일 이름 접두어 기준 근사치다(일부 파일은 두 분류에 걸친다).

### 2.1 `scripts/` → `tools/` + `tests/`

| 현재 | 권장 | 개수 | 같은 변경에서 고쳐야 할 것 | 위험 |
|---|---|---:|---|---|
| `scripts/test_*.py` | `tests/server/` | 35 | `.github/workflows/backend-validation.yml` 36줄; 지원 모듈(`ai_test_support`, `ai_credential_support`, `ai_input_support` 등)이 **같은 폴더 형제 import**(`import ai_test_support as support`)와 `Path(__file__).parents[1]`로 루트를 찾으므로 import·루트 계산 수정 | 높음 |
| `scripts/test_*.cjs` | `tests/frontend/` | 4 | CI(`test_metric_display.cjs`, `test_ai_frontend.cjs`), `windows-desktop.yml` 경로 필터(`test_workspace_navigation.cjs`) | 중간 |
| `scripts/ai_*.py` (지원·가짜 제공자·통합 앱) | `tests/server/support/` | 약 11 | 위와 동일(형제 import, `sys.path.insert` 5곳) | 높음 |
| `scripts/validate_workspace_windows.ps1` | `tools/validation/` | 1 | `windows-desktop.yml`(실행 1곳 + 경로 필터), `windows-release.yml`(실행 1곳) | 높음 (Windows 게이트) |
| `scripts/validate_p0*.ps1`, `verify_*.py`, `smoke_ai_live.py`, `validation_reports.py`, `serve_readonly_validation.py` | `tools/validation/` | 약 20 | 문서 인용 경로(`docs/…`), `scripts/test_validation_reports.py`(CI) | 중간 |
| `scripts/build_workspace_agent.py`, `audit_workspace_agent.py` | `tools/build/` | 2 | `parents[1]` → `parents[2]`; `pyright-workspace.json`의 `include`; `ruff` 명령행 경로; `validate_workspace_windows.ps1`; `docs` 인용 | 높음 |
| `scripts/backfill_*`, `cleanup_*`, `check_*`, `inspect_*`, `migrate_*`, `show_*`, `demo_*`, `run_*`, `trigger_*` | `tools/ops/` (`legacy/` 하위 구분) | 약 26 | 참조 없음 — 단, 각 스크립트가 루트를 `parents[1]`로 계산하는지 확인 | 낮음~중간 |
| `scripts/p01_*.ps1`, `install_p01_vm.ps1`, `enable_p01_hyperv.*`, `prepare_p01_vm.ps1`, `staging_*.ps1`, `test_installer_vm.ps1`, `validate_p01_full_disk.ps1` | `tools/vm/` | 약 9 | 스크립트끼리의 상대 호출, `.validation/p01-vm` 자격 증명 경로, 문서 | 중간 (내부 경로 문자열 확인 필요) |
| `scripts/fixtures/` | 소비자와 함께 이동 | 6 | — | 낮음 |

> 주의(확인): 루트 계산에 `__file__`을 쓰는 구문이 `scripts/*.py`에 77곳 있다. 폴더 깊이를 바꾸면 전부 영향 대상이다. 이동 전 일괄 점검표가 필요하다.

### 2.2 테스트

| 현재 | 권장 | 같은 변경에서 고쳐야 할 것 | 위험 |
|---|---|---|---|
| `tests/workspace/` | `tests/agent/` | `validate_workspace_windows.ps1`(`unittest discover -s`), `pyright-workspace.json`, `ruff` 경로, `windows-desktop.yml` 경로 필터, 테스트 간 절대 import(`from tests.workspace.test_apply import …` 등, `fixture_*.py`) | 중간 |

### 2.3 서버 코드

| 현재 | 권장 | 같은 변경에서 고쳐야 할 것 | 위험 |
|---|---|---|---|
| `app/routers/auth.py` | `app/api/auth.py` | `app/main.py`, `app/api/ai/dependencies.py`, `app/api/desktop/repositories.py`, `app/api/repositories.py`, `app/api/snapshot_route.py` import; 테스트·스크립트의 `app.routers.auth` 참조; 문서 링크 | 중간 |
| `app/api/analytics.py`의 `_coupled_files_for`, `COUPLED_SORT_KEYS` | `app/services/` 내 모듈 | `app/services/ai/history_context.py:123`, `app/services/analysis_snapshots.py:207·358`; 스냅샷 해시 회귀 테스트 | 중간 |
| `app/core/database.py` + `app/database.py` | `app/core/db/` | `migrations/env.py`, `app/models/*` 23곳, `app/routers/auth.py`, 스크립트·테스트 다수 | **높음** (제안 단계에서 보류 권장) |
| `app/workspace/` | `agent/gitviz_agent/` | `workspace_agent.spec`, `build_workspace_agent.py`, `pyright`/`ruff` 설정, CI 경로 필터, 데스크톱 문서, `tests/workspace/*`의 import 전부 | **높음** (2차 분석 후) |
| `app/services/ai/{token_budget,providers/base,providers/registry,content_policy,input_builder,validators,difftext,prompts}` | `shared/ai_core/` | 서버의 `app.services.ai.*` import 전부, spec의 `hiddenimports`/`datas` | **높음** |

### 2.4 문서·비코드

| 현재 | 권장 | 비고 | 위험 |
|---|---|---|---|
| `Claude outputs/` (추적 1 + 미추적 5) | 저장소 밖 또는 `.gitignore` | 추적 중인 파일을 제거하려면 별도 커밋이 필요하다. 문서 CI의 보존 기준선에는 포함되지 않음 | 낮음 |
| 이 문서군 5개 | `docs/architecture/` 유지 | `docs/index.md`에 링크 추가 필요([구조 감사](PROJECT_STRUCTURE_AUDIT.md) §6) | 낮음 |
| `desktop/out/make-0.7.0-*` 4개, 검증용 패키지 7개 | 저장소 밖 보관 | 생성물, Git 제외 | 낮음 |
| `.validation/` 대용량 하위(`p01-vm`, `p08-vm`, `iso`) | 외부 저장소 | 문서가 인용하는 증거 경로를 먼저 확인 | 중간 |

## 3. 기존 경로 ↔ 권장 경로 대응표

| 기존 | 권장 | 단계 |
|---|---|---|
| `scripts/test_*.py` | `tests/server/test_*.py` | B |
| `scripts/test_*.cjs` | `tests/frontend/` | B |
| `scripts/ai_*.py` | `tests/server/support/` | B |
| `scripts/build_workspace_agent.py`, `audit_workspace_agent.py` | `tools/build/` | B |
| `scripts/validate_*.ps1`, `verify_*.py`, `smoke_*` | `tools/validation/` | B |
| `scripts/{backfill,cleanup,check,inspect,migrate,show,demo,run,trigger}_*` | `tools/ops/` (`legacy/`) | A–B |
| `scripts/{p01,staging,install_p01_vm,enable_p01_hyperv,prepare_p01_vm}*` | `tools/vm/` | B |
| `tests/workspace/` | `tests/agent/` | B–C |
| `app/routers/auth.py` | `app/api/auth.py` | B |
| `app/workspace/` | `agent/gitviz_agent/` | C |
| `app/services/ai/{공유 7 + prompts}` | `shared/ai_core/` | C |
| `app/database.py`, `app/core/database.py` | `app/core/db/` | D |
| 나머지 (`desktop/`, `frontend/`, `contracts/`, `migrations/`, `docs/`, 루트 설정, `build/`, `dist/`) | 유지 | — |

## 4. 이전 시 주의사항

1. **한 번에 한 영역, 경로 수정은 같은 커밋에서.** 이동과 CI 수정이 분리되면 중간 상태에서 CI가 깨진다.
2. **`git mv`로 이력을 보존**하고, `docs/archive/originals/**`는 `-text` 속성과 보존 기준선이 있으므로 건드리지 않는다. 문서 이동은 `docs/audit/relocation-map.json` 규칙을 따른다.
3. 이동 전에 `python -B docs/audit/check_documents.py`, 서버 회귀 스크립트, Windows 에이전트 검증(`validate_workspace_windows.ps1`)의 현재 결과를 **기준선으로 기록**한다. 이동 후 같은 결과여야 한다.
4. 에이전트 관련 이동(Phase C)은 PyInstaller 빌드와 설치 프로그램 생성, 클린 VM 설치·제거까지 다시 검증해야 한다. 에이전트가 번들에 포함되는지는 `resources\agent\gitviz-workspace-agent.exe`의 존재로 확인한다.
5. 환경변수 이름과 `.env.example`은 구조 변경과 무관하게 **불변**으로 둔다. 배포 환경의 `.env`를 바꾸지 않아도 되어야 한다.
6. 삭제는 이동보다 나중에 한다. 이동 후 한 릴리스 동안 참조가 없는지 확인한다.
7. `.validation/`과 `desktop/out/` 정리는 저장소 구조 변경과 분리해서 별도 승인으로 진행한다(복구 불가).
