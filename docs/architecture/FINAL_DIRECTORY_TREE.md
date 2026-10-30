# 최종 디렉터리 구조 (3차 확정 · 적용 전)

작성일: 2026-10-30 · 기준 커밋: `65f1b62` · 상태: **확정 설계. 아직 적용되지 않았다.** 4차 작업은 사용자의 명시적 승인 후에만 시작한다.

함께 읽기: [최종 아키텍처](FINAL_ARCHITECTURE.md) · [이동 계획](FINAL_MIGRATION_PLAN.md) · [결정 기록](ARCHITECTURE_DECISIONS.md) · [환경 매트릭스](../deployment/FINAL_ENVIRONMENT_MATRIX.md) · [릴리스 이행 계획](../deployment/RELEASE_MIGRATION_PLAN.md)

이 문서는 **하나의 구조만** 제시한다. 여기에 없는 경로를 4차 작업자가 새로 만들지 않는다. 표기: `(유지)`는 이동·이름 변경 없음, `(이동)`/`(신규)`는 4차 변경 대상, `(생성물)`은 Git 제외.

## 1. 저장소 트리

```
gitviz/
├─ app/                          (유지) 서버 패키지 + 워크스페이스 에이전트 소스
│  ├─ api/                        (유지) HTTP 라우터. auth.py가 여기로 들어온다 (S7)
│  │  ├─ ai/  desktop/
│  │  └─ auth.py                  (이동) 현 app/routers/auth.py
│  ├─ collectors/  models/  schemas/  tasks/        (유지)
│  ├─ core/                       (유지) 설정·동기 DB·celery. DB 엔진 통합은 이번 범위 아님
│  ├─ services/                   (유지) + analytics 계산 모듈 1개 (S8)
│  ├─ workspace/                  (유지) 에이전트 소스. 물리 분리하지 않는다 (AD-02)
│  ├─ database.py  main.py        (유지)
│  └─ routers/                    (삭제) auth.py 이동 후 빈 패키지 제거 (S7)
├─ contracts/                    (유지) 프로세스 간 계약 JSON Schema. 공유 계약의 유일한 위치 (AD-03)
├─ desktop/                      (유지) Electron 셸 (main/preload/ui/tests/scripts)
│  ├─ src/  ui/  tests/  scripts/
│  ├─ forge.config.ts             (유지) outDir·extraResource 경로만 변경 (S9)
│  └─ channel.json                (신규, R1) 빌드 시 고정되는 공개 채널 설정
├─ frontend/                     (유지) 웹 UI 정적 자원 + 데스크톱 복사 원본
├─ migrations/                   (유지) Alembic 리비전 18개, schema_20261002.py
├─ alembic.ini                   (유지)
├─ packaging/
│  └─ agent/                      (신규, S4) 에이전트 동결 입력
│     ├─ workspace_agent.spec     (이동) 루트에서
│     ├─ requirements-workspace.txt        (이동)
│     ├─ requirements-workspace-build.txt  (이동)
│     └─ requirements-workspace.lock       (이동)
├─ tests/
│  ├─ server/                    (신규, S6) 서버·통합 테스트와 지원 모듈 (평면)
│  │  └─ fixtures/ai/
│  ├─ frontend/                  (신규, S6) Node 기반 프런트 테스트 4개
│  └─ workspace/                 (유지) 에이전트 테스트 (이름 유지, AD-06)
├─ tools/
│  ├─ build/                      (신규, S4) build_workspace_agent.py, audit_workspace_agent.py
│  ├─ validation/                 (신규, S5) Windows 게이트·단계별 패키지 검증·AI 목업
│  ├─ ops/                        (신규, S2) 수동 운영 도구 (ai_ops.py 포함)
│  │  └─ legacy/                   일회성 마이그레이션·검증 스크립트 (삭제 아님)
│  └─ vm/                         (신규, S3) Hyper-V·스테이징 도구
├─ docs/                         (유지) 구조·감사 규칙 유지, index.md에 새 문서 연결 (S1)
├─ .github/workflows/            (유지) 경로만 갱신
├─ docker-compose.yml            (유지) 개발용 DB·Redis (+ ai-ops 명령 경로 갱신)
├─ requirements.txt              (유지) 서버 의존성
├─ ruff-workspace.toml  pyright-workspace.json     (유지) 도구 기본 탐색 위치
├─ .env.example                  (유지) 환경변수 이름 불변
├─ .gitignore  .gitattributes  README.md           (유지, .gitignore 갱신)
│
├─ .env                          (로컬·Git 제외) 개발자 전용
├─ .venv/                        (로컬·Git 제외) 서버 개발 가상환경
├─ storage/repos/                (런타임·Git 제외) 서버가 clone한 저장소
├─ build-output/                 (생성물·Git 제외, 신규 S9)
│  ├─ agent/                      PyInstaller work + dist (`dist/agent`)
│  ├─ desktop/                    electron-forge out (패키지·make·검증 변형)
│  ├─ venv-agent/                 잠금 파일로만 만든 빌드 가상환경
│  ├─ dev-data/                   개발 실행의 사용자 데이터·로그 (R1)
│  └─ archive/                    이전 빌드 보관 (`make-0.7.0-*`)
└─ .validation/                  (로컬·Git 제외) VM·증거. 저장소 밖 이전은 별도 승인
```

