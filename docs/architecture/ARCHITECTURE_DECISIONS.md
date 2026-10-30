# 아키텍처 결정 기록 (3차 확정)

작성일: 2026-10-30 · 기준 커밋: `65f1b62` · 상태: **확정 설계 (미적용)**

함께 읽기: [최종 아키텍처](FINAL_ARCHITECTURE.md) · [디렉터리 구조](FINAL_DIRECTORY_TREE.md) · [이동 계획](FINAL_MIGRATION_PLAN.md)

1·2차 문서에서 같은 대상이 여러 위치로 제안되었거나 "결정 필요"로 남은 항목을 하나씩 결정한다. 각 결정은 **번복하려면 사용자 승인이 필요**하다. 4차 작업자는 여기 없는 대안을 선택하지 않는다.

## 결정 요약

| ID | 주제 | 결정 | 근거 요약 |
|---|---|---|---|
| AD-01 | 서버 패키지 위치 | `app/` 유지, `backend/` 미도입 | import 835줄·Alembic·Celery 문자열·CI 변경 비용 대비 이득 없음 |
| AD-02 | 에이전트 분리 / `shared/` | `app/workspace` 물리 분리 안 함, `shared/` 미생성 | 경계는 테스트와 PyInstaller `excludes`로 이미 보호됨. 분리 효용 < 위험 |
| AD-03 | 공유 계약 위치 | `contracts/` 유지 | 이미 유일한 계약 위치. 코드 공유와 계약 공유를 섞지 않음 |
| AD-04 | desktop / frontend 책임 | 유지. 복사 목록은 매니페스트화(R2) | 폴더 이동 없이 새 화면마다 빌드 스크립트 수정 문제만 해소 |
| AD-05 | migrations | 루트 유지 | `alembic.ini`·`migrations/env.py`·단일 head 보존. 이력 불변 |
| AD-06 | tests 분리 | `tests/server`, `tests/frontend`, `tests/workspace`(이름 유지), 평면 이동 | 형제 import 유지로 import 수정 최소화. 개명 이득 없음 |
| AD-07 | scripts / tools | `scripts/` 폐지 → `tools/{build,validation,ops,vm}` + `ops/legacy` | 성격별 분리. 삭제 없이 이동만 |
| AD-08 | packaging / spec | `packaging/agent/`에 spec + 잠금 의존성 | 에이전트 빌드 입력을 한 폴더로 묶음 |
| AD-09 | build / dist | `build-output/` 단일 생성물 루트 | 무시·백업·외부 보관 정책을 한 곳에서 관리 |
| AD-10 | storage | `storage/repos/` 유지(Git 제외), 설정화는 R1 | 서버 런타임 데이터. 구조 변경과 무관 |
| AD-11 | outputs / reports / Claude outputs | 아래 상세 | 제품과 무관한 산출물 분리 |
| AD-12 | docs 구조 | 유지, `docs/index.md`에 새 문서 연결 | 문서 CI가 index 도달성을 검사 |
| AD-13 | 환경 변수 파일 | 개발용 `.env` 한 개만. 테스트·운영은 파일 없이 프로세스 환경변수 | 비밀 파일이 운영·클라이언트로 흘러가는 경로 차단 |
| AD-14 | 가상환경 | 서버 개발 `.venv/`, 에이전트 빌드 `build-output/venv-agent/` | 용도별 분리, 빌드 환경은 잠금 파일로만 생성 |
| AD-15 | 빌드 의존성 | Python `packaging/agent/*.lock`(해시), Node `desktop/package-lock.json`, Node `>=24.20.0 <25` | 현재 CI 방식을 로컬에도 강제 |
| AD-16 | 운영 설정 | 서버는 프로세스 환경변수만, 클라이언트는 빌드 시 `desktop/channel.json` | 비밀과 공개 설정 분리 |
| AD-17 | 사용자 데이터 경로 | 단일 루트 `%LOCALAPPDATA%\GitViz\Workspace`, Roaming 폐기 | 설정 Roaming 안(2차 미결정)을 기각: DPAPI·SQLite는 기기 전용이라 동기화 이득 없음 |
| AD-18 | 설치 경로 | `%LOCALAPPDATA%\GitVizWorkspace` 유지 | 표준 사용자 권한·Squirrel 업데이트. 데이터 경로와 별도 작업 |
| AD-19 | 서버 코드 정리 범위 | `routers/auth`→`api/auth`, 분석 계산 이동만. DB 엔진 통합·설정 전면 통합 제외 | 나머지는 동작 위험이 높고 구조 변경과 분리해야 함 |
| AD-20 | 삭제 | 4차에서 삭제하지 않음. 이동 후 한 릴리스 경과 뒤 S10 | 불확실하면 보존 |
| AD-21 | `infrastructure/` | 만들지 않음 | 파일이 compose 1개 |
| AD-22 | 4차의 성격 | 구조 변경만(동작 불변). 동작 변경은 R1~R4로 분리 | 문제 발생 시 원인 분리와 롤백 단순화 |

