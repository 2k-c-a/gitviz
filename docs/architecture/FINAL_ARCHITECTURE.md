# 최종 아키텍처 (3차 통합 설계)

작성일: 2026-10-30 · 기준 커밋: `65f1b62` · 상태: **확정 설계. 실제 파일·코드는 변경하지 않았다.** 4차(실제 구조 변경)는 사용자의 명시적 승인 후에만 시작한다.

이 문서가 1차(구조 감사)·2차(환경 분리) 문서와 충돌하면 **이 문서가 우선**한다.

| 구분 | 문서 |
|---|---|
| 3차 확정 | [디렉터리 구조](FINAL_DIRECTORY_TREE.md) · [이동·변경 계획](FINAL_MIGRATION_PLAN.md) · [결정 기록](ARCHITECTURE_DECISIONS.md) · [환경 매트릭스](../deployment/FINAL_ENVIRONMENT_MATRIX.md) · [릴리스 이행 계획](../deployment/RELEASE_MIGRATION_PLAN.md) |
| 1차 | [구조 감사](PROJECT_STRUCTURE_AUDIT.md) · [현재 아키텍처](CURRENT_ARCHITECTURE.md) · [의존성 분석](DEPENDENCY_ANALYSIS.md) · [제안 아키텍처](PROPOSED_ARCHITECTURE.md) · [이동 후보](FILE_MIGRATION_CANDIDATES.md) |
| 2차 | [환경 아키텍처](ENVIRONMENT_ARCHITECTURE.md) · [개발 환경 설정](../development/DEVELOPMENT_SETUP.md) · [빌드와 릴리스](../deployment/BUILD_AND_RELEASE.md) · [설치 안내](../deployment/INSTALLATION_GUIDE.md) · [데이터 저장 정책](../deployment/DATA_STORAGE_POLICY.md) |

## 1. 목적과 범위

- **목적:** 현재 구조의 실제 문제만 해결한다. ① `scripts/` 한 폴더에 테스트·운영·검증·빌드·VM 도구 113개가 섞여 있음, ② 빌드 입력(`workspace_agent.spec`, 잠금 파일)이 루트에 흩어져 있고 생성물이 `build/`·`dist/`·`desktop/out`·`.validation`에 분산, ③ 개발·테스트·운영의 경계를 코드가 강제하지 않음, ④ 사용자 데이터가 Roaming에 캐시·키·DB와 섞여 있음.
- **범위 밖(이번 설계가 건드리지 않는 것):** 설치 경로, 서버 배포 방식, DB 엔진 통합, 에이전트 물리 분리, 프런트 프레임워크, Alembic 이력.
- **불변 조건:** 환경변수 이름과 `.env.example`, 18개 Alembic 리비전과 단일 head `b7e1d9c3a502`, 데스크톱 IPC·계약(`contracts/workspace/v1`), Squirrel per-user 설치와 제거 정리 동작.

## 2. 최종 구조 한눈에 보기

상세는 [디렉터리 구조](FINAL_DIRECTORY_TREE.md).

```
app/        서버 + 에이전트 소스(app/workspace)       contracts/   프로세스 간 계약
desktop/    Electron 셸                               frontend/    웹 UI 자원
migrations/ Alembic                                    packaging/agent/  동결 입력(spec, 잠금 의존성)
tests/{server,frontend,workspace}                      tools/{build,validation,ops,vm}
docs/                                                  build-output/  모든 생성물(Git 제외)
```

사용자 PC: 설치 `%LOCALAPPDATA%\GitVizWorkspace`(불변) + 데이터 `%LOCALAPPDATA%\GitViz\Workspace\{data,cache,logs,backups}`(R3).

## 3. 구성요소 경계와 의존 규칙

