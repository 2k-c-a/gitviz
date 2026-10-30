# 데이터 저장 정책

작성일 2026-10-30 · 기준 커밋 `65f1b62` · 상태: 설계 문서 (코드 변경 없음)
표기: **[현재]** 검증된 동작, **[제안]** 설계안, **[결정 필요]** 사용자 결정 사항.

관련 문서: [환경 아키텍처](../architecture/ENVIRONMENT_ARCHITECTURE.md) · [설치 가이드](INSTALLATION_GUIDE.md) · [빌드와 릴리스](BUILD_AND_RELEASE.md)

## 1. 원칙

1. 사용자 데이터를 설치 폴더에 두지 않는다.
2. 제거 정리가 지우는 루트(`%LOCALAPPDATA%\GitVizWorkspace`) 아래에 데이터를 두지 않는다.
3. 표준 사용자 권한으로 쓸 수 있는 경로만 사용한다.
4. 키와 DB는 짝으로 취급한다 (키 불일치 시 `RECOVERY_REQUIRED`).
5. 비밀값은 로그, 백업 이름, 설정 파일에 평문으로 남기지 않는다.
6. 환경별(dev/test/prod) 경로는 서로 겹치지 않는다.

## 2. [현재] 저장 위치

호스트 개발 PC에서 확인한 사용자 데이터 폴더: `%APPDATA%\gitviz-workspace` (Roaming). 에이전트 파일(DB·키·검증·창 상태·잠금)은 그 아래 `workspace\` 하위에 있고(4차 S0 실측), 상위에는 Chromium 생성물이 있다.

| 데이터 | 현재 파일 | 비고 |
|---|---|---|
| 사용자 DB | `workspace.sqlite3` (+ wal/shm) | SQLite, 스키마 v8 |
| 암호 키 | `key.dpapi` | DPAPI 보호, 같은 Windows 사용자만 복호화 |
| 키 검증 | `secret-check.bin` | 키-DB 짝 확인 |
| 실행 잠금 | `writer.lock` | 단일 쓰기 인스턴스 |
| 창 상태 | `window.json` | |
| 브라우저 캐시류 | `Cache`, `Code Cache`, `GPUCache`, `Local Storage` 등 | Chromium이 만든 파일 |
| 업그레이드 백업 | `pre-v8-{uuid}.sqlite3` | 업그레이드 시에만 생성 |

문제점: Roaming은 도메인 환경에서 프로필 동기화 대상이 될 수 있고, 캐시와 DB와 키가 한 폴더에 섞여 있다. DPAPI 키는 기기/사용자에 묶이므로 Roaming 동기화와 맞지 않는다.

## 3. [제안] 데이터 종류별 위치

기준 루트: `%LOCALAPPDATA%\GitViz\Workspace\` (설치 루트 `GitVizWorkspace`와 구분되는 별도 형제 폴더).

| 종류 | 위치 | 보존 | 비고 |
|---|---|---|---|
| DB | `...\data\workspace.sqlite3` | 영구 | WAL 사용 |
| 키/검증 | `...\data\key.dpapi`, `secret-check.bin` | 영구 | DB와 같은 폴더 유지 |
| 설정 | `%APPDATA%\GitViz\Workspace\settings.json` | 영구 | 이동해도 되는 비비밀 설정만 Roaming |
| 로그 | `...\logs\` | 회전, 예: 최근 14일/총 50 MB | 비밀값 금지 |
| 캐시 | `...\cache\` | 삭제 가능 | Chromium 캐시 포함 (`app.setPath`로 이동) |
| 임시 | `%TEMP%\GitViz\` | 실행 종료 시 정리 | |
| 백업 | `...\backups\` | 최근 N개 (예: 5개) | 업그레이드 전 자동 백업 |
| 사용자 생성 파일(내보내기 등) | 사용자가 고른 경로, 기본 `Documents\GitViz` | 영구 | 앱은 선택 위치만 기억 |

**[결정 필요]** 설정을 Roaming에 둘지, 전부 Local로 통일할지. 비밀·키가 없는 설정만 Roaming이라는 안을 권장한다.

## 4. 환경별 경로

| 환경 | 사용자 데이터 | DB |
|---|---|---|
| Development | [제안] `GITVIZ_USER_DATA_DIR`로 저장소 밖 임시/개발 폴더 지정 | 개발용 SQLite, 서버는 개발 PostgreSQL |
| Test | 실행마다 생성하는 임시 폴더, 종료 시 삭제 | `gitviz_test*` DB, SQLite는 임시 파일 |
| Production | §3 경로 | 사용자별 SQLite |

운영 경로는 `GITVIZ_ENV=production`에서만 기본값으로 쓰고, 개발/테스트가 운영 폴더를 가리키면 시작을 거부한다 ([환경 아키텍처 §3.3](../architecture/ENVIRONMENT_ARCHITECTURE.md)).

## 5. 이전(Roaming → Local) 절차 [제안]

기존 사용자가 이미 `%APPDATA%\gitviz-workspace`를 쓰고 있으므로 파괴적 이동을 피한다.

1. 새 위치가 없고 옛 위치에 `workspace.sqlite3`가 있으면 이전 대상으로 판단한다.
2. 앱 종료 상태(`writer.lock` 확보)에서 시작한다.
3. DB, WAL/SHM 체크포인트 후 DB, `key.dpapi`, `secret-check.bin`을 새 위치로 **복사**한다 (이동 금지).
4. 새 위치에서 스키마 버전, 키-DB 짝(`secret-check.bin`), `PRAGMA integrity_check`를 검증한다.
5. 검증 통과 시 새 위치를 사용하고, 옛 위치는 `migrated.marker`만 남기고 보존한다.
6. 실패하면 새 위치의 부분 복사본을 지우고 옛 위치로 계속 동작한다.
7. 옛 데이터는 N회 정상 실행 후 사용자 확인을 거쳐 정리한다 (자동 삭제 금지).

실제 도입 전 클린 VM과 기존 데이터가 있는 VM 양쪽에서 검증한다.

## 6. 백업과 복구

- **[현재]** 스키마 업그레이드 직전에만 `pre-v8-{uuid}.sqlite3`를 만든다.
- **[제안]** 위험 작업(마이그레이션, 키 재생성) 전 백업, 보존 개수 제한, 백업과 같은 시점의 키 파일 짝 보관 여부는 **[결정 필요]**. 키를 백업 폴더에 함께 두면 보호 수준이 낮아지므로 DPAPI 특성(같은 사용자만 복호화)을 문서화한다.
- 복구는 앱 종료 후 DB와 키를 같은 시점의 짝으로 되돌리는 방식이다. 키 없이 DB만 복원하면 `RECOVERY_REQUIRED`가 된다.

## 7. 서버 측 데이터 (참고)

서버 PostgreSQL은 사용자 클라이언트 데이터와 분리한다. 운영 DB는 Alembic 단일 head(`b7e1d9c3a502`)로만 변경하고, 마이그레이션은 배포 절차에서 사람이 실행한다. 개발/테스트 코드는 `assert_database_allowed`로 운영 DB 접속을 거부한다 ([환경 아키텍처 §3.3](../architecture/ENVIRONMENT_ARCHITECTURE.md)). 운영 비밀값(`SESSION_SECRET_KEY` 등)은 서버 환경에만 두고 클라이언트 빌드에 넣지 않는다.

## 8. 권한과 보안

- 데이터 폴더는 현재 사용자만 접근 가능한 기본 ACL을 유지한다. 별도 ACL 완화 금지.
- DPAPI는 사용자 범위로 사용한다.
- 로그/백업 파일명에 사용자 식별 정보나 비밀값을 넣지 않는다.
- 제거 시 데이터 삭제는 기본 꺼짐. **[결정 필요]** 선택 삭제 UI.

## 9. 검증 방법

| 환경 | 방법 |
|---|---|
| Test | 임시 폴더 지정 후 생성/삭제, 키 불일치와 신규 스키마 거부 케이스 |
| 클린 VM | 설치 → 데이터 생성 → 제거 → 데이터 유지 확인 (`pre-p10-installer` 체크포인트) |
| 이전 | 옛 위치 데이터가 있는 VM에서 §5 절차, 실패 주입 시 옛 데이터 무손상 확인 |
| 업그레이드 | 구버전 설치 위에 신버전, 백업 생성과 데이터 유지 |
