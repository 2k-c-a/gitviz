# 4차 이동 이력

기준: [최종 이동·변경 계획](FINAL_MIGRATION_PLAN.md) · 진행 기록은 [구현 기록](IMPLEMENTATION_LOG.md)

작업별 `git mv` 결과(기존 → 신규)를 적는다. 이력은 `git log --follow <신규 경로>`로 추적한다.

(작업이 끝나는 대로 아래에 추가)

## S2
- `scripts/ai_ops.py` → `tools/ops/ai_ops.py`
- `scripts/backfill_analysis_snapshots.py` → `tools/ops/backfill_analysis_snapshots.py`
- `scripts/backfill_commit_type.py` → `tools/ops/backfill_commit_type.py`
- `scripts/backfill_pr_author_login.py` → `tools/ops/backfill_pr_author_login.py`
- `scripts/backfill_pr_large_flag.py` → `tools/ops/backfill_pr_large_flag.py`
- `scripts/check_connections.py` → `tools/ops/check_connections.py`
- `scripts/check_date_formats.py` → `tools/ops/check_date_formats.py`
- `scripts/check_job.py` → `tools/ops/check_job.py`
- `scripts/check_resolution_progress.py` → `tools/ops/check_resolution_progress.py`
- `scripts/cleanup_duplicates.py` → `tools/ops/cleanup_duplicates.py`
- `scripts/cleanup_repo10.py` → `tools/ops/cleanup_repo10.py`
- `scripts/cleanup_test_repo.py` → `tools/ops/cleanup_test_repo.py`
- `scripts/demo_incremental_read.py` → `tools/ops/demo_incremental_read.py`
- `scripts/inspect_duplicates.py` → `tools/ops/inspect_duplicates.py`
- `scripts/migrate_analysis_job_user_token.py` → `tools/ops/legacy/migrate_analysis_job_user_token.py`
- `scripts/migrate_commit_on_default_branch.py` → `tools/ops/legacy/migrate_commit_on_default_branch.py`
- `scripts/migrate_commit_type_columns.py` → `tools/ops/legacy/migrate_commit_type_columns.py`
- `scripts/migrate_contributor_aliases_unique_index.py` → `tools/ops/legacy/migrate_contributor_aliases_unique_index.py`
- `scripts/migrate_identity_search_attempted.py` → `tools/ops/legacy/migrate_identity_search_attempted.py`
- `scripts/migrate_progress_detail.py` → `tools/ops/legacy/migrate_progress_detail.py`
- `scripts/migrate_resolution_columns.py` → `tools/ops/legacy/migrate_resolution_columns.py`
- `scripts/migrate_schema_v2.py` → `tools/ops/legacy/migrate_schema_v2.py`
- `scripts/verify_analysis_sql.py` → `tools/ops/legacy/verify_analysis_sql.py`
- `scripts/verify_other_repo.py` → `tools/ops/legacy/verify_other_repo.py`
- `scripts/verify_schema_v2.py` → `tools/ops/legacy/verify_schema_v2.py`
- `scripts/run_identity_resolution.py` → `tools/ops/run_identity_resolution.py`
- `scripts/run_pr_authorship.py` → `tools/ops/run_pr_authorship.py`
- `scripts/show_summary.py` → `tools/ops/show_summary.py`
- `scripts/trigger_async.py` → `tools/ops/trigger_async.py`

## S3
- `scripts/enable_p01_hyperv.bat` → `tools/vm/enable_p01_hyperv.bat`
- `scripts/enable_p01_hyperv.ps1` → `tools/vm/enable_p01_hyperv.ps1`
- `scripts/install_p01_vm.ps1` → `tools/vm/install_p01_vm.ps1`
- `scripts/p01_boot_repair_iso.ps1` → `tools/vm/p01_boot_repair_iso.ps1`
- `scripts/prepare_p01_vm.ps1` → `tools/vm/prepare_p01_vm.ps1`
- `scripts/staging_stack.ps1` → `tools/vm/staging_stack.ps1`
- `scripts/staging_vm.ps1` → `tools/vm/staging_vm.ps1`
- `scripts/test_installer_vm.ps1` → `tools/vm/test_installer_vm.ps1`
- `scripts/validate_p01_full_disk.ps1` → `tools/vm/validate_p01_full_disk.ps1`

## S4
- `requirements-workspace-build.txt` → `packaging/agent/requirements-workspace-build.txt`
- `requirements-workspace.lock` → `packaging/agent/requirements-workspace.lock`
- `requirements-workspace.txt` → `packaging/agent/requirements-workspace.txt`
- `workspace_agent.spec` → `packaging/agent/workspace_agent.spec`
- `scripts/audit_workspace_agent.py` → `tools/build/audit_workspace_agent.py`
- `scripts/build_workspace_agent.py` → `tools/build/build_workspace_agent.py`

## S5
- `scripts/ai_ui_mock_server.py` → `tools/validation/ai_ui_mock_server.py`
- `scripts/validate_p03_package.ps1` → `tools/validation/validate_p03_package.ps1`
- `scripts/validate_p03_postgres.ps1` → `tools/validation/validate_p03_postgres.ps1`
- `scripts/validate_p04_package.ps1` → `tools/validation/validate_p04_package.ps1`
- `scripts/validate_p05_package.ps1` → `tools/validation/validate_p05_package.ps1`
- `scripts/validate_p06_package.ps1` → `tools/validation/validate_p06_package.ps1`
- `scripts/validate_p06_postgres.ps1` → `tools/validation/validate_p06_postgres.ps1`
- `scripts/validate_p07_package.ps1` → `tools/validation/validate_p07_package.ps1`
- `scripts/validate_p08b_package.ps1` → `tools/validation/validate_p08b_package.ps1`
- `scripts/validate_p09_services.ps1` → `tools/validation/validate_p09_services.ps1`
- `scripts/validate_workspace_windows.ps1` → `tools/validation/validate_workspace_windows.ps1`
