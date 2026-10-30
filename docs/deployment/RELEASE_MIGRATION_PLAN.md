# 릴리스 이행 계획: 빌드·배포 통합과 사용자 데이터 이전 (3차 확정)

작성일: 2026-10-30 · 기준 커밋: `65f1b62` · 상태: **확정 설계 (미적용)**

함께 읽기: [최종 아키텍처](../architecture/FINAL_ARCHITECTURE.md) · [이동 계획](../architecture/FINAL_MIGRATION_PLAN.md) · [환경 매트릭스](FINAL_ENVIRONMENT_MATRIX.md) · [결정 기록](../architecture/ARCHITECTURE_DECISIONS.md) · [빌드와 릴리스](BUILD_AND_RELEASE.md) · [설치 안내](INSTALLATION_GUIDE.md) · [데이터 저장 정책](DATA_STORAGE_POLICY.md)

원칙: **구조 변경(4차 S0~S9) → 동작 변경 릴리스(R1~R4)**를 섞지 않는다. 설치 경로 변경과 사용자 데이터 경로 변경은 별도 작업이며, 이번 계획에서 **설치 경로는 변경하지 않는다**(AD-18). 업로드·서명·배포는 매번 별도 승인이다.

## 1. 빌드·배포 통합 설계

### 1.1 빌드 구성요소 확정

| 항목 | 현재 | 최종 | 변경 시점 |
|---|---|---|---|
| 에이전트 빌드 진입점 | `scripts/build_workspace_agent.py` | `tools/build/build_workspace_agent.py` | S4 |
| spec 파일 | `workspace_agent.spec` (루트) | `packaging/agent/workspace_agent.spec` (`root = Path(SPECPATH).parents[1]`) | S4 |
| PyInstaller work 경로 | `build/workspace-agent` (spec의 shim 포함) | `build-output/agent/work` | S9 |
| 에이전트 산출 경로 | `dist/workspace-agent/agent` | `build-output/agent/dist/agent` | S9 |
| 빌드 의존성(Python) | 루트 `requirements-workspace{,-build}.txt`, `.lock` | `packaging/agent/` 같은 이름 | S4 |
| 빌드 가상환경 | `.validation/venv-workspace` (CI 기준) | `build-output/venv-agent` | S9 |
| 빌드 의존성(Node) | `desktop/package-lock.json`, Node `>=24.20.0 <25` | 동일 | — |
| 프런트엔드 산출물 | `desktop/dist/…`(tsc) + `desktop/assets`(build.mjs가 `frontend` 일부 복사) | 동일. 복사 목록 매니페스트화 | R2 |
| 런타임 리소스 경로 | `resources\agent\gitviz-workspace-agent.exe` | 동일(`extraResource`가 `build-output/agent/dist/agent`를 가리킴) | S9 |
| 최종 패키지 경로 | `desktop/out/GitVizWorkspace-win32-x64` | `build-output/desktop/GitVizWorkspace-win32-x64` | S9 |
| Setup.exe 생성 위치 | `desktop/out/make/squirrel.windows/x64/GitVizWorkspaceSetup.exe` | `build-output/desktop/make/squirrel.windows/x64/GitVizWorkspaceSetup.exe` | S9 |
| 설치 후 실행 경로 | `%LOCALAPPDATA%\GitVizWorkspace\app-<버전>\GitVizWorkspace.exe` | 동일 | — |
| 버전 관리 | `desktop/package.json`의 `version` (현재 0.7.0) | 동일, 릴리스당 1회 증가 | R1~R4 |
| 검증용 패키지 변형 | `forge.config.ts` 안의 `GITVIZ_VERIFY_*` 8개 분기 | `forge.verify.config.ts`로 분리 | R2 |

**기존 빌드와 달라지는 점(4차 이후):** 모든 생성 경로가 `build-output/` 아래로 이동, 빌드 입력이 `packaging/agent/`로 이동, 도구 경로가 `tools/`로 이동. **달라지지 않는 점:** 명령의 의미(freeze → `npm run build` → `npm run make`), 잠금 파일 내용, 설치 프로그램 구성(Squirrel, per-user, `noMsi`), 설치 결과물의 내용.

### 1.2 배포 제외 목록 (제품 asar·설치본에 들어가면 안 되는 것)

현재 `app.asar`(985개 항목)에 `typescript`(156개 항목)와 `dist/tests`가 포함된다(2차 확인). 제품에서 제외할 대상:

| 대상 | 현재 | 조치 | 시점 |
|---|---|---|---|
| `node_modules/typescript` | 포함 (`dependencies`에 있음) | `devDependencies`로 이동하거나 packager `ignore`로 제외. 런타임에 필요한지 `desktop/src` import 확인 후 결정 | R2 |
| `dist/tests/**` | 포함 | packager `ignore` | R2 |
| `*.map`, `.env*`, `forge.*.ts`, 소스 `src/` | 확인 필요 | 허용 목록 검사로 판정 | R2 |
| 에이전트의 서버 모듈 | `excludes`로 차단 | 유지 (`test_import_boundary.py`) | — |

