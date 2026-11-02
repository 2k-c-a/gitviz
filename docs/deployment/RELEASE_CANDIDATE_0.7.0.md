# Release Candidate 0.7.0 (재빌드)

빌드 일시: 2026-10-31 01:18 (KST) · 버전: 0.7.0 · 코드 기준 커밋: `4359cea`(브랜치 `release/0.7.0-prep`, 빌드 시 작업 트리 깨끗) · 판정: PASS / FAIL / NOT TESTED (실제 실행 결과만 기록)

## 1. 결론

디버그 포트 차단(K-04), 무인 제거 수정(K-05), 패키지 정리(K-03)를 모두 포함한 미서명 0.7.0 후보를 새로 만들었고, 클린 VM에서 **설치·실행·디버그 포트 거부·제거**를 4개 조합 모두 통과했다. 아래 §6의 **시험하지 않은 항목**이 남아 있으므로 "배포 가능"이 아니라 **"알려진 문제를 안고 VM 시험을 통과한 후보"**로 판정한다. GitHub Releases 업로드·태그 생성/푸시는 하지 않았다.

## 2. 사전 조건

| 조건 | 상태 | 근거 |
|---|---|---|
| 디버그 포트 차단 시험 통과 | 충족 | 패키지 exe 10개 조합 포트 없음, 게이트 23/23 ([알려진 문제](KNOWN_ISSUES.md) K-04) |
| 무인 제거 시험 통과 | 충족 | 수정 후 VM 4개 조합 ([설치 검증 보고](TEST_INSTALLATION_REPORT.md) §8), 이번 후보로도 §5에서 재확인 |
| 배포 패키지 검사 | 충족 | [패키지 감사](RELEASE_PACKAGE_AUDIT.md) |
| 미서명 정책 | 적용 | K-01. `GITVIZ_SIGN_PFX`·`GITVIZ_SIGN_PFX_PASSWORD` 미설정으로 빌드 |

## 3. 생성 파일

위치: `build-output/release-candidates/0.7.0/` (Git 제외, 업로드하지 않음). 빌드: 개발 PC에서 `npm --prefix desktop run make`(CI 아님 — "로컬 검증용" 빌드). 에이전트(`build-output/agent/dist/agent`)는 새로 빌드하지 않고 기존 동결본을 사용했다.

| 파일 | 크기(bytes) | SHA-256 |
|---|---:|---|
| `GitVizWorkspaceSetup.exe` | 180,141,568 | `18b3cc921e91b113fc71a887283d799b9103c5493e5934729985161ef39ff018` |
| `GitVizWorkspace-0.7.0-full.nupkg` | 179,461,477 | `69ff10cebaa417b6cae276995d079fe725bdf7eb9401e0175e44acfe3731c8c1` |
| `RELEASES` | 86 | `373cc461a86f31e100b6fea7744e3bdc48d90895b8b75627ddb84f54f3b80f99` |
| `SHA256SUMS.txt` | 265 | — (위 세 해시 목록) |

- 이전 5차 후보는 덮어쓰기 전에 `build-output/release-candidates/0.7.0-5th-backup/`로 **복사**해 두었다(해시 검증 OK, 설치 파일 SHA-256 `1ed1ba6f…852c0`). 빌드는 바이트 단위로 재현되지 않는다.
- 패키지 내용(`app.asar`, 677 항목): `debug-guard` 포함, 테스트 파일 0개, 소스맵 0개.

## 4. 서명 상태

| 대상 | Authenticode 상태 |
|---|---|
| `GitVizWorkspaceSetup.exe` | **NotSigned** |
| `GitVizWorkspace.exe` | NotSigned |
| `gitviz-workspace-agent.exe` | NotSigned |

미서명은 이번 릴리스에서 수용한 Known Issue(K-01)다. SmartScreen 경고가 나올 수 있다. 사용자에게는 SHA-256 확인을 안내한다.

버전 정보(설치된 exe, VM에서 확인): 파일 버전 0.7.0, 제품 버전 0.7.0, 회사 GitViz, 설명 "GitViz Windows workspace".

