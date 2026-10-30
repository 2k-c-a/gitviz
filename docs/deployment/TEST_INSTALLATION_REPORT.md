# 설치 검증 보고 (5차)

검증일: 2026-10-30 · 브랜치 `refactor/p11-structure` · 코드 기준 커밋 `ae8f6c7` + 문서 변경 · 판정: PASS / FAIL / NOT TESTED (실제 실행 결과만 기록)

## 1. 빌드 산출물

| 항목 | 값 |
|---|---|
| 제품 버전 | 0.7.0 (`desktop/package.json`) |
| 설치 파일 | `GitVizWorkspaceSetup.exe` — 180,332,032 bytes |
| SHA-256 | `1ed1ba6ff000fe86a7bbbad9d309312af5648209edf357e2cfc5aacf834852c0` |
| 전체 패키지 | `GitVizWorkspace-0.7.0-full.nupkg` — 179,651,672 bytes, SHA-256 `f2be47d8978af4fd7443466a91af0f7131233e03755fb2abfd099807a06462a4` |
| RELEASES | 86 bytes, SHA-256 `4c0ca258c2ae46311947566a9d2b8fcd9b3f795d773b1fd5bfd89a646aeb16e7` |
| 서명 | **미서명** (`Get-AuthenticodeSignature` = NotSigned) |
| 위치 | `build-output/release-candidates/0.7.0/` (`SHA256SUMS.txt` 포함) |
| 업그레이드용 시험 설치 파일 | 같은 코드에서 버전만 0.7.1로 올려 빌드(저장소 버전은 되돌림, **배포 대상 아님**). `build-output/release-candidates/0.7.1-test/`, 설치 파일 SHA-256 `D358EF5F2967CECB74EF75D96CE0F00D13C74029E12ADD857933FC0EC09680FE` |

## 2. 테스트 환경

| 항목 | 값 |
|---|---|
| VM | Hyper-V `GitViz-P01-Clean-Win11`, 체크포인트 `pre-p10-installer`에서 복원 후 실행, 종료 후 다시 복원 |
| OS | Windows 11 Home 10.0.26300 (빌드 26300), 64비트, ko-KR |
| Python / Node / Git | 없음 (게스트에서 `node.exe`·`python.exe`·`git.exe` 검색 결과 없음) |
| 계정 | 모두 일반 사용자(Users 그룹, 관리자 아님): `P01Tester`, `테스트 사용자`(한글+공백), `qa&(x)`(특수문자) |
| UAC | 켜짐(EnableLUA=1) |
| Smart App Control | 레지스트리 상태값 2 (평가 모드를 뜻하는 값으로 알려져 있으나 이 해석은 확인하지 않았다. 설치 파일이 차단되지는 않았음) |
| 실행 스크립트 | `tools/vm/test_release_vm.ps1` (1회차 결과 `.validation/p11-vm/Release-result-run1.json`, 재실행 결과 `Release-result.json`) |

1회차는 앱 시작 직후 한 번만 헬스를 읽어 `unavailable`이 나왔고 테스트 스크립트 결함(준비 대기 없음)으로 판단해 폴링을 넣어 재실행했다. 아래는 재실행 결과이며 1회차 기록도 보존돼 있다.

## 3. 결과 (3개 계정 공통, 별도 표기 없으면 동일)