### 만들지 않는 폴더 (결정 AD-01·02·21)

| 사용자 참조 구조의 항목 | 결정 | 이유 |
|---|---|---|
| `backend/` | 만들지 않음 | `app/`을 옮기면 `from app…`/`import app…` 구문 835줄(파일 201개)·Alembic·CI·Celery 문자열이 깨지고 얻는 것이 이름뿐이다 |
| `shared/` | 만들지 않음 | 공유 영역이 실제로 존재하지 않는다(공유 7개 모듈은 서버 안에 있고 에이전트가 가져다 쓴다). 공유 계약은 `contracts/`가 담당한다 |
| `infrastructure/` | 만들지 않음 | 파일이 `docker-compose.yml` 하나다. 서버 배포 정의는 이번 범위 밖 |
| `scripts/` | 폐지 | 성격이 다른 113개 파일을 `tests/`와 `tools/`로 분배한다 |
| `build/`, `dist/` | 폐지 | `build-output/`으로 통합한다 |
| `config/` | 만들지 않음 | `ruff`/`pyright` 설정은 도구가 루트에서 찾는다 |

## 2. 영역별 책임

| 영역 | 위치 | 책임 | 하지 않는 일 |
|---|---|---|---|
| Backend | `app/`(`workspace` 제외), `migrations/` | 웹 API, 수집·분석, Celery, DB 스키마 | 데스크톱 UI, 사용자 PC 데이터 |
| Agent | `app/workspace/` | 사용자 PC에서 실행되는 동결 exe의 소스, SQLite, DPAPI, 적용 저널 | 서버 DB·네트워크 모듈 import (경계 테스트가 보호) |
| Desktop | `desktop/` | Electron 셸, IPC, 보안 설정, 설치 프로그램 정의 | 비즈니스 계산 |
| Frontend | `frontend/` | 웹 UI 자원. 일부가 데스크톱 `assets`로 복사됨 | 서버·에이전트 로직 |
| Shared contracts | `contracts/` | 프로세스 간 JSON Schema (`workspace/v1`) | 코드 |
| Packaging | `packaging/agent/` | 에이전트 PyInstaller spec, 잠금된 빌드 의존성 | 앱 코드 |
| Tests | `tests/{server,frontend,workspace}` + `desktop/tests` | 환경별 자동 검증 | 운영 도구 |
| Tools | `tools/{build,validation,ops,vm}` | 빌드·검증·운영·VM 도구 | 제품 코드 |
| Docs | `docs/` | 설계·운영·검증 문서, 감사 기준선 | — |
| Build outputs | `build-output/` | 모든 생성물 | 소스 |
| User runtime data | 사용자 PC (§3) | 사용자 DB·키·설정·로그 | 설치 폴더 |

## 3. 사용자 PC 최종 구조 (R3 이후 · 설치 경로는 변경하지 않는다)

```
%LOCALAPPDATA%\GitVizWorkspace\          설치 루트 (Squirrel). 제거 시 전체 삭제됨
├─ Update.exe
├─ packages\
└─ app-<버전>\ GitVizWorkspace.exe, resources\{app.asar, agent\gitviz-workspace-agent.exe}

%LOCALAPPDATA%\GitViz\Workspace\         사용자 데이터 루트. 제거해도 유지됨
├─ data\      workspace.sqlite3(+wal/shm), key.dpapi, secret-check.bin, writer.lock, settings.json, window.json, Local Storage
├─ cache\     Chromium sessionData·GPU 캐시 (삭제해도 되는 것)
├─ logs\      app.log, agent.log, crash\ (회전)
└─ backups\   pre-v<N>-{uuid}.sqlite3 (최근 5개)

%TEMP%\GitViz\                            실행 중 임시 파일 (종료 시 정리)

%APPDATA%\gitviz-workspace\               [현재 위치 = 0.7.x] R3 이후 읽기 전용 보존, 자동 삭제 없음
```

설치 경로(`%LOCALAPPDATA%\GitVizWorkspace`)는 바꾸지 않고, 데이터 경로 변경은 R3에서 별도 릴리스로 수행한다 ([릴리스 이행 계획](../deployment/RELEASE_MIGRATION_PLAN.md)).

## 4. 개발자 PC 최종 구조 (개발·테스트 실행의 데이터 위치)

| 환경 | 서버 DB | 클라이언트 데이터 | 로그 |
|---|---|---|---|
| Development | `docker-compose`의 개발 PostgreSQL (`.env`) | `build-output/dev-data/` (`GITVIZ_USER_DATA_DIR`) | `build-output/dev-data/logs/` + 콘솔 |
| Test | `gitviz_test*` DB (`GITVIZ_TEST_DATABASE_URL`) | `os.tmpdir()/gitviz-test-<pid>` (종료 시 삭제) | 테스트 출력 |
| Build | 사용 안 함 | 사용 안 함 | `build-output/*/` 내부 |