## 5. 설치·실행·제거 시험 (클린 Windows 11 VM)

시험: `tools/vm/test_uninstall_vm.ps1 -SetupExe …\0.7.0\GitVizWorkspaceSetup.exe -Label rc070`, 2026-10-31 01:18~01:28. 조합마다 체크포인트 `pre-p10-installer`에서 시작해 표준 사용자로 설치 → 실행 → 사용자 데이터에 표식 파일 기록 → 제거 → 점검 후 복원. 증거: `.validation/p11-vm/Uninstall-rc070-result.json`. 호스트에서는 설치·제거를 하지 않았다.

| 세션 | 제거 방식 | 설치 | 실행 | 버전 정보 | 디버그 포트 거부 | 제거 | 결과 |
|---|---|---|---|---|---|---|---|
| 비대화형(로그온 없음) | 무인 `--uninstall -s` | PASS | PASS | PASS | PASS | PASS | **PASS** |
| 비대화형 | 일반 `--uninstall` | PASS | PASS | PASS | PASS | PASS | **PASS** |
| 대화형(자동 로그온) | 무인 `--uninstall -s` | PASS | PASS | PASS | PASS | PASS | **PASS** |
| 대화형 | 일반 `--uninstall` | PASS | PASS | PASS | PASS | PASS | **PASS** |

각 조합에서 확인한 것(16개 점검 모두 통과):
- 설치: 종료 코드 0, `%LOCALAPPDATA%\GitVizWorkspace\Update.exe` 생성.
- 실행: 25초 뒤 앱 생존, 에이전트 프로세스(`gitviz-workspace-agent.exe`) 실행 중, 앱 명령줄에 `remote-debugging`/`inspect` 없음.
- 버전: `ProductVersion` 0.7.0, 서명 NotSigned.
- 디버그 포트: 설치된 exe에 `--remote-debugging-port=9333`을 주면 종료 코드 2로 종료, 9333 수신 포트 없음.
- 제거: 종료 코드 0, 설치 폴더 파일 212개 모두 삭제, 폴더 자체 삭제, 바로가기 0개, 제어판 항목 제거.
- 보존: 사용자 데이터(`workspace.sqlite3`, `key.dpapi`, `secret-check.bin`, `writer.lock`, 표식 파일)가 남음. 다른 `%LOCALAPPDATA%` 폴더 손실 없음.
- 정리: 남은 프로세스 없음, 정리 작업·스크립트 흔적 없음.

## 6. 시험하지 않은 것 (NOT TESTED)

- **앱 기능 상태:** 이번에는 렌더러를 조사할 수 없으므로(디버그 포트 차단) 에이전트 프로세스 생존까지만 확인했다. 에이전트↔DB 헬스 `ready`와 화면 동작은 설치본에서 확인하지 않았다(호스트의 격리된 폴더에서 패키지 exe의 데이터 파일 생성은 확인함).
- 폴더 등록 UI 등 수동 기능(K-10, 체크리스트 C1~C6).
- 업그레이드(이전 버전 → 0.7.0), 앱 실행 중 제거, 제거 중 중단.
- WMI가 거부되는 환경의 제거 정리 대체 경로(단위 시험만).
- Windows 10, Smart App Control·다운로드 표시(MOTW) 환경, SmartScreen 실제 표시.
- CI(`windows-release.yml`)에서의 기준 빌드. 이 후보는 개발 PC 빌드다.
- 에이전트를 이번에 다시 빌드하지 않았다.
- `tools/vm/test_release_vm.ps1`(디버그 포트로 조사하던 시험)은 차단된 빌드에서 동작하지 않으며 아직 고치지 않았다.

## 7. 지키지 않은/하지 않은 것

GitHub Releases 업로드, 태그 생성·푸시, 기존 사용자 데이터 삭제는 하지 않았다. 시험하지 않은 항목은 배포 적합으로 판정하지 않았다. 이 문서와 `tools/vm/test_uninstall_vm.ps1` 변경은 아직 커밋하지 않았다.
