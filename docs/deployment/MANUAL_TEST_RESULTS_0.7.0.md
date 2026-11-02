# 수동 검증 결과 (0.7.0 Release Candidate)

작성일: 2026-10-31 · 대상: `build-output/release-candidates/0.7.0/GitVizWorkspaceSetup.exe` (180,141,568 bytes, SHA-256 `18b3cc921e91b113fc71a887283d799b9103c5493e5934729985161ef39ff018`, 빌드 2026-10-31 01:18, 커밋 `4359cea`) · 판정: PASS / FAIL / BLOCKED / 사용자 확인 필요 · 시험 환경: 클린 Windows 11 VM `GitViz-P01-Clean-Win11`(Windows 11 Home 10.0.26300, ko-KR, 1024×768), 체크포인트 `pre-p10-installer`에서 시작하고 끝나면 복원. 호스트에는 설치하지 않았다.

## 0. 요약

| # | 시험 | 판정 | 비고 |
|---|---|---|---|
| 1 | 폴더 등록 UI | **PASS** (발견 사항 3건) | VM의 실제 데스크톱에서 설치본을 실행하고 화면을 캡처해 확인. 단, 시험에 Git for Windows를 호스트에서 복사해 설치했다(§1.3). Git 미설치 시 동작과 오류 문구는 발견 사항 F1~F3 |
| 2 | 계정 간 접근 분리 | **PASS** (범위: 파일 시스템 권한) | 두 표준 계정, 18개 접근 시도 모두 거부. 앱 내부 암호화 분리는 NOT TESTED |
| 3 | SmartScreen | **관찰 확인 (사용자 보고)** | 사용자가 인터넷 PC에서 브라우저로 내려받아 실행: `Windows의 PC 보호` 창, 알 수 없는 게시자, 실행 차단 문구, `추가 정보` 후 `실행` 버튼 표시. 에이전트가 직접 보지 않았고 스크린샷·해시·스마트 앱 컨트롤 상태·설치 완료 여부는 아직 받지 못했다(§3.4). 서명 없음은 여러 방식으로 확인함 |

시험 1·2는 증거가 있고, 시험 3은 사용자가 실제 동작을 보고했다. 필수 조건은 모두 관찰 단계에서 충족됐으나, 시험 3은 에이전트가 직접 확인하지 않은 사용자 보고이고 보조 증거가 비어 있다(§3.4). 판정 갱신은 `FINAL_RELEASE_REVIEW_0.7.0.md`를 따른다.

## 1. 시험 1 — 폴더 등록 UI

- 날짜: 2026-10-31 02:20~02:37
- Windows 버전·환경: 위 VM. 표준 사용자 `P01Tester`가 자동 로그온한 대화형 콘솔 세션(세션 1). 앱은 `GitVizWorkspaceSetup.exe`를 실행해 설치하고, 바탕화면 바로가기를 더블클릭해 실행했다.
- 방법: 에이전트가 VM 데스크톱 안에서 마우스 클릭·키 입력을 보내고 화면을 캡처해 직접 관찰했다(디버그 포트는 사용하지 않음). 사람이 직접 한 시험이 아니다.
- 판정: **PASS**

### 1.1 결과

