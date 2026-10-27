# 분석 지표·스냅샷 계약 안내

검토일: 2026-10-23 · 코드 기준: `a8351f0` · [문서 안내](../index.md)

## 확인한 기준

산식·분모·해석 제한은 [지표 정의](metrics.md), 저장·접근·backfill은 [스냅샷 안내](../architecture/repository-access-and-snapshots.md)에서 관리한다. 보존 원문은 [Historical 보관소](../archive/index.md)에 있으며 현재 운영 절차로 적용하지 않는다.

| 계약 | 확인한 내용 | 코드 |
|---|---|---|
| 계산 버전 | `gitviz-2026.10-priority-v3` | [지표 정책](../../app/services/metric_policy.py) |
| 기본 기간 | `[T-180일,T)`. 명시한 구간은 별도 적용 | [스냅샷 파라미터](../../app/services/analysis_snapshots.py) |
| 점검 우선순위 | change_freq·churn·bugfix_count 백분위 평균. 결측을 0으로 보정하지 않음 | [analytics](../../app/api/analytics.py) |
| BF80 | 식별된 변경 기여의 누적 80% 최소 인원. 미식별 분모 별도 표시 | [analytics](../../app/api/analytics.py) |
| 큰 변경 | `changed_file_count >= max(15, ceil(0.1*N))`. N은 목표 트리 대용 | [정책](../../app/services/metric_policy.py), [snapshot](../../app/services/analysis_snapshots.py) |
| 공동 변경 | 대칭 결합도와 양방향 동반 비율을 구분. 전체 후보 정렬 후 limit 적용 여부 확인 | [analytics](../../app/api/analytics.py) |
| 조회 | 저장 결과를 선택·필터링. 스냅샷 무결성 확인 | [snapshot route](../../app/api/snapshot_route.py) |
| 비교 | 동일 저장소·계산 버전, 정확한 path 대응. rename은 추가·제외 | [비교 서비스](../../app/services/snapshot_comparison.py) |

`GET /repositories/{id}/snapshots`는 `limit`·`before_id`를, `/snapshots/compare`는 `before`·`after`·`offset`·`limit`을 사용한다. 권한·스냅샷 불일치 오류는 [API 코드](../../app/api/snapshots.py)를 기준으로 확인한다.

## 주의할 의미 차이

- 작업 `done`과 수집 완전성은 별도다. `pr_file_coverage`는 상세가 한 행 이상 있는 PR 비율이며 모든 페이지 완전성이 아니다.
- BF 목록의 `observation_count`와 tree의 같은 이름 필드는 분모가 다를 수 있다. [METRICS](metrics.md)의 식별·전체 변경 수 구분을 따른다.
- co-edit는 스냅샷 범위의 PR에 연결된 원본 커밋을 raw 테이블에서 참조하는 별도 경로가 있다. 다른 지표의 기간·SHA 범위를 확장한 것으로 해석하지 않는다.
- 구형 스냅샷의 refactor 이후 계산 설명은 그 버전에 대한 설명일 수 있다. 문구가 존재한다는 이유만으로 현재 정책 오류라고 판정하지 않는다.

추정: 산식을 중복 작성하는 대신 지표 정의를 영역 기준으로 유지하고 변경 시 코드·설명·테스트·계산 버전을 함께 검토한다.

미확정: 지표의 미래 결함 예측·순위 타당성은 [실험 계획](../product/analysis-validation-plan.md)이 남아 있다. 현재 산식 일치 검사를 정확도 실험으로 대체하지 않는다.
