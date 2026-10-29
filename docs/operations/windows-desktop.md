# Windows 데스크톱 앱 설치·업데이트

기존 웹 서버 실행([설치·프로세스 안내](setup.md))은 그대로 유지한다. 데스크톱 앱은 서버를 대체하지 않으며 같은 서버에 접속하는 별도 클라이언트다.

## 현재 상태 (2026-10-29)

- 대상은 Windows 11 x64, 사용자별 설치(`%LocalAppData%`, 관리자 권한 불필요)다. 업데이트는 서명 확인 후 **수동**이며 자동 다운로드·설치·삭제는 하지 않는다. Squirrel의 자동 업데이트(`Update.exe`)는 사용하지 않는다.
- 공개 신뢰 서명 인증서와 publisher는 없다. 배포용 설치 파일은 아직 제공하지 않는다.
- maker-squirrel `8.0.1`을 설치했고(`npm run make`), 서명 없는 설치기와 자체 서명 개발 설치기를 로컬에서 빌드했다.
- 서명 없는 설치기의 설치·실행·0.7.0→0.7.1 업그레이드·제거는 clean VM(표준 사용자)에서 확인했다(`scripts/test_installer_vm.ps1`, [기록](../migration/validation/p10-partial-2026-10-28.md)). 개발 PC에는 설치하지 않았다.
- 알려진 한계: 제거 뒤 `%LocalAppData%\GitVizWorkspace`에 `Update.exe` 등 파일 4~5개가 남는다. 사용자 데이터(`%APPDATA%\gitviz-workspace`)는 제거 후에도 보존된다. Windows Smart App Control이 강제 모드이면 서명 없는 설치기가 차단될 수 있다(미시험).

## 설치기 빌드

```powershell
npm --prefix desktop run build
npm --prefix desktop run make      # desktop/out/make/squirrel.windows/x64/
```

산출물은 `GitVizWorkspaceSetup.exe`, `GitVizWorkspace-<버전>-full.nupkg`, `RELEASES`다. MSI는 만들지 않는다(사용자별 설치).

- `electron-winstaller`의 설치 스크립트(arch별 7-Zip 복사)는 `npm ci --ignore-scripts` 때문에 실행되지 않는다. 그러면 `make`가 `7z.exe`를 찾지 못해 실패한다. `desktop/node_modules/electron-winstaller/vendor`에서 `7z-x64.exe`·`7z-x64.dll`을 `7z.exe`·`7z.dll`로 복사하면 같은 효과다(`npm ci`로 되돌린다).
- 서명은 환경 변수 `GITVIZ_SIGN_PFX`(pfx 경로)와 `GITVIZ_SIGN_PFX_PASSWORD`가 **둘 다** 있을 때만 적용한다. 값은 저장소·로그에 남기지 않는다.
- 앱은 `--squirrel-install/updated/uninstall/obsolete` 이벤트에 고정 인자로 `Update.exe`를 호출해 바로가기를 만들거나 지우고 종료한다(`desktop/src/squirrel.ts`).

## 앱 연결 상태

렌더러에는 읽기 전용 `desktop.update.status()`만 노출된다. 릴리스 위치가 설정되기 전에는 항상 `unconfigured`를 반환하며 네트워크 조회·다운로드·설치를 하지 않는다. `.github/workflows/windows-release.yml`은 수동 실행 전용 초안으로, 서명 없는 설치기·nupkg·RELEASES·패키지 zip·`manifest.json`·SHA-256을 워크플로 artifact(보관 7일)로만 올리고 릴리스를 게시하지 않는다. 릴리스 게시와 `manifest.json`의 URL 유효화는 수동 작업이다.

## 업데이트 검증 규칙

`desktop/src/update.ts`가 구현한다. 네트워크 조회·다운로드·실행 경로는 없다.

1. 매니페스트(`version`, `assetName`, `sha256`, `size`, `url`)는 고정 키만 허용한다. 다른 키는 거부한다.
2. `url`은 설정된 GitHub Releases 접두사 + `v<version>/<assetName>`과 정확히 같아야 한다.
3. 현재 버전보다 높은 버전만 `available`이다.
4. 설치 전에 크기 → SHA-256 → publisher 서명 순서로 확인한다. publisher가 비어 있으면 `unsigned-not-trusted`이며 신뢰하지 않는다.

## 미결 설정

| 항목 | 상태 |
| --- | --- |
| 서명 인증서 | 공개 신뢰 인증서 없음. 서명 없는 배포가 기본, 자체 서명은 개발 시험용 |
| publisher | 공개 신뢰 인증서를 확보할 때까지 비움 |
| 배포 위치 | GitHub Releases로 가정. 실제 `<owner>/<repo>` 미설정 |
| maker-squirrel | 8.0.1 설치 완료(사용자 승인). 의존성 32개 추가, 모두 dev·optional |
| 설치 실행 검증 | clean VM에서 서명 없는 설치기 통과. 서명된 설치기·창 표시·Smart App Control 강제 모드는 미시험 |

## 배포 방침 (졸업작품·소규모 팀)

- 공개 신뢰 서명(Azure Artifact Signing 등)은 지역·신원 요건 때문에 한국 개인·학생 팀에는 적용하기 어렵다고 판단했다. 요건은 가입 전에 Microsoft 공식 문서로 다시 확인한다.
- 기본은 **서명 없는 배포**다. 설치 파일은 매니페스트의 크기와 SHA-256으로만 검증하며 `unsigned-not-trusted`로 취급한다. Windows SmartScreen 경고가 표시될 수 있다.
- 서명 검증 경로는 자체 서명 인증서로 개발 시험한다. 자체 서명 인증서는 공개 신뢰가 아니므로 배포용 신뢰 근거로 쓰지 않는다.

## 수동 검증

설치 파일을 실행하지 않고 매니페스트와 대조한다.

```powershell
pwsh -NoProfile -File desktop/tests/install-update.ps1 -InstallerPath <설치파일> -ManifestPath <manifest.json> [-ExpectedPublisher '<인증서 subject>' | -PinnedThumbprint '<자체 서명 지문>']
```

- `-ExpectedPublisher`: 공개 신뢰 서명(`Valid`)과 subject 일치를 요구한다.
- `-PinnedThumbprint`: 자체 서명 개발 인증서용이다. 루트가 신뢰되지 않아도 서명자 지문이 일치하면 통과한다. 둘을 함께 쓸 수 없다.
- 종료 코드: 0 검증됨, 1 크기·해시·서명 불일치, 2 서명 미확인(서명 옵션 미지정).

## 자체 서명 종단 시험

```powershell
pwsh -NoProfile -File desktop/tests/self-signed-update.ps1
```

`Cert:\CurrentUser\My`에 7일짜리 시험 인증서를 만들어 임시 복사본에 서명하고 정상·지문 불일치·변조·루트 미신뢰·무서명 5경우를 확인한 뒤 인증서와 임시 파일을 삭제한다. 신뢰 저장소는 변경하지 않는다. 개발 전용이다.