## 상세

### AD-01 · AD-02 에이전트와 서버
1차는 "에이전트 분리(Phase C)"를 보류 기본으로 두었다. 확정: **분리하지 않는다.**
- 경계는 `tests/workspace/test_import_boundary.py`와 spec의 `excludes`로 이미 검증된다.
- 분리하면 `workspace_agent.spec`의 `hiddenimports`/`datas`, 서버의 `app.services.ai.*` import, pyright/ruff 대상, CI 경로 필터, 에이전트 테스트 import 전체가 동시에 바뀌며 PyInstaller·설치·VM 검증을 다시 해야 한다.
- 공유 7개 모듈은 서버 안에 두고 에이전트가 정적 3 + `importlib` 동적 5로 사용한다. 이 관계는 유지한다.
- 재검토 조건: 에이전트를 서버와 다른 저장소/릴리스 주기로 분리해야 할 때.

### AD-06 tests 구조
`scripts/test_*.py`는 `import ai_test_support as support`처럼 **같은 폴더의 형제를 import**한다. 하위 폴더로 쪼개면 import를 모두 고쳐야 하므로 `tests/server/`에 **평면으로** 옮긴다(루트 계산 `parents[1]`→`parents[2]`만 수정). `tests/workspace`는 모듈 이름 `tests.workspace.*` 절대 import가 있고 `app/workspace`와 짝이므로 이름을 바꾸지 않는다(1차 제안 `tests/agent`는 철회).

### AD-07 scripts 분배 기준
| 성격 | 위치 |
|---|---|
| 자동 테스트, 그 지원 모듈과 픽스처, 지원 모듈을 import하는 통합 검증(`validation_reports`·`verify_ai_mvp_integration`·`verify_live_repository`·`verify_production_integration`·`verify_snapshot_integration`·`serve_readonly_validation`·`evaluate_ai_review`) | `tests/server` (서로 형제 import) |
| Node 테스트 4개 | `tests/frontend` |
| PyInstaller 빌드·감사 | `tools/build` |
| Windows 게이트와 단계별 패키지 검증, AI 목업 서버 | `tools/validation` |
| 수동 운영·백필·정리·점검 도구, `ai_ops.py` | `tools/ops` |
| 일회성 마이그레이션·검증 | `tools/ops/legacy` |
| Hyper-V·스테이징 | `tools/vm` |

`ai_ops.py`는 `test_ai_ops.py`가 import하지만 `docs/operations/ai.md`와 `docker-compose.yml` 주석(점검 명령 안내)이 운영 점검 도구로 지칭하므로 `tools/ops`에 둔다. 이동 시 테스트의 import 경로(`sys.path`)와 문서·주석을 같은 커밋에서 고친다.

### AD-08 · AD-15 packaging
`workspace_agent.spec`은 `root = Path(SPECPATH)`를 저장소 루트로 가정한다. `packaging/agent/`로 옮기면 `root = Path(SPECPATH).parents[1]`로 바꾸고, 생성되는 진입 shim(`build/workspace-agent/entry.py`)은 `build-output/agent/work/entry.py`로 옮긴다. 잠금 파일(`requirements-workspace.lock`)은 해시 고정이며 내용을 바꾸지 않는다(파일 이동만; 경로가 `-r` 상대 참조인 `requirements-workspace-build.txt`는 같은 폴더로 같이 이동하므로 유효).