| 항목 | 절차 | 기대 | 실제 | 판정 | 증거 |
|---|---|---|---|---|---|
| 설치·실행 | Setup 실행(종료 코드 0), 바탕화면 바로가기 더블클릭 | 앱 창이 열림 | 창 제목 `시작하기 · GitViz Workspace`, 하단 `로컬 워크스페이스 기반 준비되었습니다`. 설치 후 앱이 자동으로 열리지는 않았다 | PASS | `out/04-launch/04-after-shortcut.png` |
| 폴더 선택 창 실행 | `폴더 선택…` 클릭 | 선택 창이 열림 | 창 제목 `로컬 Git 프로젝트 선택`의 Windows 폴더 선택 창이 열림 | PASS | `out/12-register/12a-dialog-open.png` |
| 폴더 선택 | 창의 `폴더` 입력란에 `C:\Users\P01Tester\Documents\TestRepo` 입력 후 `폴더 선택` | 선택됨 | 창이 닫히고 앱으로 돌아옴(탐색 트리에서 클릭하지 않고 경로를 입력했다) | PASS | `12-register/12b-path-typed.png` |
| 등록 성공 | (자동) | 등록되고 정보 표시 | 창 제목 `TestRepo — main · GitViz Workspace`, 프로젝트 선택에 `TestRepo`, 브랜치 `main`, `변경 2`, 개요에 Git 상태(Unstaged 1, Untracked 1) | PASS | `12-register/12d-after-select-8s.png` |
| 등록 폴더 표시 | 프로젝트 화면 | 목록에 표시 | 프로젝트 목록에 `TestRepo` | PASS | `out/13-restart/13b-relaunched.png` |
| 재실행 후 유지 | 창 닫기(앱·에이전트 프로세스 0개 확인) → 바로가기로 재실행 | 등록 정보 유지 | 재실행 후 `TestRepo`가 목록에 그대로 있음 | PASS | `13-restart/13b-relaunched.png`, 전사 `13-restart.transcript.txt` |
| 존재하지 않는 폴더 | 앱을 닫고 `TestRepo`를 `TestRepo-moved`로 이름 변경 → 재실행 → 프로젝트 열기 | 크래시 없이 오류 표시 | 앱 생존(프로세스 4개), 제목 `TestRepo (접근 불가)`, 화면에 `프로젝트 정보를 읽지 못했습니다. PROJECT_UNAVAILABLE: 폴더 위치와 접근 권한을 확인하세요.`와 `다시 시도` 버튼 | PASS (문구 F3) | `out/14-missing/14c-open-7s.png` |
| 접근 권한 없는 폴더 | `icacls /deny P01Tester:(OI)(CI)(R)`로 `Documents\Locked`를 막고(목록 조회가 `UnauthorizedAccessException`으로 거부됨을 확인) 선택 | 크래시 없이 오류, 등록 안 됨 | 앱 생존, 화면에 `ACCESS_DENIED`만 표시, 프로젝트 목록은 그대로(추가 없음) | PASS (문구 F3) | `out/15-denied/15a-locked-typed.png`, `15c-locked-8s.png` |
| 다른 사용자 프로필 폴더 | `C:\Users\P01Admin` 선택 | 같음 | 앱 생존, `ACCESS_DENIED`, 목록 변화 없음 | PASS (문구 F3) | `out/16-others/16a-otheruser-after-7s.png` |
| Git 저장소가 아닌 폴더(추가) | `Documents\PlainFolder` 선택 | 오류, 등록 안 됨 | 앱 생존, `NOT_A_REPOSITORY`, 목록 변화 없음 | PASS (문구 F3) | `16-others/16b-plainfolder-after-7s.png` |

증거 위치: `.validation/manual-0.7.0/out/<단계>/` (Git 제외). 각 단계 폴더에 스크린샷(`*.png`)과 실행 기록(`*.transcript.txt`)이 있다. 하네스 로그: `.validation/manual-0.7.0/harness-status.txt`.

### 1.2 발견 사항 (제품 개선 후보, 판정을 바꾸지 않음)

- **F1. Git for Windows가 필수인데 설치 안내에 없다.** 앱은 `C:\Program Files\Git\cmd\git.exe` 또는 `%LOCALAPPDATA%\Programs\Git\cmd\git.exe`에서 Git을 찾는다(`app/workspace/git_service.py` `installed_git`, 없으면 `GIT_NOT_FOUND`). 클린 VM에는 Git이 없고 `docs/deployment/INSTALLATION_GUIDE.md`에도 Git 요구 사항이 적혀 있지 않다.
- **F2. Git이 없을 때 폴더 등록이 아무 안내 없이 끝나는 것으로 관찰되었다.** Git이 없는 VM에서 `.git` 폴더만 흉내 낸 폴더(`HEAD`·`config`만 있는 빈 구조)를 선택했을 때 화면은 `시작하기` 그대로였고 오류도 목록도 없었다(`.validation/manual-0.7.0/out/08-retry/08-frame2.png`, 처음 시도는 `06-select/06b-after-select.png`). 같은 앱이 일반 폴더에는 `NOT_A_REPOSITORY`를 표시하므로, 원인이 "Git 없음"인지 "불완전한 저장소 구조"인지는 **분리하지 못했다.** 확인된 사실은 "Git이 없는 클린 환경에서 등록이 조용히 실패한 것처럼 보였다"까지다.
- **F3. 오류가 내부 코드 그대로 표시된다.** `ACCESS_DENIED`, `NOT_A_REPOSITORY`, `PROJECT_UNAVAILABLE`이 화면 문구에 그대로 나온다(`PROJECT_UNAVAILABLE`만 한국어 안내가 붙는다). 크래시·데이터 손상은 없다.