방식은 **허용 목록**(들어가도 되는 최상위 경로만 명시)이며 위반 시 빌드를 실패시킨다.

### 1.3 서명·무결성
- 기본은 미서명(현재). 인증서(`GITVIZ_SIGN_PFX*`)가 있으면 서명하되 서명 대상 범위(Setup.exe만 vs 앱 exe·에이전트 exe 포함)는 **미검증**이므로 R2에서 확인한다.
- 배포 시 `SHA256SUMS`(Setup.exe, nupkg, RELEASES)를 함께 게시한다(R2).

## 2. 릴리스 열차

| ID | 버전 | 내용 | 선행 | 사용자 영향 |
|---|---|---|---|---|
| (S0~S9) | 변경 없음 | 4차 구조 변경. 산출물 내용 동일 | 승인 | 없음 |
| R1 | 0.7.1 | `GITVIZ_ENV`와 DB 가드, `.env` 정책, `channel.json`, 비패키지 전용 개발 변수, 서버 `storage` 경로 설정, `migrated.marker` 감지 시 경고·읽기 전용 처리(R3 다운그레이드 방어) | S9 | 없음(내부 가드). 데이터가 있는 상태에서의 업그레이드 검증 기회(0.7.0→0.7.1 마커 파일 업그레이드는 2026-10-29 VM에서 확인됨) |
| R2 | 0.7.2 | asar 허용 목록·제외 목록, `forge.verify.config.ts` 분리, 빌드 환경 잠금 검사, `SHA256SUMS`, 디버그 인수 차단, `build.mjs` 매니페스트화, 서명 범위 확인 | R1 | 설치 파일 크기 감소 |
| R3 | 0.8.0 | **사용자 데이터 경로 이전** (§3) | R2 | 첫 실행 시 데이터 복사. 가장 위험한 릴리스 |
| R4 | 선택 | 무인 제거 대응(WMI), 제거 시 데이터 삭제 선택 UI, 코드 서명 도입 | R3 | — |

각 릴리스는 클린 VM(체크포인트 `pre-p10-installer`)에서 **신규 설치, 이전 버전 위 업그레이드, 제거**를 모두 통과해야 하며, 업로드는 별도 승인이다.

## 3. 사용자 데이터 이전 (R3)

### 3.1 경로 확정