### AD-09 build-output
- 에이전트: `build-output/agent/{work,dist}` (`dist/agent`가 `extraResource`).
- 데스크톱: forge `outDir` = `build-output/desktop`. 검증용 변형 패키지도 같은 곳.
- 이전 빌드(`desktop/out/make-0.7.0-*` 4개 약 4 GB)는 `build-output/archive/`로 옮기거나 저장소 밖으로 보관한다 (복구 불가 작업이므로 별도 승인, S9에 포함하지 않음).
- `tests/workspace/test_p06_migration.py`·`test_p07_migration.py`는 `desktop/out/GitVizWorkspaceP0xVerify-win32-x64`를 하드코딩하므로 같은 커밋에서 갱신한다.

### AD-11 비코드 산출물
| 대상 | 결정 |
|---|---|
| `outputs/` | 이미 Git 제외. 위치 유지, 저장소 밖 이전은 사용자 몫 |
| `Claude outputs/` | `.gitignore`에 추가하고 추적 중인 1개는 `git rm --cached`(파일은 디스크에 유지). **커밋하지 않는다는 기존 원칙과 일치** |
| `reports/`, `test-react/` | 빈 폴더. 삭제 후보(S10), 영향 없음 |
| `certs/` | Git 제외 유지. 내용은 열지 않음 |
| `.validation/` | 유지. 58 GB 외부 이전은 별도 승인 작업 |

### AD-13 · AD-16 설정
환경별 상세는 [환경 매트릭스](../deployment/FINAL_ENVIRONMENT_MATRIX.md). 핵심: `load_dotenv`는 이미 설정된 환경변수를 덮어쓰지 않으므로 우선순위는 *프로세스 환경 > `.env` > 코드 기본값*이다. `GITVIZ_ENV=production`에서는 `.env`를 읽지 않는다(R1). 클라이언트의 `GITVIZ_SERVER_ORIGIN` 환경변수 덮어쓰기는 `app.isPackaged === false`일 때만 허용한다(R1).

### AD-17 사용자 데이터
2차 문서의 "설정은 Roaming?" 미결정을 닫는다. **전부 Local.** DB와 키가 기기 고정(DPAPI)이므로 설정만 로밍하면 다른 PC에서 키 없는 반쪽 상태가 된다. 데이터 루트는 설치 루트와 **형제 폴더**(`GitViz\Workspace`)여야 한다: 제거 정리(`cleanupPlan`)가 `GitVizWorkspace` 루트를 통째로 삭제하기 때문이다. 이전은 R3, 방법은 [릴리스 이행 계획](../deployment/RELEASE_MIGRATION_PLAN.md).

### AD-19 서버 코드 정리 범위
포함: `app/routers/auth.py`→`app/api/auth.py`(import 변경 5개 모듈 + 테스트/문서), `app/api/analytics.py`의 `_coupled_files_for`·`COUPLED_SORT_KEYS`를 서비스 모듈로 이동(역방향 의존 제거, 스냅샷 해시 회귀 테스트로 보호).
제외(후속, 4차 아님): 동기/비동기 DB 엔진 통합(`Base` metadata·Alembic autogenerate·프로덕션 스키마 영향), `os.getenv` 전면 통합, 로깅 전면 도입. `GITVIZ_ENV` 가드는 R1에서 최소 형태로만 도입한다.

### AD-22 구조 변경과 동작 변경의 분리
4차(S0~S9)는 **사용자가 보는 동작과 설치 산출물 내용이 같아야** 한다. 판정: 이동 전후 `app.asar` 항목 목록·`resources\agent` 구성·마이그레이션 head·테스트 결과가 동일. 동작이 바뀌는 항목(환경 가드, 채널 설정, asar 허용 목록, 데이터 경로 이전)은 릴리스 단위(R1~R4)로 분리해 롤백 단위를 작게 유지한다.
