# PostgreSQL 통합 검증 (2026-10-03)

## 결과

- `scripts/test_backend_improvements.py`: 14개 통과.
- `scripts/test_metric_policy.py`: 12개 통과.
- PostgreSQL 18, `127.0.0.1:55432`, 신규 전용 DB `gitviz_test_policy_20261003` 사용.
- 빈 DB에서 Alembic head `f3a4b5c6d7e8`까지 설치 성공. 기존 스키마 업그레이드 및 데이터 보존 검사도 통과.
- 운영 `.env` DB와 기존 실연동 DB는 사용하지 않았다.

## 새 지표 경로 보강

실제 PostgreSQL에 feature/refactor/bugfix 커밋과 파일 변경 행을 삽입하고 SHA·기간 범위를 적용했다. refactor 이전 변경의 유지, bugfix 집계, 분석 범위의 대규모 변경 플래그 재판정, 미식별 기여의 null, PR 없는 경우의 fallback을 검사했다.

동일 테스트에 `build_snapshot` 호출을 추가했다. 새 계산 버전과 payload 해시, JSONB 재로드 결과를 확인하고 FastAPI HTTP 조회 결과가 저장된 파일 지표와 일치하는지 검사했다. HTTP 조회 중 계산 함수를 호출하면 실패하도록 하여 조회가 재계산하지 않는 것도 확인했다.

기존 검사는 작업 상태·잠금·실패 복구, job/스냅샷 완료 저장, 비교/이력 HTTP 응답, 접근 권한, 불변 스냅샷, 앱 import의 DDL 부재, async DB 연결을 포함한다.

## 실행 환경

프로젝트 `venv`의 Python 경로가 끊어져 번들 Python 3.12.14에 기존 `venv/Lib/site-packages`와 `.validation/packages`를 연결했다. 첫 실행은 cp932 출력에서 워커 로그의 이모지를 인코딩하지 못해 2개가 실패했다. `PYTHONIOENCODING=utf-8` 설정 후 모두 통과했다. 수집 코드나 시각화 코드는 수정하지 않았다.

정상 Python 환경에서는 다음과 같이 실행한다. 테스트는 `gitviz_test`로 시작하는 DB 이름만 허용한다.

```powershell
$env:PYTHONIOENCODING = 'utf-8'
$env:GITVIZ_TEST_DATABASE_URL = 'postgresql://gitviz_test@127.0.0.1:55432/gitviz_test_policy_20261003'
python scripts/test_backend_improvements.py
python scripts/test_metric_policy.py
```

## 검증의 범위

GitHub 권한 응답과 수집 함수는 통합 테스트에서 대체한다. 실제 GitHub 수집 완전성, 브라우저 화면 연결, 순위 정확도, 대규모 성능을 검증한 결과는 아니다. CI의 PostgreSQL 16 실행 결과는 이번 로컬 PostgreSQL 18 결과와 별개다.
