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
| 서명 인증서 | 없음. 확보 후 종류·보관 방식 결정 |
| publisher | 인증서 subject 확정 후 설정 |
| 배포 위치 | GitHub Releases로 가정. 실제 `<owner>/<repo>` 미설정 |
| maker-squirrel | 의존성 설치 별도 승인 필요 |

## 수동 검증

설치 파일을 실행하지 않고 매니페스트와 대조한다.

```powershell
pwsh -NoProfile -File desktop/tests/install-update.ps1 -InstallerPath <설치파일> -ManifestPath <manifest.json> [-ExpectedPublisher '<인증서 subject>']
```

종료 코드: 0 검증됨, 1 크기·해시·서명 불일치, 2 서명 미확인(publisher 미지정).
