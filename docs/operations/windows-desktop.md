# Windows 데스크톱 앱 설치·업데이트

기존 웹 서버 실행([설치·프로세스 안내](setup.md))은 그대로 유지한다. 데스크톱 앱은 서버를 대체하지 않으며 같은 서버에 접속하는 별도 클라이언트다.

## 현재 상태 (2026-10-28)

- 대상은 Windows 11 x64, 사용자별 설치(관리자 권한 불필요)다. 업데이트는 서명 확인 후 **수동**이며 자동 다운로드·설치·삭제는 하지 않는다.
- 서명 인증서와 publisher가 없어 installer 빌드·서명·배포는 하지 않았다. 설치 파일은 아직 제공되지 않는다.
- `forge.config.ts`의 `makers`는 비어 있고 maker-squirrel은 설치하지 않았다.

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
| maker-squirrel | 의존성 설치 별도 승인 필요 |

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
