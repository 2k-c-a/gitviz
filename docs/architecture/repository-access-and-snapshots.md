# 저장소 접근·불변 스냅샷·backfill

검토일: 2026-10-23 · 코드 기준: `a8351f0` · [문서 안내](../index.md)

## 확인한 접근·저장 계약

- 수집 경로는 `storage/repos/<repository_id>`다. 기존 clone origin이 다르면 갱신하지 않는다. Git 인증은 자식 프로세스 환경으로 전달하고 저장소 URL·설정에 토큰을 저장하지 않는다.
- HTTPS GitHub owner/repository URL과 숫자 GitHub ID를 확인한다. 이름이 같다는 이유로 과거 저장소와 새 저장소 결과를 합치지 않는다. 숫자 ID 없는 legacy 행은 별도 확인이 필요하다.
- 분석 요청은 로그인·현재 저장소 접근을 요구한다. private는 요청자의 개인 token이 필요하며 공용 token이나 과거 접근 기록만으로 허용하지 않는다.
- GitHub가 현재 인증·상태를 확인한 304일 때만 ETag 응답을 재사용한다. 접근 확인 장애를 권한 있음으로 추정하지 않는다.
- 진행 중 작업과 새 요청이 충돌하면 409다. 만료 후보 교체는 worker와 같은 저장소 advisory lock을 얻는 경우에 한정한다.
- 결과는 SHA·기간·계산 버전·파라미터·실행 시각·정규화 JSON·SHA-256을 저장한다. DB trigger가 기존 snapshot UPDATE/DELETE를 제한하고 조회는 해시를 확인한다.
- frontend는 repo/snapshot을 고정해 다시 연다. 코드 링크도 branch 대신 SHA를 사용한다. 같은 저장소·계산 버전의 정확한 path만 비교하며 rename은 추가·제외다.

근거: [접근 서비스](../../app/services/repository_access.py), [clone 관리](../../app/collectors/git/clone_manager.py), [작업 수명주기](../../app/services/job_lifecycle.py), [snapshot 서비스](../../app/services/analysis_snapshots.py), [비교](../../app/services/snapshot_comparison.py).

## 관측 기간

`POST /repositories`와 `POST /repositories/{id}/reanalyze`는 `analysis_start`·`analysis_end`를 받는다. 시간대가 있는 ISO 시각, 시작 포함·종료 제외다. URL의 `+`는 `%2B`로 인코딩한다.

```text
analysis_start=2026-01-01T00:00:00+09:00
analysis_end=2026-07-01T00:00:00+09:00
```

기본 관측 기간은 `[T-180일,T)`이다. 원본 snapshot 문서의 ‘시작 제한 없음’은 현재 기본 정책과 다르다. [지표 정의](../contracts/metrics.md)와 [파라미터 코드](../../app/services/analysis_snapshots.py)를 따른다. 수집 raw 이력과 분석 구간은 다르고, 과거 신원 매핑·force push 전 그래프의 완전한 복원을 보장하지 않는다.

작업 done, PR 상세 존재율, PR 전체 페이지 완전성, 분석 근거 충분함은 별도 상태다. 스냅샷을 재조회할 수 있다는 사실이 미래 예측 타당성이나 당시 자료의 완전성을 증명하지 않는다.

## 기존 데이터 backfill

아래는 운영자가 대상 DB·clone·백업을 확인한 뒤 수행하는 절차이며 이번 정리에서 실행하지 않았다.

```powershell
python -m alembic upgrade head
python tools/ops/backfill_analysis_snapshots.py
python tools/ops/backfill_analysis_snapshots.py --apply
```

기본 실행은 [스크립트의 옵션·보고](../../tools/ops/backfill_analysis_snapshots.py)를 확인한다. `--apply`는 DB에 쓴다. 이름 기반 기존 clone은 origin을 검증하고 주소가 다르면 `verified_clone_required`로 남긴다. 임의 clone을 다른 저장소에 대응시키지 않는다.

- `--offline-existing-data`: 외부 GitHub 확인 없이 기존 DB·clone으로 처리하는 유지보수 옵션이다. 공개 여부·사용자 권한을 갱신하지 않으며 HTTP의 현재 접근 검사는 유지한다.
- `--repair-hashes`: 기존 결과를 덮어쓰는 대신 새 해시 형식의 snapshot을 추가한다.

## 검사와 증거

```powershell
python scripts/test_repository_snapshots.py
python scripts/verify_snapshot_integration.py --auth-fixture
```

후자의 운영 DB 읽기 검사는 GitHub 접근을 대역으로 분리한다. 실제 GitHub 인증 통과와 구분한다. 일반 모드는 실제 접근을 확인한다. 필요한 DB·clone·서버 조건을 준비하고 [검증 상태](../validation/current-status.md)에 별도 실행으로 기록한다. 새 결과는 `reports/validation/`에 실행별 파일로 저장한다.

[보존 원문](../archive/originals/SNAPSHOT_AND_ACCESS.md)과 [과거 증거](../validation/evidence-index.md)의 2026-10-02 적용·통과 기록은 Historical이다. 읽기 전용 검증 서버는 OAuth를 차단하므로 실제 로그인용 개발 서버와 구분한다.

추정: 유지보수 전후 비교는 동일 입력·버전·수집 품질을 명시해야 한다. 미확정: 운영 DB revision·현재 snapshot·실제 권한·성능은 이번 문서 재배치로 검증되지 않았다.