```
frontend ──복사(build.mjs)──▶ desktop ──stdio JSON(contracts/workspace/v1)──▶ app/workspace(동결 exe)
                                 │                                                  │ 정적 3 + importlib 5
                                 └──HTTPS(remote-contracts 허용 목록)──▶ app(서버) ◀─┘ app/services/ai 공유 7개
                                                                      │
                                                          PostgreSQL / Redis(Celery)
```

| 규칙 | 보호 수단 |
|---|---|
| 에이전트는 서버 전용 모듈(`app.main`, `app.core`, `app.api`, `app.models`, SQLAlchemy, FastAPI 등)을 import하지 않는다 | `tests/workspace/test_import_boundary.py`, spec `excludes` |
| 서비스는 API 계층을 import하지 않는다(역방향 제거) | S8 이후 `git grep` 검사 |
| 데스크톱은 허용 목록에 있는 서버 엔드포인트만 호출한다 | `desktop/src/remote-contracts.ts` (대조 테스트는 후속) |
| `tests/`는 `tools/`를 import하지 않는다(예외: `test_ai_ops` → `tools/ops/ai_ops`) | 코드 리뷰 + `git grep` |
| `tools/`는 제품 런타임이 import하지 않는다 | 동결 spec `excludes` |
| 제품(설치본)은 `.env`·개발 경로를 읽지 않는다 | R1 가드, R2 asar 허용 목록 |

## 4. 구성 체계 요약

[환경 매트릭스](../deployment/FINAL_ENVIRONMENT_MATRIX.md)가 정본이다. 핵심만:

- 환경 판별 `GITVIZ_ENV`(development|test|production). 설치본은 항상 production.
- 서버: *프로세스 환경 > `.env`(개발 한정) > 기본값*. 운영은 `.env`를 읽지 않는다.
- 클라이언트: 빌드 시 `desktop/channel.json`(공개 값만)을 고정. 비밀은 클라이언트에 없다.
- DB: 모든 진입점이 `assert_database_allowed(env, url, intent)` 단일 가드를 통과해야 한다.

## 5. 빌드·배포 요약

[릴리스 이행 계획](../deployment/RELEASE_MIGRATION_PLAN.md) §1이 정본이다. 빌드 순서는 변하지 않는다: 잠금 환경에서 에이전트 동결 → 데스크톱 빌드 → Squirrel `make` → SHA-256 기록 → 클린 VM 검증 → (승인 후) 업로드. 달라지는 것은 경로(`packaging/`, `tools/build/`, `build-output/`)와 R2의 검증 강화(asar 허용 목록, 빌드 환경 잠금 검사)다.

## 6. 변경의 4단 구조

| 단계 | 내용 | 사용자 영향 | 문서 |
|---|---|---|---|
| 4차 S0~S9 | 구조만 변경(이동·경로·소규모 코드 이동) | 없음 (산출물 동일) | [이동 계획](FINAL_MIGRATION_PLAN.md) |
| R1 (0.7.1) | 환경 가드·채널 설정·개발 변수 분리·다운그레이드 방어 | 없음 | [릴리스 이행](../deployment/RELEASE_MIGRATION_PLAN.md) |
| R2 (0.7.2) | 빌드 위생·제외 목록·검증 구성 분리 | 설치 파일 축소 | 〃 |
| R3 (0.8.0) | 사용자 데이터 경로 이전 | 첫 실행 복사 | 〃 |
| R4 (선택) | 무인 제거·삭제 선택·서명 | — | 〃 |
| S10 | 삭제 후보 정리 | 없음 | [이동 계획 §2](FINAL_MIGRATION_PLAN.md) |

## 7. 자체 검증 결과