### 1.3 시험의 한계

- **Git을 호스트에서 복사했다.** 정상 경로(등록·재실행·권한 오류) 시험은 호스트의 `C:\Program Files\Git`(2.53.0)을 VM의 같은 경로에 풀어 넣은 뒤 했다. 설치 파일만으로 만든 클린 환경의 결과가 아니다(F1·F2 참조).
- 시험용 저장소는 VM에서 `git init`으로 만든 1커밋 저장소 `TestRepo`다. 실제 사용자 저장소가 아니다.
- 폴더는 탐색 트리가 아니라 경로 입력으로 골랐다. 폴더가 이미 등록된 상태에서 앱이 실행 중일 때 이름을 바꾸는 경우는 시험하지 않았다(앱을 닫고 바꿈).
- 한국어(ko-KR) Windows 11 1024×768 화면에서만 확인했다.
- 에이전트가 화면을 보고 판단한 결과이며, 사람이 같은 절차를 한 번 더 확인하는 것을 막지 않는다.

## 2. 시험 2 — 계정 간 접근 분리

- 날짜: 2026-10-31 01:41~01:44
- Windows 버전: 클린 VM `GitViz-P01-Clean-Win11` — Windows 11 Home 10.0.26300, ko-KR (체크포인트 `pre-p10-installer`에서 시작, 끝나면 복원). 호스트에서는 설치하지 않았다.
- 판정: **PASS (범위: 파일 시스템 권한)**
- 증거: `.validation/p11-vm/Isolation-rc070d-result.json`. 시험 스크립트: `tools/vm/test_account_isolation_vm.ps1`(앞선 세 실행은 시험 준비 단계(계정 생성)의 스크립트 오류로 실패했고, 스크립트를 고친 `rc070d`가 통과했다. 실패 기록: `Isolation-rc070-result.json`, `Isolation-rc070b-result.json`, `Isolation-rc070c-result.json`).

### 2.1 환경

계정 A = `P01Tester`(표준 사용자), 계정 B = `P01TesterB`(시험 중 새로 만든 표준 사용자, 로컬 관리자 그룹에 없음. 관리자 그룹 구성원은 `Administrator`, `P01Admin`뿐). 두 계정 모두 같은 설치 파일로 사용자별 설치(`%LOCALAPPDATA%\GitVizWorkspace`)했다.

### 2.2 절차

1. A로 설치·실행 후 데이터 생성(DB, 키, 표식 파일 `marker-A.txt`).
2. B로 설치·실행 후 데이터 생성(`marker-B.txt`).
3. A로 B의 프로필·데이터 폴더·설치 폴더에 9가지 접근을 시도.
4. B로 A에 대해 같은 9가지 시도(반대 방향).
5. 각 계정이 자기 폴더에서 자기 데이터만 보는지 확인.

### 2.3 기대 결과

모든 교차 접근이 파일 시스템 수준에서 거부된다. 각 계정은 서로 다른 키·SID를 가지며 자기 표식 파일만 본다.

### 2.4 실제 결과

| 시도 | A → B | B → A |
|---|---|---|
| 상대 프로필 폴더 목록 | 거부 (`UnauthorizedAccessException`) | 거부 |
| 상대 데이터 폴더 목록 | 거부 | 거부 |
| 상대 표식 파일 읽기 | 거부 | 거부 |
| 상대 DB(`workspace.sqlite3`) 읽기 | 거부 (`MethodInvocationException`*) | 거부 (*) |
| 상대 키 파일(`key.dpapi`) 읽기 | 거부 (*) | 거부 (*) |
| 상대 DB 복사 | 거부 | 거부 |
| 상대 데이터 폴더에 파일 쓰기 | 거부 | 거부 |
| 상대 설치 폴더 목록 | 거부 | 거부 |
| 상대 데이터 폴더의 ACL 변경 | 거부 (`icacls` 실패) | 거부 |

\* `File.ReadAllBytes`가 접근 거부를 감싸 던진 예외다. 내부 예외 종류는 기록하지 않았다(다른 시도는 `UnauthorizedAccessException`으로 직접 확인).

