# Windows 플랫폼 통합

목적: 요구되는 OS 동작과 불필요한 플랫폼 기능을 구분한다. 근거: [01 O06/O07/O17/O18](01-repository-analysis.md), [03 기술 비교](03-migration-strategy.md). 관련: N01/N04/N05/N08·P01~P08. 아래는 설계 권고이며 Windows 버전·CPU·배포 채널은 Q01/Q02, toast는 Q09, 기업 보안 정책은 Q10이 미정이다.

## 필수·선택·제외

| 통합 | 분류 | 구현 방향·이유 |
|---|---|---|
| 폴더 dialog·Git/파일 상태 | 필수 | N01, user 선택 grant·scope 검사 |
| 외부 에디터·검증 프로세스 | 필수 | N04/N05, 고정 adapter·승인된 계획 |
| 표준 사용자·current-user data ACL | 필수 | 관리자 권한·Windows service 설치 불필요 |
| 설치/제거·서명·안전 업데이트 | 필수 설계·출시 검증 | 실제 installer/auto update 채널은 Q02 이후 확정 |
| single instance·창 상태·정상 종료 | 필수 | 중복writer 방지·작업 중단 복구 |
| 앱 내 통합 알림 | 필수 | N08의 권위 있는 inbox |
| OS toast | 선택 | inbox를 보완, 비밀 내용 없음·실패해도 inbox 유지 |
| tray·자동 실행·다중 작업창 | 제외 | 확정 기능에 필수 아님, 종료/권한 복잡성 억제 |
| 파일 연결·drag/drop·custom URL protocol | 제외 | 폴더 dialog와 browser poll handoff로 충족 |
| 원격 API/AI 기능의 완전 offline | 결정 필요 | Q03, 서버 자료를 허위로 로컬 생성하지 않음 |

## 설치·실행·제거

개발 검증은 Windows 11 x64를 우선 대상으로 제안하지만 최소 지원 OS를 확정한 것은 아니다. Windows 10/ARM64·사내 배포·Store/MSIX 요구가 있으면 dependency/installer 매트릭스를 다시 검증한다. 지원 중인 Electron 안정 버전과 Python packager 버전은 P00에서 실제 호환성 확인 후 lock한다.

초기 배포 후보는 current-user 설치의 Electron Forge/Squirrel.Windows다. 설치 파일·agent·helper는 신뢰된 빌드 파이프라인에서 생성하고 서명/타임스탬프를 검증한다. 사용자 PC에 Node·개발 Python·Docker·PostgreSQL·Redis 설치를 요구하지 않는다. Git은 승인된 Git for Windows 설치를 탐지하는 방향이며 번들링 여부·라이선스는 Q02다. Git이 없으면 read-only remote 분석만 사용 가능하고 설치 방법을 안내한다.

제거는 프로그램 파일만 지우며 프로젝트 원본을 절대 지우지 않는다. 로컬 상태/자격증명 삭제 여부는 별도 사용자 선택과 처리 결과를 제공한다. 잠긴 파일·진행 중 journal이 있으면 제거 전 경고·복구 지침을 표시하고 원본 코드의 자동 되돌리기를 시도하지 않는다.

## 업데이트

자동 확인/다운로드/설치 정책은 Q02에 따른다. 구조는 HTTPS feed·허용된 publisher/서명·hash·버전 검증, app/agent protocol·schema 호환 검사, local DB backup, 검증/apply 진행 시 설치 보류, 정상 종료 뒤 설치를 요구한다. 중단되면 기존 binary+DB가 실행 가능해야 하며 다운그레이드로 새 schema를 쓰지 않는다. 임의 URL·다른 publisher·재전송된 과거 release를 거부한다. 설치 기술이 바뀌면 업데이트 규약도 재검증한다.

## 파일·Git·OS 권한

한글·공백·Unicode 정규화·긴 경로·CRLF·case-insensitive 충돌·read-only·공유 잠금·UAC·백신 격리·삭제/이동된 폴더를 fixture에 포함한다. Windows reparse point/junction·UNC/network share·device path/ADS는 기본 거부 또는 명시 지원 정책이 필요하다. v1은 local fixed drive를 기본 범위로 제안하고 UNC는 Q01에서 판단한다.

linked worktree는 외부 git-dir/common-dir의 메타데이터 접근을 별도로 승인한다. 다른 workspace 파일 본문은 읽지 않는다. Git `core.fsmonitor`, 외부 diff/textconv·pager·hook·credential helper·submodule의 예상치 못한 실행을 제한한다. `--literal-pathspecs`, `--no-ext-diff`, `--no-textconv`, `-z` 파싱 등을 사용하며 구체 옵션은 실제 Git 버전 fixture로 검증한다.

## 비밀·인증·네트워크

main의 SecretStore는 Windows OS 보호를 이용하고 암호화를 사용할 수 없으면 비밀 저장을 중단한다. 동일 Windows 사용자의 악성 프로세스·관리자 침해까지 방어한다고 주장하지 않는다. 서버 desktop token과 PC의 AI key는 분리하며 renderer/localStorage에 저장하지 않는다. OS 보호 key·암호문 backup만으로 다른 사용자/PC 복원이 가능하다고 안내하지 않는다.

시스템 browser handoff는 [07](07-api-design.md)의 poll 방식이므로 custom protocol·수신 포트가 필요 없다. GitHub OAuth는 서버에서 처리한다. 네트워크 복구 후 미전송 source·승인·명령을 자동 재개하지 않는다. 설치된 로컬 모델은 agent PC에서만 연결하며 임의 LAN endpoint를 허용하지 않는다. 기업 proxy/CA 조건은 Q10이며 TLS 검증을 끄는 대안을 제공하지 않는다.

## 에디터·검증·창

에디터는 검증된 executable과 adapter별 인자로 파일·위치를 연다. Windows PATH 탐지는 보여주고 선택할 수 있게 하며 repo 내부의 같은 이름 executable을 우선 실행하지 않는다. 경로가 없으면 폴더/파일 위치 복사와 재설정을 제공한다. runner는 project scope 검증, command approval, Windows 자식 트리 종료를 함께 적용한다.

창 최소화는 작업 허용 정책에 따라 표시하고 close는 tray로 숨기지 않는다. 새 instance의 argv를 자동 명령으로 처리하지 않고 기존 창을 활성화한다. 앱 재시작 시 마지막 project/view·필터·창 상태만 복원하고 일회 승인·진행 명령은 복원 실행하지 않는다. toast click도 event target 검증 후 앱 내 탐색만 한다.

## P07 초기 editor adapter 확정

사용자는 VS Code·Cursor를 초기 대상으로 확정했다. native 선택한 공식 publisher/product의 실행 파일을 hash와 함께 등록하고 --reuse-window/--goto의 argv 배열만 구성한다. 파일/줄은 agent의 project/snapshot/fileId 및 live hash로 확인하며 열은 UTF16 code unit으로 검사하고 surrogate 중간 위치는 거부한다. 열 단위가 확인되지 않은 diagnostic은 보고 열을 보존하고 에디터에서는 행 시작으로 연다. 실행 파일 변화는 재설정이 필요하고 설정/미설치 실패 시 파일 위치 복사를 제공한다. 설치된 검증 버전은 VS Code1.143.1·Cursor3.27.14이며 실제 화면의 줄·열 인수는 [P07 기록](validation/p07-2026-10-27.md)의 별도 gate다.
