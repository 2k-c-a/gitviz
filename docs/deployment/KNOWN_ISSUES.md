# 알려진 문제 (5차 기준)

기준일: 2026-10-30 · 심각도: 차단(릴리스 막음) / 중요 / 경미. 원인과 해결 방향은 확인된 사실과 제안을 구분해 적는다.

| ID | 심각도 | 문제 | 원인(확인) | 해결 방향(제안) |
|---|---|---|---|---|
| K-01 | **수용(의도적)** | 0.7.0 설치 파일은 **의도적으로 미서명**(NotSigned). Windows SmartScreen 경고가 나타날 수 있고, 변조 확인 수단은 게시된 SHA-256뿐이다 | 소유자 결정: 서명 인증서를 만들거나 구매하지 않음(2026-10-30) | 이번 릴리스에서 수용한 Known Issue이며 릴리스를 차단하지 않는다. 사용자 안내는 아래 "미서명 배포 안내". 서명 도입은 후속(R4)에서 별도 결정 |
| K-02 | 해결(검증 대기) | 앱 로그 파일이 없음. 오류 시 사용자·지원이 확인할 기록이 없다 | 구현되지 않았었음(데이터 폴더에 로그 없음, Squirrel 로그만 존재) | 2026-11-03: `%APPDATA%\gitviz-workspace\logs\app.log` 회전 로그 추가(`desktop/src/app-log.ts`). 정해진 이벤트와 코드값만 쓰고 그 밖의 값은 `[redacted]`로 바꾼다. 단위 시험만 했고 설치본·VM 실행은 NOT TESTED. 에이전트(Python) 쪽 로그는 없음 |
| K-03 | 해결(검증 대기) | 릴리스 빌드에서 테스트 코드·소스맵·`.d.ts`·`.git*`·패키지 README/CI 설정 300개(912,052 B)를 `app.asar`에서 제외했다. `dist/tests` 안에 있던 개발 경로 문자열(`.validation/`)도 함께 사라졌다 | `forge.config.ts`에 릴리스 전용 제외 규칙이 없었음 | 규칙 적용 완료([패키지 감사](RELEASE_PACKAGE_AUDIT.md)). 새 패키지(`0.7.0-pkgopt`)의 클린 VM 설치 시험은 아직 하지 않았다. 검증용 패키지 변형은 `dist/tests`를 유지한다 |
| K-04 | 해결 | **포장된 실행 파일이 `--remote-debugging-port`를 받아 렌더러를 외부 프로세스가 조종할 수 있었다**(수정 전: `-`·`/` 접두사, `=0` 무작위 포트, `--remote-debugging-address`도 모두 열림. `--inspect=<포트>`는 Node 검사 포트가 열림. `ELECTRON_EXTRA_LAUNCH_ARGS`·`NODE_OPTIONS`로는 열리지 않음) | Electron 기본 동작을 막지 않음 | `desktop/src/debug-guard.ts` + `main.ts` 맨 앞: **`app.isPackaged`일 때** 명령줄에 `remote-debugging-*`(port, pipe, address, socket-name)·`inspect*`·`debug-port`가 있으면 아무 것도 시작하지 않고 종료 코드 2로 즉시 종료(무시하고 계속 실행하지 않음). 개발(비포장) 실행은 그대로 허용. 그 밖의 실행 인자는 건드리지 않음. 패키지 빌드 10개 조합에서 포트가 열리지 않음을 확인(개발 빌드는 그대로 열림). **한계:** 설치 파일(Squirrel)로 다시 빌드한 후보의 VM 설치·실행 시험은 아직 하지 않음(NOT TESTED). 기존 `tools/vm/test_release_vm.ps1`·호스트 스모크는 이 포트로 렌더러를 조사했으므로 패키지 빌드에서는 더 이상 동작하지 않는다 |
| K-05 | 해결 | **무인 제거 후 설치 폴더 파일 잔존**(수정 전 `.dead`, `Update.exe`, `app-0.7.0\squirrel.exe`, `app-0.7.0\v8_context_snapshot.bin`와 빈 `app-0.7.0` 폴더). 사용자 로그온 세션이 없는 비대화형 제거에서만 발생, 대화형은 정상 | 제거 후 정리 도우미를 작업 스케줄러 일회성 작업으로 시작했는데, 이 작업은 사용자의 대화형 로그온이 있을 때만 실행된다. 비대화형에서는 `/Run`이 종료 코드 0을 내도 도우미가 시작되지 않았다(로그에 `helper: process started` 없음) | 도우미를 WMI(`Win32_Process.Create`)로 먼저 시작하고, WMI가 거부될 때만 기존 일회성 작업으로 대체하도록 변경(`desktop/src/squirrel.ts`). 클린 VM에서 무인·일반 제거를 대화형·비대화형 세션 4가지 모두 통과. 아직 시험하지 않은 것: WMI가 거부되는 환경에서의 대체 경로(단위 시험만 있음) |
| K-06 | 중요 | 데이터 위치가 Roaming `%APPDATA%\gitviz-workspace\workspace`. 도메인 로밍 프로필에서 SQLite·DPAPI 키가 동기화 대상이 될 수 있음 | 현재 설계(R3로 `%LOCALAPPDATA%` 이전 계획) | 업그레이드 시 데이터 이전 루틴과 함께 변경 |
| K-07 | 경미 | 환경 가드·채널 설정(R1) 미적용. 개발/운영 구분은 코드 경로에 의존 | 미구현 | R1 |
| K-08 | 경미 | `app/services/analysis_snapshots.py:207`이 `app.api.analytics`를 import(서비스→API 역의존) | 4차 범위 밖 | 후속 리팩터링 |
| K-09 | 경미 | 에이전트용 venv가 `.validation/venv-workspace` | 이동은 승인 필요로 보류 | `build-output/venv-agent`로 이동(승인 후) |
| K-10 | 정보 | 검증하지 못한 항목: 프로젝트 등록 UI, 계정 간 접근 분리, 실제 데이터 DB 업그레이드, 스키마 변경 업그레이드, SmartScreen, 자원 사용량, 로그 쓰기 실패, 업데이트 중단, DB 손상 | 자동화 불가 또는 시험 시간 | 수동 인수 시험(체크리스트 §수동) |
| K-11 | 정보 | 시험 DB는 신규 빈 DB라 마이그레이션 경로(스키마 8) 실데이터 검증은 이전 단계 단위 시험(P06/P07)에 의존 | | 스키마 변경 시 업그레이드 시험 추가 |
| K-12 | 중요 | **Git for Windows가 필수인데 설치 안내에 없다.** 앱은 `C:\Program Files\Git\cmd\git.exe` 또는 `%LOCALAPPDATA%\Programs\Git\cmd\git.exe`만 찾는다. 클린 VM(Git 없음)에서 불완전한 `.git` 구조의 폴더를 등록하면 오류·목록 없이 아무 일도 일어나지 않은 것처럼 보였다(원인이 Git 부재인지 구조 불완전인지는 분리하지 못함) | 설치 문서가 Git을 요구 사항으로 적지 않고, Git 없음(`GIT_NOT_FOUND`)을 화면에 알리는 경로를 확인하지 못함 | 2026-11-03: 설치 안내에 Git 필요와 찾는 경로를 적었다([설치 가이드 §0](INSTALLATION_GUIDE.md)). 앱 로그에 `git.check found=` 기록. **남은 일:** Git이 없을 때 등록 화면에서 안내 문구를 표시 ([수동 검증](MANUAL_TEST_RESULTS_0.7.0.md) §1.2 F1·F2) |
| K-13 | 경미 | 폴더 등록·열기 오류가 내부 코드 그대로 표시됨: `ACCESS_DENIED`, `NOT_A_REPOSITORY`, `PROJECT_UNAVAILABLE`(후자만 한국어 안내 동반). 크래시·데이터 손상은 없음 | 오류 코드를 사용자 문구로 바꾸는 단계 없음 | 코드별 한국어 안내 문구 ([수동 검증](MANUAL_TEST_RESULTS_0.7.0.md) §1.2 F3) |

