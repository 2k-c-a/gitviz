# 릴리스 패키지 감사 (0.7.0 · 패키지 최적화)

작성일: 2026-10-30 · 대상: 설치 파일 안의 `app.asar`(제품 코드 묶음) · 변경: `desktop/forge.config.ts`의 릴리스 빌드 제외 규칙만 · 관련: [빌드와 릴리스](BUILD_AND_RELEASE.md) · [알려진 문제](KNOWN_ISSUES.md) K-03

## 1. 결론

릴리스 빌드의 `app.asar`에서 **테스트 코드, 소스맵, 타입 선언(`.d.ts`), 패키지 CI 설정·git 메타데이터·README**를 제외했다. 기능 영향은 발견되지 않았다. 크기 절감은 작다(설치 파일 약 183 KB, `app.asar` 약 1 MB): 용량의 대부분은 Electron 런타임과 동결된 에이전트다. 목적은 용량이 아니라 **제품에 테스트 코드와 개발 경로 문자열이 들어가지 않게 하는 것**이다.

## 2. 현재 빌드 시스템의 패키징 규칙 (조사)

- `desktop/forge.config.ts`의 `packagerConfig.ignore`가 `dist/`, `node_modules/`, `package.json`만 `app.asar`에 넣는다(소스 `src/`, `tests/`, `ui/` 등은 이미 제외).
- 검증용 패키지 변형(`GITVIZ_VERIFY_*=1`)은 패키지 안의 `dist/tests/*.e2e.js`를 실행한다. 그래서 이번 제외 규칙은 **릴리스 빌드(이 환경 변수가 없는 빌드)에만** 적용하고 변형은 그대로 둔다.
- `typescript`는 `dependencies`이고 `desktop/src/impact-worker.ts`가 런타임에 import한다. **제외하지 않는다**(이전 문서의 "제외 후보"는 정정됨). TypeScript 자체의 `lib/*.d.ts`도 유지한다.
- 에이전트(`resources/agent`, 134개 파일)는 PyInstaller 동결본이며 이번 작업 대상이 아니다.

## 3. 제외한 파일 (300개, 912,052 bytes)

| 제외 대상 | 파일 수 | 크기(bytes) | 이유 |
|---|---:|---:|---|
| `dist/tests` (컴파일된 단위·E2E 테스트) | 37 | 341,836 | 제품이 실행하지 않는다. E2E 4개에 개발 경로 문자열(`.validation/`)이 있었다 |
| `dist/forge.config.js` | 1 | 3,502 | 빌드 도구 설정. 런타임에 읽지 않는다 |
| `*.map` (모두 `ajv`가 동봉한 소스맵) | 106 | 280,018 | 디버깅용. 사용자 앱에 필요 없다. 우리 코드는 소스맵을 생성하지 않는다 |
| `*.d.ts` (`ajv`, `fast-deep-equal`, `fast-uri` 등. `typescript/` 제외) | 112 | 79,514 | 타입 선언은 컴파일 시점용이다 |
| `node_modules/*/test`, `spec` (`fast-uri`, `json-schema-traverse`) | 28 | 163,119 | 의존 패키지의 자체 테스트 |
| `node_modules/*/.github` (CI 설정) | 8 | 5,787 | 패키지의 CI 파일 |
| `.gitattributes` | 2 | 357 | git 메타데이터 |
| 패키지 README/CHANGELOG (`LICENSE`는 유지) | 6 | 37,919 | 문서 |
| **합계** | **300** | **912,052** | |

의도적으로 **유지**한 것: 모든 `LICENSE` 파일(라이선스 고지), `typescript`(런타임 사용, 139개 파일), `dist/ui/web`의 `chart-4.4.1/LICENSE.md`, `typescript/SECURITY.md`, 제품 코드·UI·스키마·계약.

## 4. 패키지 파일 구성 (제외 후)

`app.asar`: 569개 파일(항목 676개) — `node_modules` 약 4/5, `dist`(`ui` 112, `src` 29, `schema` 5, `contracts` 1, `preload` 1), `package.json`. 전체 파일 목록은 빌드마다 `tools`가 아니라 실행 시 만든 목록으로 비교했다(작업 증거: `C:\AI_Workspace\temp\claude\gitviz\data\package-{before,after}.json`, 제외 목록 `removed-files.json`). 에이전트 `resources/agent`는 변경 없음.

## 5. 크기 비교

