# 최종 배포 판정 (0.7.0)

판정일: 2026-10-31 · 대상: `build-output/release-candidates/0.7.0/`(빌드 2026-10-31 01:18, 커밋 `4359cea`) · 목표 판정: RELEASE READY WITH KNOWN ISSUES · 규칙: 증거가 없으면 PASS로 판정하지 않는다.

## 1. 최종 판정

# **NOT READY FOR RELEASE**

필수 조건 7개가 모두 관찰 단계에서 충족됐다. 마지막 SmartScreen 항목은 2026-10-31 사용자 보고(PC 보호 창, 알 수 없는 게시자, `추가 정보` → `실행` 가능)다. 이 판정란은 **사용자가 실행한 파일의 SHA-256 일치, 설치 완료 여부, 스크린샷을 확인한 뒤** `RELEASE READY WITH KNOWN ISSUES`로 올린다. 그 전에는 증거 없는 항목을 PASS로 확정하지 않는 규칙에 따라 이 표기를 유지한다. 배포 승인은 별도로 사용자가 한다.

## 2. 검토 대상 문서

| 문서 | 상태 |
|---|---|
| `KNOWN_ISSUES.md`, `RELEASE_PACKAGE_AUDIT.md`, `RELEASE_CANDIDATE_0.7.0.md` | 있음, 검토함 |
| `CODE_SIGNING_AUDIT.md` | **없음.** 서명 정책은 `KNOWN_ISSUES.md` K-01과 `BUILD_AND_RELEASE.md` §6에 있어 그 내용으로 판정했다 |
| `MANUAL_TEST_RESULTS_0.7.0.md` | **없음.** 수동 시험이 수행되지 않았다. 근거는 `RELEASE_CHECKLIST.md` C1~C6 |

## 3. 항목별 판정

| # | 항목 | 판정 | 근거 |
|---|---|---|---|
| A1 | 미서명 배포 정책 명시 | PASS | K-01(소유자 결정 2026-10-30), `BUILD_AND_RELEASE.md` §6, 사용자 안내와 SHA-256 확인 방법 |
| A2 | SmartScreen 경고 가능성 기록 | PASS | K-01, `KNOWN_ISSUES.md` 사용자 안내 |
| A3 | 실제 서명 상태 확인 | PASS | `Get-AuthenticodeSignature`: 설치 파일·앱 exe·에이전트 exe 모두 `NotSigned`, VM의 설치된 exe도 `NotSigned`(미서명은 허용) |
| B1 | 패키지 Release에서 디버그 포트 비활성 | PASS | 패키지 exe 10개 조합 + 설치본 VM 4개 조합에서 `--remote-debugging-port`가 종료 코드 2로 거부됨 |
| B2 | 프로세스 실행 인자 확인 증거 | PASS | 시험 전 5개 프로세스 중 4개에 플래그, 시험 후 0개. 설치본에서 앱 명령줄에 `remote-debugging`/`inspect` 없음(`.validation/p11-vm/Uninstall-rc070-result.json`) |
| B3 | 실제 포트가 열려 있지 않음 | PASS | netstat 수신 포트 없음, 설치본에서 9333 미수신 |
| C1 | 잔존 파일 4개 해결 | PASS | 수정 후 설치 폴더 파일 212개 삭제, 폴더 자체 삭제, 4개 조합 |
| C2 | Silent Uninstall 통과 | PASS | 비대화형·대화형 모두 `--uninstall -s` 종료 코드 0 |
| C3 | 사용자 데이터 보존 | PASS | 제거 후 `workspace.sqlite3`, `key.dpapi`, 표식 파일 등 유지, 다른 앱 폴더 손실 없음 |
| D1 | 폴더 등록 UI | PASS | 2026-10-31 클린 VM 대화형 세션에서 설치본으로 확인([수동 검증 결과](MANUAL_TEST_RESULTS_0.7.0.md) §1): 선택 창·등록·표시·재실행 후 유지·없는 폴더·권한 없는 폴더. Git 미설치 시 동작과 오류 문구는 발견 사항 F1~F3 |
| D2 | 계정 간 접근 분리 | PASS (범위: 파일 시스템 권한) | 표준 계정 2개, 18개 접근 시도 모두 거부([수동 검증 결과](MANUAL_TEST_RESULTS_0.7.0.md) §2). 앱 내부 암호화 분리는 NOT TESTED |
| D3 | SmartScreen 동작 확인 | **PASS (사용자 보고)** | 인터넷 PC에서 브라우저 다운로드 후 실행: `Windows의 PC 보호` 창, 알 수 없는 게시자, `추가 정보` → `실행` 가능([수동 검증 결과](MANUAL_TEST_RESULTS_0.7.0.md) §3.3). 에이전트 직접 관찰 아님, 해시·스크린샷·스마트 앱 컨트롤 상태 미수신(§3.4) |
| E1 | 테스트 코드·소스맵 제외 | PASS | `app.asar` 677 항목: 테스트 파일 0, `.map` 0 |
| E2 | 설치 파일 무결성 | PASS | `sha256sum -c SHA256SUMS.txt` 3개 모두 OK(이 문서 작성 시 재확인). VM에서 설치한 파일과 동일한 파일 |
| E3 | SHA-256 기록 | PASS | §6 |