그 밖의 결과:
- 18개 시도 모두 `denied`, `allowed` 0개.
- 각 데이터 폴더의 ACL(`icacls`)에는 해당 계정만 `(OI)(CI)(F)`로 표시됐다: `…\P01Tester\…\workspace GITVIZ-P01\P01Tester:(OI)(CI)(F)`, `…\P01TesterB\…\workspace GITVIZ-P01\P01TesterB:(OI)(CI)(F)`.
- 키 파일 해시가 서로 다르고(`4AA9C4CD…` / `816EA4A6…`) SID도 다르다. DB 파일 해시는 같았다: 두 DB 모두 방금 만든 빈 DB라서 내용이 같기 때문이며 분리의 증거가 아니다.
- A의 폴더에는 `marker-A.txt`만, B의 폴더에는 `marker-B.txt`만 있다.
- UI에서 숨기는 것이 아니라 실제 파일 접근 시도가 거부된 결과다.

### 2.5 이 시험이 확인하지 않은 것

- **관리자 계정은 다른 사용자의 프로필을 읽을 수 있다.** 이것은 Windows 기본 동작이며 이 시험은 관리자 접근을 시도하지 않았다(NOT TESTED). 관리자에 대해서는 파일 권한이 아니라 키 암호화가 보호 수단이다.
- **앱 내부의 암호화 분리**(Electron `safeStorage`로 감싼 키가 다른 계정에서 풀리지 않는지)는 확인하지 못했다. 키 파일을 PowerShell로 직접 풀 수 없고 이 시험에서는 앱 화면 관찰을 하지 않았다. NOT TESTED.
- **앱 내부 접근 제어**: 앱은 계정별 로컬 데이터 폴더에 의존하는 단일 사용자 앱이며, 계정 구분을 앱이 따로 하지 않는다(설계상 없음).
- 같은 PC에서 두 계정이 동시에 로그온한 상태, 빠른 사용자 전환, 로밍 프로필(K-06)은 시험하지 않았다. 시험은 PowerShell Direct(비대화형 세션)로 수행했다.

## 3. 시험 3 — Windows SmartScreen

- 날짜: 2026-10-31 02:35~02:37 · 환경: 위 VM(대화형 세션)
- 판정: **관찰 확인(사용자 보고).** 에이전트의 VM 시험(§3.1~3.2)은 BLOCKED였고, 아래 §3.3 사용자 관찰로 실제 동작이 확인됐다.

### 3.1 확인한 사실

| 항목 | 값 | 증거 |
|---|---|---|
| 설치 파일 SHA-256 (VM에 놓은 복사본) | `18B3CC921E91B113FC71A887283D799B9103C5493E5934729985161EF39FF018` — 호스트 값과 같음 | `out/20-smartscreen-prep/20-smartscreen-prep.transcript.txt` |
| 크기 | 180,141,568 bytes | 같은 곳, 파일 속성 화면 |
| Authenticode 서명 상태 | **NotSigned**, 서명자 인증서 없음 (`Get-AuthenticodeSignature`) | 같은 곳 |
| 파일 속성 > 디지털 서명 탭 | 탭은 **있으나** `포함된 서명`·`카탈로그 서명` 목록이 모두 **비어 있음** → 서명 없음 | `out/23-sigtab-keys/23-signature-tab.png` |
| 파일 속성 > 일반 탭 | 다운로드 표시가 있으면 `이 파일은 다른 컴퓨터로부터 왔으며… 차단 해제` 체크박스가 표시됨 | `out/20-smartscreen-prep/20a-properties-general.png` |
| 설치된 exe 서명 | NotSigned (후보 VM 시험의 `version-info` 단계) | `.validation/p11-vm/Uninstall-rc070-result.json` |
| VM의 Smart App Control 값 | 레지스트리 `VerifiedAndReputablePolicyState` = 2(일반적으로 "평가 모드"로 알려진 값, 이 값의 의미는 Microsoft 문서로 확인하지 않았다) | `out/02-env/02-env.transcript.txt` |
| VM의 SmartScreen 설정값 | `Explorer\SmartScreenEnabled` 값이 없음(기본값) | 같은 곳 |
| 인터넷 연결 | **없음**(`www.msftconnecttest.com` 이름 확인 실패, 작업 표시줄 아이콘도 연결 없음) | 같은 곳 |