> 4차 S0 실측(2026-10-30): 에이전트 파일(`workspace.sqlite3`, `key.dpapi`, `secret-check.bin`, `window.json`, `writer.lock`)은 `%APPDATA%\gitviz-workspace\workspace\` 하위에 있고, 상위 `gitviz-workspace\`에는 Chromium 생성물이 있다. 아래 표의 `…`은 이 `workspace\` 폴더를 뜻한다.

| 데이터 | 기존 경로 | 신규 경로 | 비고 |
|---|---|---|---|
| 개발 DB (서버) | `.env`의 `DATABASE_URL`이 가리키는 compose PostgreSQL | 변경 없음 | 구조·릴리스와 무관. 이전하지 않음 |
| 테스트 DB | `gitviz_test*` (CI·로컬) | 변경 없음 | 임시. 이전하지 않음 |
| 개발 실행의 클라이언트 데이터 | `%APPDATA%\gitviz-workspace` (운영과 공유 가능) | `build-output/dev-data/` | R1에서 `GITVIZ_USER_DATA_DIR`로 분리. 기존 데이터는 그대로 둔다 |
| 사용자 DB | `%APPDATA%\gitviz-workspace\workspace\workspace.sqlite3`(+wal/shm) | `%LOCALAPPDATA%\GitViz\Workspace\data\workspace.sqlite3` | 복사·검증 후 전환 |
| 암호 키/검증 | `…\key.dpapi`, `secret-check.bin` | `…\data\` | DB와 반드시 짝으로 이전 |
| 실행 잠금 | `…\writer.lock` | `…\data\writer.lock` | 이전하지 않고 새로 생성 |
| 사용자 설정 | `…\window.json` (관찰된 유일한 설정 파일) | `…\data\window.json` | `settings.json`은 새 기능이 생길 때 도입 |
| 로그 | 앱 로그 파일은 관찰되지 않음(Squirrel 로그는 `%LOCALAPPDATA%\SquirrelTemp`) | `…\logs\` | 이전할 데이터 없음 |
| 캐시 | `…\Cache`, `Code Cache`, `GPUCache`, `DawnGraphiteCache` 등 Chromium 생성물 | `…\cache\` | **이전하지 않음**(재생성) |
| 백업 | 업그레이드 시 만들어지는 `pre-v8-{uuid}.sqlite3` (위치는 DB 옆으로 **추정**) | `…\backups\` | R3에서 실제 위치를 코드로 확인 후 복사 |
| 사용자 생성 파일 | 앱이 사용자 폴더에 만드는 산출물은 **확인되지 않음** | 사용자 선택 위치 | 이전 대상 없음. 발견 시 R3 전에 목록 보완 |

### 3.2 이전 절차 (복사 → 검증 → 전환 → 보존)

오케스트레이션은 Electron main(`app.setPath('userData')`를 정하기 전), DB 복사는 **에이전트의 SQLite backup API**가 수행한다(파일 복사는 WAL 상태에서 불완전할 수 있음).

1. **감지:** 신규 루트에 `data\workspace.sqlite3`가 없고 기존 경로에 있으면 이전 대상. 둘 다 있으면 신규를 정본으로 사용하고 기존은 건드리지 않는다.
2. **사전 점검:** 에이전트가 실행 중이 아님(`writer.lock` 확보 가능), 신규 드라이브 여유 공간 ≥ 기존 DB 크기의 2배.
3. **복사:** backup API로 DB를 신규 임시 이름(`workspace.sqlite3.migrating`)에 기록, `key.dpapi`·`secret-check.bin`·`window.json` 복사. 기존 파일은 **수정·이동하지 않는다**.
4. **검증:** `PRAGMA integrity_check = ok`, 스키마 버전 일치, 키로 `secret-check.bin` 복호화 성공, 핵심 테이블 행 수 일치.
5. **전환:** 임시 이름을 최종 이름으로 바꾸고 `migrated.json`(원본 경로·시각·버전·행 수)을 기록한 뒤 신규 경로로 실행.
6. **보존:** 기존 폴더에는 `migrated.marker`만 추가한다. 자동 삭제 금지. 정리는 안내 후 사용자 확인으로만(R4 이후).
7. **실패 시:** 신규 임시 파일을 지우고 이번 세션은 기존 경로로 실행(`UNSUPPORTED_PROTOCOL`/`RECOVERY_REQUIRED` 흐름은 그대로). 다음 실행에서 재시도한다. 실패 사유는 로그에 남긴다(비밀 제외).

### 3.3 업그레이드·다운그레이드 경로
- 0.7.x → 0.8.0: Squirrel이 새 `app-0.8.0`을 설치, 첫 실행 시 §3.2. 설치 경로 불변.
- **다운그레이드는 지원하지 않는다.** 0.8.0 이후 0.7.x를 실행하면 기존 경로의 *이전 시점* 데이터로 시작해 두 데이터가 갈라진다. 방어: `migrated.marker` 존재를 0.7.x가 알지 못하므로(코드 수정 불가), 릴리스 노트에 명시하고 R1(0.7.1)에 "marker가 있으면 경고하고 읽기 전용" 처리를 **선행 도입**한다. → 이 선행 도입을 R1 범위에 포함한다.
- 새 PC/다른 Windows 사용자로 복사된 데이터는 DPAPI 키 때문에 열리지 않는다(`RECOVERY_REQUIRED`). 이전 대상이 아니다.

### 3.4 제거와의 관계
제거 정리는 `%LOCALAPPDATA%\GitVizWorkspace`만 삭제한다. 신규 데이터 루트는 `%LOCALAPPDATA%\GitViz\Workspace`로 **형제 폴더**이므로 제거 후에도 남는다(현재 Roaming 데이터가 남는 것과 같은 동작). `cleanupPlan`의 안전 루트 검사가 이 형제 폴더를 건드리지 않음을 R3 테스트에 포함한다.

### 3.5 검증 (R3)
| 시나리오 | 방법 | 합격 |
|---|---|---|
| 신규 설치 | 클린 VM | 신규 경로에 생성, 기존 경로 없음 |
| 0.7.x 데이터 있음 → 0.8.0 | VM에 0.7.x로 데이터 생성 후 업그레이드 | 행 수·키 동일, 기존 폴더 보존 |
| 중간 실패 | 복사 도중 프로세스 종료, 디스크 부족, 읽기 권한 오류 주입 | 기존 데이터 무손상, 다음 실행에서 재시도 성공 |
| 둘 다 존재 | 두 경로에 DB 존재 | 신규 정본, 기존 무변경 |
| 제거 | 제거 후 | 신규 데이터 유지, 설치 루트 잔여 0 |
| WAL 상태 | 비정상 종료 직후 이전 | 마지막 커밋까지 보존 |

## 4. 롤백
- 4차 S#: 작업 단위 커밋 되돌리기(`git revert <커밋>`). 파괴적 명령(`reset --hard`, `clean`)은 사용하지 않는다.
- R1/R2: 이전 버전 Setup.exe 재배포(릴리스 자산 유지). 데이터 변경 없음.
- R3: 데이터 원본이 보존되므로 앱만 롤백하면 기존 경로 데이터로 복귀한다. 단, 롤백 이후 쌓인 변경은 신규 경로에만 있다(§3.3). 릴리스 노트에 안내.

## 5. 아직 검증되지 않은 항목 (릴리스 전 실측 필요)
실제 DB가 있는 상태의 업그레이드(마커 파일 업그레이드는 확인됨), 웹에서 받은 파일(MOTW)·SmartScreen·Smart App Control 동작, 서명 범위, 백업 파일의 실제 위치, Squirrel 로그 내용, 무인 제거.