## 4. 필수 조건 판정

| 조건 | 결과 |
|---|---|
| 미서명 정책 수용·문서화 | 충족 |
| 디버그 포트 차단 검증 | 충족 |
| 무인 제거 해결 | 충족 |
| 폴더 등록 UI 테스트 통과 | 충족 |
| 계정 간 접근 분리 통과 | 충족(파일 시스템 권한 범위) |
| SmartScreen 실제 동작 확인 | 충족(사용자 보고, 보조 증거 미수신) |
| 후보 무결성 검증 | 충족 |

## 5. 남은 Known Issues와 미해결 위험

- **K-01 미서명(수용):** SmartScreen 경고 가능. 변조 확인 수단은 게시 SHA-256뿐.
- **K-02 앱 로그 없음:** 오류 시 사용자·지원이 확인할 기록이 없다.
- **K-06 데이터가 Roaming `%APPDATA%`:** 도메인 로밍 프로필에서 SQLite·DPAPI 키가 동기화될 수 있다.
- **K-10/K-11 미검증:** 실데이터 DB 업그레이드, 스키마 변경 업그레이드, 자원 사용량, 로그 쓰기 실패, 업데이트 중단, DB 손상.
- K-07·K-08·K-09: 경미(환경 가드 미적용, 서비스→API 역의존, venv 위치).
- K-03·K-04·K-05는 해결됨(K-03은 "검증 대기" 표기 유지, 패키지 정리 후 설치본 VM 시험은 이번 후보로 수행됨).
- **설치본의 앱 기능 미확인:** 디버그 포트 차단으로 렌더러를 조사할 수 없어 설치본에서 에이전트 헬스 `ready`와 화면 동작을 확인하지 못했다(에이전트 프로세스 생존까지만 확인).
- 이 후보는 개발 PC 빌드이며 CI 기준 빌드가 아니다. 에이전트를 새로 빌드하지 않았다. 빌드는 바이트 재현되지 않는다.
- WMI가 거부되는 환경의 제거 정리 대체 경로는 단위 시험만. Windows 10, Smart App Control, 업그레이드·실행 중 제거는 시험하지 않았다.
- `tools/vm/test_release_vm.ps1`은 차단된 빌드에서 동작하지 않는다.

## 6. 배포 가능한 파일 (조건 충족 전에는 배포 승인 없음)

수동 시험 3건이 통과하면 아래 파일이 배포 대상이다. 지금은 **배포 승인 대상이 아니다.** 위치: `build-output/release-candidates/0.7.0/`.

| 파일 | 크기(bytes) | SHA-256 |
|---|---:|---|
| `GitVizWorkspaceSetup.exe` | 180,141,568 | `18b3cc921e91b113fc71a887283d799b9103c5493e5934729985161ef39ff018` |
| `GitVizWorkspace-0.7.0-full.nupkg` | 179,461,477 | `69ff10cebaa417b6cae276995d079fe725bdf7eb9401e0175e44acfe3731c8c1` |
| `RELEASES` | 86 | `373cc461a86f31e100b6fea7744e3bdc48d90895b8b75627ddb84f54f3b80f99` |

이전 5차 후보(`0.7.0-5th-backup/`)는 배포 대상이 아니다(디버그 포트 차단·무인 제거 수정 미포함).

## 7. 배포 승인 권고

**지금은 승인하지 않는다.** `RELEASE READY WITH KNOWN ISSUES`로 올리려면 다음이 필요하다.

1. ~~C1 폴더 등록 UI~~ — 2026-10-31 확인됨(PASS, 발견 사항 F1~F3은 개선 후보).
2. ~~C2 계정 간 접근 분리~~ — 2026-10-31 확인됨(PASS, 파일 시스템 권한 범위).
3. ~~C3 SmartScreen~~ — 2026-10-31 사용자 보고로 관찰됨. 남은 것은 보조 증거(해시, 설치 완료, 스크린샷, 스마트 앱 컨트롤 상태)뿐이다.

권장: 3번 결과를 [수동 검증 결과](MANUAL_TEST_RESULTS_0.7.0.md)에 반영한 뒤 이 문서를 갱신해 재판정한다. 설치본의 에이전트 헬스는 화면 하단 `로컬 워크스페이스 기반 준비되었습니다`로 확인했다(수동 검증 §1). GitHub Releases 생성·공개 업로드·태그 푸시는 하지 않았고, 사용자 승인 없이 하지 않는다. 기존 배포 파일은 삭제하지 않았다.