정정: 앞 판 문서에서 "디지털 서명 탭이 없어야 맞다"고 안내했으나 틀렸다. Windows 11은 서명이 없어도 탭을 표시하고 목록이 빈다.

### 3.2 관찰 (실제 SmartScreen 동작의 증거가 아님)

다운로드 표시(`ZoneId=3`, `HostUrl=https://example.invalid/…`)를 **직접 붙인** 복사본(`Downloads\GitVizWorkspaceSetup.exe`)을 `Start-Process`로 실행했다. SmartScreen 경고 창은 나타나지 않았고 설치 프로그램의 초록색 시작 화면이 바로 보였다(`out/21-sigtab-run/21b-run-after-5s.png`). 이 결과를 "경고가 뜨지 않는다"로 해석하면 안 된다. 이유:

- VM에 인터넷이 없어 SmartScreen이 평판을 조회할 수 없었다.
- 다운로드 표시를 브라우저가 붙인 것이 아니라 스크립트가 붙였고, 실행도 탐색기 더블클릭이 아니라 `Start-Process`였다.
- Smart App Control이 평가 모드로 보이는 환경이며, 호스트나 일반 사용자 PC와 다르다.

### 3.3 사용자 관찰 (2026-10-31, 사용자 보고)

사용자가 인터넷에 연결된 Windows PC에서 메일로 받은 설치 파일(브라우저 다운로드)을 실행해 다음을 보고했다. 에이전트가 직접 관찰하지 않았다.

| 항목 | 보고된 값 |
|---|---|
| SmartScreen 창 | 표시됨. `Windows의 PC 보호` 파란 창 |
| 게시자 | 알 수 없는 게시자 |
| 문구 | "스마트스크린에서 인식할 수 없는 앱의 시작을 차단했습니다. 이 앱을 실행하면 PC가 위험에 노출될 수 있습니다." |
| 버튼 | `추가 정보` 클릭 후 `실행` 버튼이 보임(실행 가능, 완전 차단 아님) |

K-01(미서명 수용)에서 예상한 동작과 일치한다.

### 3.4 보고에 없는 항목 (NOT TESTED / 미수신)

- 실행한 파일의 SHA-256(`18b3cc92…f39ff018`과 일치하는지)
- 스마트 앱 컨트롤 상태(켜짐/평가/꺼짐)
- `실행` 후 설치가 끝까지 완료되고 앱이 열렸는지
- 스크린샷 등 증거 파일
- 다운로드 경로(메일 첨부/Drive 링크)와 PC의 Windows 버전

## 4. 시험 방법과 한계

- VM UI 조작은 임시 하네스(저장소 밖 `C:\AI_Workspace\temp\claude\gitviz\scripts\vm_ui\`)로 했다. 하네스가 VM 데스크톱 세션에서 마우스·키 입력과 화면 캡처를 수행한다. 저장소에 넣지 않았다.
- 사고 기록: 01:57의 첫 하네스 시도가 사용자님이 쓰고 있던 VM을 강제 종료·복원했다(시작 전 확인이 없던 정리 단계 때문). 해당 스크립트(`tools/vm/test_account_isolation_vm.ps1`과 하네스)는 "자신이 복원해서 시작한 VM만 정리"하도록 고쳤다.
- 새 시험 스크립트 `tools/vm/test_account_isolation_vm.ps1`과 이 문서는 아직 커밋하지 않았다(`tools/vm/test_uninstall_vm.ps1`의 이전 변경도 미커밋).
- GitHub Releases 업로드, 태그 생성·푸시는 하지 않았다. 호스트 사용자 데이터는 건드리지 않았다. 시험 후 VM은 체크포인트로 복원했다. 시험하지 않은 항목을 배포 적합으로 판정하지 않았다.

## 5. 남은 항목

1. 시험 3 보조 증거(§3.4): 해시, 스마트 앱 컨트롤 상태, 설치 완료 여부, 스크린샷 — 사용자 회신 대기.
2. 발견 사항 F1(설치 안내에 Git 필요 명시), F2(Git 없을 때 안내), F3(오류 코드 문구): 개선 후보. 배포 차단 여부는 소유자가 정한다.
3. (선택) 시험 2의 앱 내부 암호화 분리와 관리자 접근.
