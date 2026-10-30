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