| 항목 | 변경 전 | 변경 후 | 차이 |
|---|---:|---:|---:|
| `app.asar` 파일 | 869개 / 29,633,242 B | 569개 / 28,722,328 B | −300개 / −910,914 B |
| `app.asar` 파일(아카이브) | 29,743,038 B | 28,756,287 B | −986,751 B |
| 패키지 폴더 전체 | 446,801,863 B | 445,815,121 B | −986,742 B |
| `GitVizWorkspaceSetup.exe` | 180,332,032 B | 180,141,568 B | −190,464 B |
| `GitVizWorkspace-0.7.0-full.nupkg` | 179,651,800 B | 179,461,745 B | −190,055 B |

## 6. 검증 결과

| 항목 | 판정 | 근거 |
|---|---|---|
| 제외 후 `app.asar`에 테스트 코드 없음 | PASS | 파일 목록에서 `tests/test/spec/__tests__`, `*.test.js`, `*.e2e.js`, `*.spec.js` 일치 0개 |
| 소스맵 없음 | PASS | `*.map` 0개 |
| 타입 선언(typescript 제외) 없음 | PASS | `*.d.ts` 0개 |
| `.git*` / `.github` 없음 | PASS | 0개 |
| 이번 변경 이후 추가된 파일 없음 | PASS | 변경 전 목록 대비 추가 0개 |
| 런타임 모듈 로드 (`typescript`: `createSourceFile`+`createProgram(noLib)`, `ajv`: `compile`) | PASS | 패키지된 exe를 node 모드로 실행해 asar 안에서 로드 |
| UI 자산 매니페스트 92개 모두 존재 | PASS | `assets.json` 항목마다 파일 확인, 누락 0 |
| 핵심 파일(main, preload, UI, 스키마) 존재 | PASS | |
| 앱 시작, 프런트 로드, 렌더러 격리 | PASS | 패키지된 exe, 격리된 사용자 데이터 폴더(`--user-data-dir`), 디버깅 포트로 확인 |
| 에이전트 ↔ DB 헬스 `ready`, 프로젝트 목록 IPC(`listProjects`) | PASS | `{"items":[],"nextCursor":null}` |
| 페이지 리소스 로드(스타일 1, 스크립트 13, 깨진 이미지 0) | PASS | |
| Windows 전체 게이트 23단계(검증용 변형 빌드 포함, E2E 12개) | PASS | 최종 제외 규칙으로 23단계 모두 통과, 실패 0, 저장소 상태 변화 없음(증거 `C:\AI_Workspace\temp\claude\gitviz\logs\gate-after-package-opt3-*`). 같은 규칙의 앞선 실행은 통과했으나 실행 중 문서를 수정해 "상태 변경"으로 판정이 실패해서 저장소를 건드리지 않고 다시 실행했다 |
| 클린 VM 설치·업그레이드·제거 재시험 | NOT TESTED | 이번 단계는 호스트 스모크와 게이트만 수행. 이 패키지의 VM 설치 시험은 별도 필요 |
| 폴더 등록 UI 등 수동 기능 | NOT TESTED | 5차와 동일한 한계 |
| 미서명 | 정책에 따라 수용 | K-01 |

## 7. 주의와 한계

- 후보 파일: `build-output/release-candidates/0.7.0-pkgopt/` (Git 제외, 업로드하지 않음). SHA-256 — 설치 파일 `a3d304022f82ff430e0d15bca1c2999dd5af14f17b5ba2e4dfae86525311e0e5`, nupkg `18a8b59e7ff5121b5b9b1e0cbaaeb9924b7bbc349bd7d42cef3a371e1bd438c4`. **5차에서 VM 시험을 마친 `release-candidates/0.7.0/`와는 다른 파일**이다. 이 후보를 배포하려면 VM 설치 시험을 다시 해야 한다.
- 빌드는 바이트 단위로 재현되지 않는다(같은 코드도 해시가 다름).
- 호스트 스모크 시험 중 첫 시도에서 `APPDATA` 환경 변수만 바꿔 격리했으나 Electron이 따르지 않아 **호스트의 실제 사용자 데이터 폴더를 열었다**. 데이터 파일 5개의 해시는 백업과 동일했고 내용 변화는 없었지만 SQLite가 빈 `workspace.sqlite3-wal`·`-shm`을 남겼다. 이후 시험은 `--user-data-dir`로 격리했다. 교훈: 패키지 스모크 시험은 처음부터 `--user-data-dir`을 쓴다.
- 이번에 바꾸지 않은 것: 디버그 포트, 무인 제거 로직, 사용자 데이터 위치, `storage/`·`venv/`·`.validation/`·`desktop/out/`·`certs/`·환경 변수 백업 파일.