| 영역 | 항목 | 판정 | 근거 |
|---|---|---|---|
| 사전 | 일반 사용자, 개발 도구 없음, 미설치 | PASS | precheck |
| 설치 | `--silent` 설치 종료 코드 0 (36~43 s) | PASS | install-exit |
| 설치 | 관리자 권한 불필요, `%LOCALAPPDATA%\GitVizWorkspace`에 사용자별 설치 | PASS | per-user-no-admin |
| 설치 | exe·Update.exe·agent exe·app.asar 존재 | PASS | files-installed |
| 설치 | 바탕화면·시작 메뉴 바로가기 생성 | PASS | shortcuts |
| 설치 | 제어판 제거 항목(HKCU) 생성 | PASS | uninstall-entry |
| 실행 | 첫 실행 후 프로세스 유지, 에이전트 프로세스 구동 | PASS | first-launch-alive, agent-process-running |
| 실행 | 프런트엔드 로드(`gitviz://app/index.html`, 제목 "프로젝트 · GitViz Workspace") | PASS | frontend-loaded |
| 실행 | 렌더러 격리(`require`/`process` 없음, `window.workspace` 있음) | PASS | renderer-isolated |
| 실행 | 앱↔에이전트↔DB 헬스 `ready` (store/secrets/lifecycle) | PASS | ipc-agent-db-health |
| 데이터 | 사용자 데이터가 설치 폴더 밖 `%APPDATA%\gitviz-workspace\workspace`에 생성(sqlite·key.dpapi·secret-check.bin·writer.lock) | PASS | user-data-created |
| 데이터 | 설치 폴더·Program Files에 사용자 데이터 없음 (Program Files에는 아무것도 설치하지 않음) | PASS | no-user-data-in-install-dir-* |
| 데이터 | 재시작 후 키 파일 해시 동일 | PASS | key-stable-across-restart |
| 경로 | 한글+공백 계정, 특수문자(`&`, `(`, `)`) 계정에서 설치·실행·업그레이드·제거 | PASS | 3계정 동일 |
| 업데이트 | 0.7.0 → 0.7.1 설치, `app-0.7.1`만 남음, 바로가기 유지 | PASS | upgrade-* |
| 업데이트 | 업그레이드 직후(실행 전) DB·키·마커 파일 해시 동일 | PASS | data-intact-after-upgrade-before-launch |
| 업데이트 | 0.7.1 실행 시 기존 데이터로 헬스 `ready`, 프런트 로드 | PASS | upgraded-app-runs-with-old-data |
| 업데이트 | 실행 후 키·secret-check 해시 불변 | PASS | key-unchanged-after-upgraded-run |
| 제거 | 제거 명령 종료 코드 0 | PASS | uninstall-exit |
| 제거 | 바로가기·제어판 항목 제거 | PASS | uninstall-shortcuts/entry-removed |
| 제거 | 사용자 데이터·설정 기본 보존(마커 파일·DB 해시 동일) | PASS | user-data-kept-after-uninstall |
| 제거 | 앱 파일 완전 삭제 | **FAIL** | 무인(`--uninstall -s`) 제거 후 `.dead`, `Update.exe`, `app-0.7.1\squirrel.exe`, `app-0.7.1\v8_context_snapshot.bin` 4개가 90초 대기 후에도 남음(3계정 모두 재현). 대화형 세션 제거에서는 통과한 이전 증거 `.validation/p10-vm/installer-0.7.0-cleanup-interactive-Run-result.json` 있음 |

### 3.1 장애·복구 (첫 계정 `P01Tester`만, 데이터 파일 해시 기준)

| 주입한 장애 | 판정 | 관찰 |
|---|---|---|
| 키 파일(`key.dpapi`) 없음 | PASS (데이터 보존) | DB 해시 불변, 헬스 `unavailable`로 표시(복구 필요 상태), 키 파일 되돌린 뒤 정상. 앱 프로세스는 유지. **화면에 어떤 안내가 보이는지는 확인하지 않았다 → UI 안내는 NOT TESTED** |
| 에이전트 exe 없음 | PASS (데이터 보존) | DB 해시 불변, 헬스 `unavailable`, 앱 프로세스 유지 |
| 데이터 폴더 쓰기 거부(ACL deny) | PASS (데이터 보존) | DB 해시 불변. 헬스는 `ready` (거부 ACL이 이미 열린 핸들·읽기에는 영향이 적었던 것으로 보이며, "쓰기 실패 시 오류 표시"는 검증되지 않음) |
| `window.json` 없음 | PASS | 헬스 `ready`. 해당 파일은 시험 시점에 애초에 없었고 재생성도 확인되지 않음 |
| 실행 중 강제 종료 | PASS | 고아 에이전트 0개, 재실행 후 헬스 `ready` |
| 로그 쓰기 실패 | NOT TESTED | 앱에 로그 파일이 없음(KNOWN_ISSUES K-02) |
| DB 파일 손상 / 마이그레이션 실패 | NOT TESTED | 이전 단계(P06/P07)의 에이전트 단위 시험에서 다뤘으나 5차 VM에서는 실행하지 않음 |
| 업데이트 중 중단 | NOT TESTED | |

## 4. 실행하지 못했거나 자동화로 증명할 수 없는 항목

