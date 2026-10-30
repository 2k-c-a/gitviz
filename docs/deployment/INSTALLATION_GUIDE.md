# 설치 가이드 (Setup.exe)

작성일 2026-10-30 · 기준 커밋 `65f1b62` · 상태: 설계 문서 (코드 변경 없음)
표기: **[현재]** 검증된 동작, **[제안]** 설계안, **[결정 필요]** 사용자 결정 사항.

관련 문서: [환경 아키텍처](../architecture/ENVIRONMENT_ARCHITECTURE.md) · [빌드와 릴리스](BUILD_AND_RELEASE.md) · [데이터 저장 정책](DATA_STORAGE_POLICY.md)

## 1. 설치 방식 요약

| 항목 | [현재] | 비고 |
|---|---|---|
| 도구 | electron-forge maker-squirrel 8.0.1 (Squirrel.Windows) | MSI 없음 (`noMsi`) |
| 설치 범위 | 사용자 단위 (per-user) | 관리자 권한 불필요, 표준 사용자로 설치 가능 |
| 설치 경로 | `%LOCALAPPDATA%\GitVizWorkspace\app-<버전>\` | `Update.exe`가 루트에 위치 |
| 바로가기 | 시작 메뉴 `GitViz\GitVizWorkspace.lnk`, 바탕화면 `GitVizWorkspace.lnk` | |
| 제거 항목 | `HKCU\...\Uninstall\GitVizWorkspace` | 설정 > 앱에서 제거 가능 |
| 서명 | 기본 미서명. `GITVIZ_SIGN_PFX`와 `GITVIZ_SIGN_PFX_PASSWORD`가 있을 때만 서명 | 서명 범위는 미검증 |
| 사용자 데이터 | `%APPDATA%\gitviz-workspace` (Roaming) | 설치 폴더 밖 |

근거: 클린 VM 검증 결과(`.validation\p10-vm\installer-0.7.0-cleanup-interactive-Run-result.json`). 설치 종료 코드 0, 제거 종료 코드 0, 제거 후 잔여 파일 0, 바로가기와 제거 항목 삭제, 사용자 데이터 표식 파일은 유지됨.

## 2. `%ProgramFiles%` 설치 평가

| 기준 | per-user (현재) | `%ProgramFiles%` (MSI/NSIS 등) |
|---|---|---|
| 권한 | 불필요 | 설치 시 UAC 필요 |
| 자동 업데이트 | 앱 폴더 쓰기 가능, Squirrel이 처리 | 업데이트마다 권한 상승 필요 또는 별도 서비스 |
| 다중 사용자 공유 | 사용자마다 설치 | 한 번 설치로 공유 |
| 변경 비용 | 없음 | 패키징 도구 교체, 업데이트 경로 재설계, 제거 정리 재검증 |

**[제안]** per-user 설치를 유지한다. 표준 사용자 권한으로 동작해야 한다는 요구와 자동 업데이트 요구에 부합한다.
**[결정 필요]** 조직이 모든 사용자 공용 설치를 요구하면 MSI 변형을 별도 산출물로 추가한다 (현재 범위 밖).

## 3. 설치 흐름

1. 사용자가 `GitVizWorkspaceSetup.exe`를 실행한다. 서명이 없으면 SmartScreen 경고가 나올 수 있다 (MOTW 동작 미검증).
2. Squirrel이 `%LOCALAPPDATA%\GitVizWorkspace\`에 패키지를 풀고 `--squirrel-install` 훅으로 앱을 호출한다.
3. 바로가기와 제거 항목이 만들어지고 앱이 시작된다.
4. 앱 첫 실행 시 에이전트가 사용자 데이터 폴더에 DB, 키(DPAPI), `secret-check.bin`을 만든다.

설치 중 서버 비밀값은 만들거나 묻지 않는다. 클라이언트에는 서버 주소 같은 공개 설정만 포함한다 ([환경 아키텍처 §3.4](../architecture/ENVIRONMENT_ARCHITECTURE.md)).

## 4. 업데이트 정책

- **[현재]** Squirrel `Update.exe`가 새 `app-<버전>` 폴더를 만들고 이전 폴더를 유지한다. 앱에는 매니페스트 기반 업데이트 검사 로직(`desktop/src/update.ts`: 버전 비교, 크기·SHA-256 검증, 서명 검증기 주입)이 있다. 실제 호출 경로와 Squirrel 적용 연결은 이 단계에서 검증하지 않았다. 배포는 GitHub Releases 업로드 방식으로 승인되었고 실제 업로드는 별도 승인 대상이다.
- **[현재]** 사용자 데이터는 설치 폴더 밖이므로 업그레이드가 건드리지 않는다. DB 스키마는 `SCHEMA_VERSION=8`이며 업그레이드 시에만 `pre-v8-{uuid}.sqlite3` 백업을 만든다.
- 더 새로운 스키마의 DB를 구버전 앱이 열면 `UNSUPPORTED_PROTOCOL`로 거부한다 (다운그레이드 보호).
- **[미검증]** 0.7.0 → 0.7.1 실제 업그레이드 시나리오는 아직 VM에서 실행하지 않았다. 릴리스 전에 검증한다.
- **[제안]** 업데이트 서버 주소는 빌드 시점 채널 설정으로 고정하고 런타임에 사용자 입력으로 바꾸지 못하게 한다.

## 5. 제거 정책

- 제거는 Squirrel 제거 훅이 일으킨다. Update.exe가 훅 프로세스의 모든 자손을 기다리므로 앱이 직접 띄운 도우미는 교착된다. 그래서 정리는 일회성 예약 작업(`GitVizWorkspaceUninstallCleanup`)이 한다 (`desktop/src/squirrel.ts`).
- 정리 스크립트는 부모(Update.exe) 종료를 기다리고 설치 폴더에서 실행 중인 프로세스가 없을 때까지 대기한 뒤 `Remove-Item`을 최대 10회 시도하고, 자기 자신과 예약 작업을 지운다. 로그는 `CLEANUP_LOG`에 남는다.
- **[현재 한계]** 로그온 세션이 없는 무인 제거에서는 예약 작업이 실행되지 않아 `.dead` 폴더와 `Update.exe`가 남을 수 있다. 대화형 세션에서는 두 번 검증되었다. WMI 대체 경로는 미구현이다.
- 제거 정리는 `%LOCALAPPDATA%\GitVizWorkspace` 루트를 통째로 삭제한다. 따라서 데이터는 이 루트 아래에 두면 안 된다 ([데이터 저장 정책](DATA_STORAGE_POLICY.md)).
- 사용자 데이터는 기본적으로 제거하지 않는다. **[결정 필요]** 제거 시 데이터 삭제 선택 UI를 둘지 여부.

## 6. 로그와 진단

| 로그 | 위치 | 상태 |
|---|---|---|
| Squirrel 설치 로그 | `%LOCALAPPDATA%\SquirrelTemp\SquirrelSetup.log` | 호스트에서 파일 존재 확인. 항목 내용은 미검토 |
| 제거 정리 로그 | `desktop/src/squirrel.ts`의 `CLEANUP_LOG` 경로 | 코드 상수, VM에서 기록 확인 |
| 앱 로그 | [제안] `%LOCALAPPDATA%\GitViz\Workspace\logs` | 현재 위치는 데이터 정책 문서 참조 |

로그에 키, 토큰, 환경 변수 값을 남기지 않는다.

## 7. 오류 복구

| 증상 | 원인 | 조치 |
|---|---|---|
| 앱이 `RECOVERY_REQUIRED` | DPAPI 키와 DB 불일치 (다른 사용자 프로필 복사 등) | 원본 키와 DB를 짝으로 복원하거나 새 DB로 초기화 (사전 백업) |
| 앱이 `UNSUPPORTED_PROTOCOL` | DB 스키마가 앱보다 새로움 | 앱을 최신 버전으로 업데이트. DB를 수동 수정하지 않음 |
| 설치 중단 | 설치 폴더 일부만 생성 | `Setup.exe` 재실행. 안 되면 설치 루트와 SquirrelTemp 로그 확인 후 수동 삭제 |
| 제거 후 폴더 잔존 | 무인 제거 또는 프로세스 잠금 | 로그오프/로그온 후 예약 작업 실행 여부 확인, 수동 삭제 |
| 실행 직후 종료 | 에이전트 프레임 오류, 단일 실행 잠금(`writer.lock`) | 다른 인스턴스 종료 후 재실행. 로그 확인 |

## 8. 보안과 권한

- 표준 사용자 권한만 사용한다. 관리자 권한을 요구하는 단계를 추가하지 않는다.
- 설치 폴더에 사용자 데이터나 비밀값을 쓰지 않는다.
- 렌더러는 샌드박스, `devTools:false`이다. **[주의]** 패키징된 exe가 현재 `--remote-debugging-port` 인수를 받아들인다. 릴리스 빌드에서 차단하는 것을 권장한다 (변경 후보로 등록).
- 서명되지 않은 설치 파일은 무결성 검증 수단으로 SHA-256을 릴리스 노트에 함께 게시한다.

## 9. 검증 체크리스트 (릴리스 전)

- [ ] 클린 VM 체크포인트 `pre-p10-installer`로 복원
- [ ] 설치 → 실행 → 데이터 생성 확인
- [ ] 이전 버전 위에 설치(업그레이드) 후 데이터 유지 확인
- [ ] 제거 후 설치 루트 잔여 0, 바로가기/제거 항목 삭제, 사용자 데이터 유지
- [ ] 서명 여부와 SmartScreen/Smart App Control 동작 기록
