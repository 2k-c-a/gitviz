# 제안 아키텍처 (1차 · 제안만, 적용 없음)

작성일: 2026-10-30 · 기준 커밋: `65f1b62` · [문서 안내](../index.md)

다른 문서: [구조 감사](PROJECT_STRUCTURE_AUDIT.md) · [현재 아키텍처](CURRENT_ARCHITECTURE.md) · [의존성 분석](DEPENDENCY_ANALYSIS.md) · [이동 후보](FILE_MIGRATION_CANDIDATES.md)

이 문서는 **제안**이다. 아무 파일도 옮기지 않았고, 승인된 계획이 아니다. 2차(환경 분리) 분석과 사용자 결정을 거쳐야 실행할 수 있다. 표기: **확인**은 코드에서 확인한 사실, **추정**은 판단이다.

## 1. 설계 방향

현재 구조의 실제 문제([구조 감사](PROJECT_STRUCTURE_AUDIT.md) §4)에 맞는 변경만 제안한다. 원칙별로 무엇에 적용하는지, 적용하지 않는 곳은 어디인지 함께 적는다.

| 원칙 | 적용 대상 | 적용하지 않는 곳과 이유 |
|---|---|---|
| 관심사 분리 | 서버 / 에이전트 / 데스크톱 셸 / 웹 UI / 도구를 폴더 수준에서 구분 | `app` 안의 `api`·`services`·`models`는 이미 계층별로 나뉘어 있어 재분할하지 않는다 |
| 단일 책임 | `scripts/`(테스트·운영·검증·빌드 혼합), `app/api/analytics.py`(API + 계산) | 작은 모듈(`app/utils` 1개 파일 등)을 억지로 합치거나 쪼개지 않는다 |
| 모듈성 | 에이전트가 쓰는 서버 AI 공용 코드 7개를 이름 있는 공유 영역으로 | 서버 내부 AI 서비스 24개 파일은 그대로 둔다 |
| 의존 역전 | `core → services`, `services → api`, `api → routers(비공개 함수)` 역방향 3종 해소 | 서비스에 인터페이스 계층을 새로 만들지 않는다(현재 규모에 과함) |
| 유지보수성 | 설정·DB 엔진의 이중화 정리, 빌드 경로의 단일 출처 | 동작하는 마이그레이션 18개는 건드리지 않는다 |
| 확장성 | 새 워크스페이스 화면 추가 시 `build.mjs` 수정 불필요하게 | 서버 수평 확장 구조는 이번 범위가 아니다 |
| 테스트 용이성 | 서버 테스트를 `tests/`로 일원화, 러너 도입 | 기존 스크립트형 테스트를 한 번에 재작성하지 않는다 |
| 배포 격리 | 에이전트 빌드 입력을 서버 코드와 물리적으로 분리 | 서버는 컨테이너화·배포 방식을 이번에 바꾸지 않는다 |

만들지 않는 것: 공통 `utils`/`common` 폴더, 서비스 인터페이스 계층, 마이크로서비스 분할, 프런트 프레임워크 도입.

## 2. 권장 최종 디렉터리 구조

```
gitviz/
├─ app/                      서버 패키지 이름 유지 (웹 API + Celery)
│  ├─ api/                    HTTP 라우터. routers/auth.py를 api/auth.py로 흡수
│  ├─ services/  collectors/  tasks/  models/  schemas/
│  ├─ core/                   설정(settings), DB(sync/async를 db/ 로 정리), celery
│  └─ main.py
├─ agent/                    ★ 에이전트 (현 app/workspace). Phase C에서 이동
│  ├─ gitviz_agent/           PyInstaller 대상 패키지
│  └─ pyproject 또는 requirements (현 requirements-workspace*)
├─ shared/ai_core/           ★ 서버·에이전트 공용 순수 파이썬 7개 모듈 + prompts (Phase C)
├─ desktop/                  Electron 앱 (구조 유지)
├─ frontend/                 웹 UI (구조 유지, 데스크톱 복사 목록은 매니페스트로)
├─ contracts/                프로세스 간 계약 (유지)
├─ migrations/  alembic.ini  유지
├─ tests/
│  ├─ server/                 ← scripts/test_*.py 이전 (러너 도입과 함께)
│  ├─ agent/                  ← tests/workspace
│  └─ frontend/               ← scripts/test_*.cjs
├─ tools/                    ← scripts/ 에서 성격별로 분리
│  ├─ build/                  build_workspace_agent.py, audit_workspace_agent.py
│  ├─ ops/                    backfill_*, cleanup_*, 점검 도구 (수동 운영)
│  ├─ validation/             validate_*.ps1, verify_*.py, smoke_*
│  └─ vm/                     p01_*, staging_*, install_p01_vm 등 Hyper-V 도구
├─ docs/                     문서 (architecture/ 에 이 문서군)
├─ build/  dist/             생성물 (Git 제외, 위치 유지)
├─ docker-compose.yml  requirements*.txt  .env.example  README.md
└─ .github/
```