| 항목 | 판정 | 사유 |
|---|---|---|
| 폴더 선택 대화상자로 프로젝트 등록·저장·재조회 | NOT TESTED | 네이티브 대화상자는 자동화 불가. 등록 후 재실행 시 DB 행 유지는 미확인 |
| 계정 간 데이터 분리(다른 사용자가 남의 `%APPDATA%` 접근 불가) | NOT TESTED | 프로필 기본 ACL에 의존. 시험 스크립트의 교차 접근 점검과 관리자 복사가 모두 결과로 확정되지 않았다 |
| DB 스키마 버전·무결성의 호스트 측 직접 점검 | NOT TESTED | VM에서 호스트로 데이터를 복사하지 못했다(접근 거부). 대신 게스트 안 해시 비교와 앱 헬스 `ready`만 확인 |
| 실제 데이터가 있는 DB로 업그레이드, 스키마 변경 업그레이드 | NOT TESTED | 시험 DB는 새로 만든 빈 DB. 스키마 변경 빌드가 아직 없음 |
| SmartScreen / 파일 다운로드 표시(MOTW) 경고 | NOT TESTED | 인터넷에서 내려받은 상태를 재현하지 않음. 미서명이므로 경고 가능성이 높다 |
| 화면 시각 점검(레이아웃) | NOT TESTED | DOM 텍스트·제목만 확인. 스크린샷 없음 |
| CPU·메모리·디스크 측정 | NOT TESTED | 설치 후 디스크 약 625 MB(설치 폴더 바이트 합계)만 기록 |
| 정상 종료(창 닫기) 시 데이터 플러시 | NOT TESTED | 시험은 프로세스 종료 방식. 재시작 후 키·DB 정상은 확인 |
| 서명·무결성 검증 | FAIL | 미서명. 해시(SHA-256)는 게시 파일과 VM 전달본이 일치함을 확인(전송 무결성 검사 통과) |

## 5. 패키지 사전 점검 (호스트, 읽기 전용)

점검 도구: 시험 보조 스크립트(저장소 밖). `app.asar`를 임시 폴더에 풀어 파일 이름과 비밀 패턴만 검사하고 값은 출력하지 않았다.

| 항목 | 판정 | 결과 |
|---|---|---|
| `.env`·인증서·개인 키·DB 파일 | PASS | 없음 (`app.asar` 869개 파일, 에이전트 134개 파일) |
| API 키·토큰·DB URL 비밀번호·세션 시크릿 패턴 | PASS | 일치 없음 (asar·에이전트 파일·에이전트 exe) |
| 개발 PC 경로(`C:\Users\user\`) | PASS | 일치 없음 |
| 개발 저장소 상대 경로(`.validation/`) 문자열 | FAIL(경미) | 포장된 `dist/tests`의 E2E 4개 파일에만 존재 — 테스트 코드가 제품에 들어 있기 때문 (KNOWN_ISSUES K-03) |
| 테스트 코드 / 소스맵 / `.d.ts` | FAIL(경미) | 63 / 106 / 329개 포함 (기능 영향 없음, 용량·정보 노출) |
| `.git*` 파일 | FAIL(경미) | 2개 (node_modules/dist/ui/web) |
| `typescript` | 정보 | 140개 파일 포함 — `impact-worker.ts`가 런타임에 import하므로 **필요** (이전 문서의 "제외 후보"는 정정함) |
| 최상위 비표준 파일 | PASS | 없음 (`resources`에는 `agent`, `app.asar`만) |

## 6. 이번 5차에서 실제 실행한 명령 요약

- `npm --prefix desktop run make` (0.7.0 후보는 이전 4차 산출물을 보존, 0.7.1 시험 빌드 1회, 저장소 `desktop/package.json`은 `git checkout`으로 되돌림)
- 패키지 점검 스크립트(읽기 전용), VM 스크립트 `tools/vm/test_release_vm.ps1` 2회(UAC 승인 후)
- 체크포인트 `pre-p10-installer` 복원(각 실행 전후), 호스트 사용자 데이터는 열지도 쓰지도 않음

## 7. 보존된 증거

`.validation/p11-vm/Release-result-run1.json`, `.validation/p11-vm/Release-result.json` (gitignored). 설치 파일과 해시는 `build-output/release-candidates/` (커밋 대상 아님).