| # | 질문 | 판정 | 근거·남은 일 |
|---|---|---|---|
| 1 | 기존 기능이 누락되지 않았는가 | 충족 (구조 단계) | 4차는 파일 이동과 import 이동만 한다. 기능 코드 변경은 S7·S8의 함수 이동뿐이며 동일 테스트로 보호 |
| 2 | 모든 핵심 파일에 최종 위치가 있는가 | 충족 | `scripts/` 113개 전부를 파일 단위로 매핑(§1.4, 분류 누락 0). 그 외는 "유지" 또는 §1 표에 명시. 추적 파일 642개 중 유지 대상은 폴더 단위로 기술 |
| 3 | 깨질 import가 파악되었는가 | 부분 충족 | 서버 `app.routers.auth` 5곳, `analytics` 3곳, 지원 모듈 형제 import(평면 이동으로 회피), `__file__` 루트 계산 77곳, CI 40곳, spec `SPECPATH`, `tests/workspace`의 `desktop/out` 하드코딩 2곳. **S4~S6 착수 시 이동 대상별 정적 점검표를 먼저 작성**해야 한다(계획에 포함) |
| 4 | DB 마이그레이션이 보존되는가 | 충족 | `migrations/`, `alembic.ini` 불변. S0·S7·S8·S9에서 `alembic heads` 확인. 서버 DB 엔진 통합은 제외 |
| 5 | 사용자 데이터가 보존되는가 | 충족 (설계) | 4차는 데이터 경로를 건드리지 않는다. R3은 복사→검증→전환→원본 보존. 실측은 R3 VM 시나리오 |
| 6 | 개발·운영 환경이 분리되는가 | 충족 (설계) | V1~V8 방어. 현재는 미구현이므로 R1 전까지는 지금과 같은 위험이 남는다 |
| 7 | 빌드 과정이 유지되는가 | 충족 | 명령 의미 불변. S4·S9에서 B7·B8 비교로 입증 |
| 8 | Setup.exe 생성 과정이 유지되는가 | 충족 | 설치 프로그램 구성 불변, S9에서 클린 VM 검증 |
| 9 | 기존 설치 버전의 업그레이드 경로 | 부분 충족 | 설치 경로 불변. 4차는 산출물 동일. 0.7.0→0.7.1 업그레이드는 마커 파일 수준으로 VM에서 확인됨(2026-10-29). 실제 DB가 있는 상태의 업그레이드는 R1에서 검증 |
| 10 | 각 변경에 검증·롤백이 있는가 | 충족 | 모든 S#/R#에 명령·판정·롤백 기재 |

### 설계 단계에서 수정한 문제 (4차로 넘기지 않음)
`tests/agent` 개명 철회, `tests/server/support` 하위 폴더 철회(import 증가), 통합 검증 6개의 위치를 `tests/server`로 확정(교차 import), `ai_ops.py` 처리 확정, 설정 Roaming 미결정 해소(AD-17), 다운그레이드 방어를 R1로 선행 배치.

### 4차 착수 전에도 남는 불확실성 (숨기지 않고 명시)
1. **CI 실행 결과는 로컬에서 대체 검증**한다. 푸시·CI 실행은 승인이 필요하며, 경로 필터와 `run:` 줄은 로컬 재현으로만 확인된다.
2. 서버 테스트 36줄은 PostgreSQL이 필요하다. 테스트 DB가 준비되지 않으면 B5 기준선을 만들 수 없다(S0 선행 조건).
3. `.env`를 읽지 않았으므로 개발 DB의 실제 대상을 알 수 없다. R1의 가드 허용 목록은 사용자가 제공해야 한다.
4. 서명 범위, MOTW/Smart App Control, 무인 제거, 0.7.x 백업 파일의 실제 위치는 미검증이다.
5. 프런트 복사 목록(16개)의 매니페스트화와 `forge.verify.config.ts` 분리는 동작에 가깝기 때문에 R2로 미뤘다.

## 8. 4차 승인 요청 사항
사용자가 승인해야 하는 것: ① 4차 S0~S9 착수 및 작업 브랜치, ② 커밋 단위(작업당 1커밋), ③ S0에 필요한 테스트 DB 준비, ④ 새 가상환경이 필요한 경우 설치(S4), ⑤ `Claude outputs` 추적 해제(S1), ⑥ R1~R4 및 S10은 각각 별도 승인.