## 정정

- 이전 문서(2차·3차)가 `typescript`를 제외 후보로 적었으나 `desktop/src/impact-worker.ts`가 런타임에 import하므로 **유지**해야 한다. 관련 문서를 정정했다.


## 미서명 배포 안내 (0.7.0)

- 0.7.0은 **의도적으로 코드 서명 없이** 배포한다(소유자 결정). 서명 인증서를 새로 만들거나 구매하지 않고 Authenticode 서명을 하지 않는다. 서명되지 않은 설치 파일을 정상적인 Release Candidate로 취급하며, 서명 부재만으로 릴리스 판정을 막지 않는다.
- Windows SmartScreen 또는 Smart App Control이 "인식할 수 없는 앱" 경고를 표시할 수 있다. 이 경고의 실제 발생 여부와 정도는 미검증이다(K-10).
- 사용자 확인 방법: 설치 파일은 이 저장소의 GitHub Releases 페이지에서만 받는다. 함께 게시되는 `SHA256SUMS.txt`와 `Get-FileHash .\GitVizWorkspaceSetup.exe -Algorithm SHA256` 결과가 같은지 확인한다. 경고가 나오면 출처와 해시를 확인한 사용자가 "추가 정보 → 실행"을 선택할 수 있다.
- 서명이 없으므로 게시자(publisher) 신원은 Windows가 보증하지 않는다. 해시 확인은 게시 파일과 받은 파일이 같음만 보증한다(게시 위치가 침해되면 해시도 같이 바뀔 수 있다).