별도 폴더를 **만들지 않는 것:** `infra/`(compose 파일 1개), `config/`(루트 설정 파일이 도구의 기본 탐색 위치), `libs/`.

## 3. 영역별 제안과 이유

### 3.1 에이전트 분리와 공용 AI 코어 (Phase C, 선택)

(확인) 현재 `app/workspace`(5,225줄)는 서버와 같은 `app` 패키지에 있고, 경계는 `test_import_boundary.py`와 PyInstaller `excludes`로만 보장된다. 서버 AI 모듈 7개를 정적 3 + 동적 5 방식으로 공유한다(`importlib`, 그래서 `hiddenimports` 수동 등록).

제안: 공유 7개 모듈과 프롬프트를 서버·에이전트가 모두 의존하는 `shared/ai_core`로 옮기고, 에이전트는 `agent/`로 분리한다.

- **얻는 것:** 에이전트 빌드 입력이 `agent/`+`shared/`로 닫혀 `excludes`·`hiddenimports` 목록이 사라지거나 단순해진다. 서버 코드 변경이 에이전트 번들에 영향을 주지 않음을 파일 구조로 알 수 있다.
- **잃는 것/위험:** 서버가 `from app.services.ai.…`로 쓰는 import 전부와 spec·CI 경로 필터·pyright/ruff 대상·문서 링크를 바꿔야 한다. 변경량이 크다.
- **추정:** 효용 대비 위험이 가장 큰 항목이다. 2차 분석(환경 분리) 결과에 따라 **보류**를 기본으로 한다. 이 항목 없이도 Phase A·B만으로 구조 문제 대부분이 해소된다.

### 3.2 `scripts/` → `tools/` 와 `tests/` 일원화 (Phase A–B)

(확인) 루트 107개 중 CI가 경로로 호출하는 파일이 39개, 서버 테스트 35개가 `scripts/`에 있다.

- Phase A(저위험): 참조 없는 일회성 스크립트와 `Claude outputs` 같은 비코드 파일을 정리 대상으로 분류만 하고, 이동은 승인 후([이동 후보](FILE_MIGRATION_CANDIDATES.md)).
- Phase B(중위험): 하위 폴더(`tools/…`)로 이동하되 **CI 워크플로, `build_workspace_agent.py`의 `parents[1]`, 검증 스크립트의 상대 경로를 같은 커밋에서 고친다.** 테스트는 `tests/server/`로 옮기며 `pytest`(또는 `unittest discover`)를 도입한다. 도입 시 CI 36줄이 1~2줄로 줄어든다.

### 3.3 서버 내부 정리 (Phase B–D)

| 항목 | 제안 | 위험 |
|---|---|---|
| 라우터 이중화 | `app/routers/auth.py` → `app/api/auth.py`; `_get_session_user_id` 공개 이름으로 | 낮음~중간 (import 변경 4곳 + 문서) |
| 계산 로직 위치 | `app/api/analytics.py`의 `_coupled_files_for`, `COUPLED_SORT_KEYS`를 `services`로 이동해 `services → api` 역방향 제거 | 중간 (스냅샷 계산 결과가 달라지면 안 됨 — 해시 검증 테스트로 보호) |
| 설정 | `os.getenv` 48곳 + `load_dotenv` 5곳을 `app/core/settings`로 모으기. AI 설정은 이미 중앙화 | 중간 (환경변수 이름·기본값 불변 유지) |
| DB 엔진 | 동기/비동기 엔진을 `app/core/db/`(`sync.py`, `async_.py`)로 정리하고 `Base`를 하나의 `metadata`로 통합 가능한지 평가 | **높음** (Alembic autogenerate와 `users` 테이블 처리, 프로덕션 스키마) |
| 로깅 | 서버 전역 로깅 구성 도입, `print` 78곳 정리 | 낮음 |
| 순환 5개 | #1(ai_settings↔registry)은 설정 검증을 제공자 쪽으로 이동, #3은 인증 모듈 분리로 해소. #2·#5는 작은 범위라 보류 | 낮음~중간 |

### 3.4 프런트엔드·데스크톱 결합 (Phase B)

(확인) `desktop/scripts/build.mjs`가 `frontend` 파일 16개를 이름으로 나열해 복사한다.

- 제안: 복사 대상 목록을 `frontend/js/workspace/` 전체 + 명시적 예외로 바꾸거나, 목록을 데이터 파일 하나(매니페스트)로 분리한다. 새 화면 추가 시 빌드 스크립트 수정이 필요 없어진다. **폴더 이동은 하지 않는다.**
- `desktop/ui/status.js`와 `frontend/js/workspace/status.js`(데스크톱에서 `git-status.js`로 복사)의 이름 충돌을 파일 이름으로 구분한다.
- 서버 엔드포인트 허용 목록(`desktop/src/remote-contracts.ts`)과 서버 라우트의 일치를 CI에서 대조하는 테스트를 추가한다(현재는 수동 유지).

### 3.5 빌드·배포 (Phase B)

- `forge.config.ts`의 검증용 패키지 변형 8개를 별도 설정 파일(예: `forge.verify.config.ts`)로 분리해, 실제 설치 프로그램 설정에서 `GITVIZ_VERIFY_*` 분기와 `manifest.main` 교체 코드가 보이지 않게 한다. 환경변수 실수로 제품 진입점이 바뀌는 경로를 없앤다.
- 에이전트 빌드 산출 위치(`dist/workspace-agent/agent`)를 `forge.config.ts`와 `build_workspace_agent.py`가 각자 문자열로 갖는 대신 한 곳(환경변수 또는 설정)으로 모은다.
- `desktop/out` 아래 이전 빌드 폴더(`make-0.7.0-*`)는 저장소 밖 보관 위치를 정한다(§5).

### 3.6 데이터 경계

- 서버 런타임 데이터 `storage/repos/`는 작업 디렉터리 상대 경로다. 설정값(`GITVIZ_STORAGE_DIR` 같은 이름은 제안일 뿐)으로 빼면 배포 위치를 바꿀 수 있다. Phase D 이후 항목.
- 서버 DB 백업·복구 절차는 저장소에 없다. 구조 변경과 별개로 운영 문서가 필요하다(2차 분석 항목).

## 4. 단계별 도달 경로

| 단계 | 내용 | 파일 이동 | 위험 | 선행 조건 |
|---|---|---|---|---|
| **A. 위생** | 정리 후보 분류, `docs/index.md`에 이 문서군 연결, `.gitignore` 낡은 항목 정리 | 없음 또는 소수 | 낮음 | 사용자 승인 |
| **B. 경계 명확화** | `scripts/` 하위 폴더화, 테스트 일원화, `forge` 설정 분리, `build.mjs` 매니페스트화, 라우터 통합 | 중간 | 중간 | CI를 고칠 수 있는 환경, 같은 커밋에서 경로 수정 |
| **C. 배포 단위 분리** | `agent/` + `shared/ai_core` 분리 | 대량 | 높음 | 2차 환경 분리 분석, Windows 빌드·VM 검증 재실행 |
| **D. 서버 내부** | 설정 통합, DB 엔진 정리, 계산 로직 이동, 로깅 | 소수(코드 수정 위주) | 중간~높음 | 서버 회귀 테스트(PostgreSQL), 스키마 변경 없음 확인 |

각 단계 종료 조건: 기존 CI 3개 워크플로 통과 + 로컬에서 설치 프로그램 생성 후 클린 VM 설치·제거 재검증(Phase B 이후). 단계는 서로 독립이므로 A·B만 하고 멈춰도 가치가 있다.

## 5. 저장소 밖으로 내보낼 후보 (구조와 별개)

| 대상 | 크기 | 제안 |
|---|---|---|
| `.validation/` | 58 GB | VM 디스크·ISO(`p01-vm`, `p08-vm`, `iso`)는 별도 드라이브/아카이브. 증거 JSON·로그만 남기는 규칙 필요. 일부 문서가 인용하는지 먼저 조사 |
| `desktop/out/` 이전 빌드 | 약 4 GB | 최신 빌드 외 보관 위치 결정 |
| `outputs/*.pptx`, `Claude outputs/` | 15 MB / 164 KB | 저장소 밖 또는 별도 브랜치. 추적 중인 보고서 1개의 의도 확인 |

## 6. 의사결정이 필요한 항목

1. 에이전트 물리 분리(Phase C)를 할 것인가, 보류하고 현 경계를 유지할 것인가.
2. 서버 테스트를 계속 스크립트형으로 둘 것인가, 러너를 도입할 것인가(도입 시 CI와 문서가 함께 바뀐다).
3. 동기/비동기 DB 이중화를 유지할 것인가(성능·pgbouncer 제약 때문에 의도된 것인지 확인 필요).
4. `Claude outputs/`와 `outputs/`의 위치.
5. `.validation/` 보관 정책.
