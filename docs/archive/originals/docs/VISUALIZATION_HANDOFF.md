# 시각화 담당 전달 사항

## 지표 후보 정책 연결 작업 지시 — 2026-10-03

이번 지표 수정에서 `frontend/`는 수정하지 않았다. 아래는 시각화 담당 작업이다.

### 점검 우선순위 화면

- 새 계산 버전은 `gitviz-2026.10-priority-v3`다. `maintenance_priority`를 우선 사용하고 `risk_score`는 기존 버전 호환에만 사용한다. 제목은 유지보수 점검 우선순위로 표시한다.
- 새 점수는 변경 횟수·Churn·버그 수정 횟수의 백분위 평균이다. `priority_components`와 원시 `change_freq/churn/bugfix_count`를 상세에서 함께 보여준다. 구성 요소의 백분위 평균 자체를 상위 퍼센트로 표현하지 않는다.
- CurrentRisk/StructuralRisk 카드와 refactor 이후 초기화 설명을 새 버전에서 제거한다. null은 0으로 치환하지 않는다. 새 모델의 0도 안전을 뜻하지 않는다.
- `evidence_status=metrics_missing`은 Churn 등의 계산 입력 누락이다. 점수 순위에서 뒤로 배치하고 자료 부족으로 표시한다. no_history(관측 변경 없음), low_support(표본 부족)와 구분한다.
- 기본 180일 구간은 요약·타임라인·협업에도 적용된다. 전체 누적 수라고 표시하지 말고 `_snapshot.window_start/window_end`를 기준으로 관측 기간을 표시한다.
- 기존 0.7 배지는 표시 임계값이다. 상위 30%라고 부르지 않는다. 실제 상위 K개 목록이 필요하면 파일 집합을 정렬하고 동률 처리 정책을 별도로 정한다.

### 기여 분포 화면

- BF80, `top_owner_share`, `contributor_count`, `entropy_normalized`, 표본 수를 함께 표시한다. BF80=1은 한 명에게 관측 변경 기여가 집중되었다는 뜻이다.
- `file_bf80_median`을 파일별 BF80 중앙값으로 표시한다. `project_bus_factor`는 호환 필드이며 프로젝트 전체 지식 인원으로 표시하지 않는다.
- BF 목록의 `observation_count`는 식별된 변경 수, `total_change_count`는 전체 변경 수다. tree의 observation_count는 전체 변경 수이므로 같은 의미로 섞지 않는다. `unidentified_share`와 low_support도 표시한다.
- Entropy가 높다는 이유로 위험색을 적용하지 않는다. 기여 1명은 정규화 0, 식별된 기여 없음은 null로 구분한다.

### 변경 결합 화면

- 목록 기본 정렬은 원 결합도·공동 변경 횟수다. `GET /repositories/{id}/analytics/coupling?sort_by=idf`로 실험용 IDF 정렬을 선택할 수 있다. snapshot_id는 유지한다.
- 파일 상세의 `selected_to_other_ratio`는 선택 파일 변경 시 상대 파일이 동반된 과거 비율이다. 역방향 `other_to_selected_ratio`와 `co_change_count`도 표시한다. 미래 영향 확률·실제 의존성이라고 표현하지 않는다.
- 파일 상세 `GET .../files/{id}/detail?limit=&sort_by=coupling|idf`: `sort_by`를 주면 서버가 **전체 후보를 정렬한 뒤** `limit`으로 자르고, 응답에 `sort_by`(에코)와 `coupled_total`(자르기 전 후보 수)을 싣는다. 화면은 이 둘이 있을 때만 "전체 후보 기준 서버 정렬"이라고 표시하고, 없으면 "조회된 후보 안에서 정렬"로 표시한다. `sort_by`를 생략하면 기존 순서(위 `selected_to_other_ratio`)다.
- 파일 상세는 그 파일 자신의 `evidence_status`·`change_freq`·`churn`·`bugfix_count`·`priority_components`를 준다(점수 목록 `limit`과 무관). 점수 목록에 없다는 이유로 `no_history`를 추정하지 않는다. 응답에 `evidence_status`가 없으면(기준 SHA에 없는 파일·이전 형식) 상태를 단정하지 않는다.
- 수집 범위·분석 근거 배지는 확인 가능한 수치만 말한다: `일부만 수집`(누락 확인) / `수집 범위 확인 불가`(수치 없음) / `확인된 범위 누락 없음`, `근거 제한적` / `근거 확인 불가` / `관측 구간 커밋 없음` / `확인된 제한 없음`. "수집 완료"·"근거 충분"은 수집 단계가 완료 정보를 제공하기 전에는 표시하지 않는다.
- `meta.fallback_reason`: no_merged_prs / pr_details_missing / all_collected_prs_filtered를 각각 PR 없음 / 상세 부족 / 전부 필터됨으로 구분한다.
- `meta.pr_file_coverage`는 상세가 하나 이상 있는 PR 비율이다. 전체 수집 완료율로 표현하지 않는다. `excluded_unit_count`도 함께 보여준다.
- Hidden Coupling 확정 개수 표시는 제거한다. AST 비교가 없어서 새 risk summary의 hidden_coupling_count는 null이다.

### 완료 기준

1. 구버전·신버전 스냅샷을 각각 열어 해당 버전의 설명을 표시한다. 서로 다른 버전의 비교 409를 안내한다.
2. 기여자가 미식별인 파일, 변경 없는 파일, 변경 2건 파일을 넣어 null/0/관측 부족을 구분한다.
3. A 변경 100회·B 변경 2회·공동 변경 2회에서 결합도 1, A→B 2%, B→A 100%, 표본 2회를 표시한다.
4. IDF 정렬 선택 후 탭 이동·뒤로 가기에서도 snapshot_id와 선택 정책이 보존된다.
5. 현재 frontend에는 옛 점수 설명이 남아 있으므로 UI 수정 전에는 새 정책이 완전히 반영된 화면이라고 발표하지 않는다.

기준: 2026-10-02, `05a58db` 검토 후 백엔드 개선 작업.
이번 작업에서 `frontend/` 파일은 수정하지 않았다.

## 먼저 수정할 오류

### 상태 조회 오류 처리

`frontend/js/modal.js:163`의 2초 폴링은 비정상 응답을 무시하고 계속 대기한다.

- 401: 폴링 종료와 재로그인 안내.
- 404: 접근 불가/삭제 안내와 종료. 비공개 저장소 존재를 구체적으로 노출하지 않기.
- 429: 응답의 Retry-After가 있으면 사용하고, 없으면 backoff로 재시도. 사용자가 재시도 시점을 알 수 있게 표시.
- 502/503/네트워크 오류: 횟수를 제한해 재시도하고 실패 상태를 표시.
- 이전 요청이 끝나기 전에 다음 요청을 보내지 않기. 대기 중 조회 간격을 늘려 API 부담 줄이기.

완료 기준: 위 HTTP 응답을 각각 주입한 브라우저 테스트에서 무한 대기 없음, 중복 요청 없음, 닫힌 모달의 폴링 없음. 실제 GitHub 권한 확인은 백엔드에서 유지된다.

### 과거 스냅샷 이동

`frontend/js/navbar-loader.js:27`의 `goPage()`가 snapshot 쿼리를 버린다. 같은 저장소의 탭 이동에는 현재 URL의 snapshot을 함께 전달하고, 저장소를 바꿀 때는 이전 snapshot을 가져가지 않는다.

`GET /repositories/{id}/status`에 완료 job의 `snapshot_id`를 추가했다. 새 분석의 결과 보기에는 이 ID를 명시해 sessionStorage의 오래된 결과가 선택되지 않게 한다.

완료 기준: A 스냅샷 열기 → B 열기 → 뒤로 가기 → 코드 분석/네트워크 전환에서 계속 A. 재분석 완료 후에는 정확히 새 job의 결과로 이동. 다른 저장소의 snapshot 혼입 없음.

### 작업 복구 표시

서버가 멈춘 작업을 안전하게 교체하도록 개선했다. `cancelled` 상태를 '교체됨'으로 처리한다. 409는 실행 중인 작업과 충돌했다는 안내이며 일반 네트워크 오류로 표시하지 않는다. `is_stale`은 시간상 복구 후보이고, 실제 워커가 살아 있으면 재시작이 409로 거부될 수 있다.

## 새 기능 연결 계약

### 분석 이력

`GET /repositories/{id}/snapshots?limit=20&before_id=100`

응답: `{ snapshots: [{id,target_sha,analyzed_at,window_start,window_end,formula_version,parameters,payload_sha256}], next_before_id }`.
최신 ID부터 반환하며 next_before_id가 null이면 마지막 페이지다. 목록을 열 때마다 JSON payload 전체를 전송하지 않는다.

### 두 결과 비교

`GET /repositories/{id}/snapshots/compare?before=10&after=20`

변경 파일은 기본 100개씩 반환한다. `offset`과 `limit`(최대 1000)으로 페이지를 요청하고 `pagination.next_offset`이 null이면 마지막이다. summary는 전체 비교 기준이다.

응답의 before/after는 스냅샷 메타데이터, summary는 added/removed/retained/changed 건수, changes는 변경된 파일의 path/status/metrics다.
각 metric(risk_score/bus_factor/coupling_score)은 before/after/delta를 제공한다. `retained`는 양쪽에 존재하는 모든 파일 수이고 `changed`는 그중 지표가 달라진 파일 수다. changes에는 동일 지표의 retained 파일이 없다.

- 다른 저장소 결과 ID: 404. 계산 버전 불일치/해시 무결성 실패: 409. 잘못된 ID: 422.
- 정확한 path 대응만 적용한다. 이름 변경은 삭제/추가이며 자동 동일 파일 추정은 하지 않는다.
- 관측값이 없는 delta는 null로 표시. 0으로 바꾸지 않는다.
- Risk Score 차이는 각 결과 내 정규화된 상대 점수 차이이며 절대 품질 향상/악화로 단정하지 않는다.
- interpretation에는 양쪽 데이터 수집 범위와 구간 변경 여부가 포함된다.
- 추가/삭제는 분석 결과에 나타남/사라짐을 뜻한다. 파일 수집 범위가 불완전하면 실제 Git 추가/삭제라고 단정하지 않는다.
- 비교 화면을 추가한다면 시작 결과와 종료 결과를 사용자가 선택하고, 증가/감소/추가/삭제 및 데이터 부족을 구분한다.

## 이후 개선

긴 ISO 시각·계산 버전 안내를 분석 기준/수집 범위/데이터 부족 중심으로 요약하고 상세 설명에서 기술 정보를 펼친다. 재분석이 실패해도 마지막 성공 스냅샷을 열 수 있게 한다. 대형 트리는 전체 펼치기 제한을 유지하면서 점진 렌더링과 파일 검색을 검토한다.

## 실연동 검증 추가 결과 — 2026-10-03

[실검증 보고서](LIVE_VALIDATION.md)의 samplemod는 job이 done이어도 PR 상세가 0/9인 부분 수집 결과다. 네트워크 엣지 0을 '협업 없음'으로 단정하지 말고, data_quality의 수집 범위 부족을 함께 표시한다.
git-consortium의 결합도 0건도 6개 커밋이 대형 변경 필터로 전부 제외된 결과다. 조회 성공·수집 완료·분석 근거 충분함은 별도 상태로 표시하는 것이 필요하다.

## 실제 웹·Redis·Celery 검증에서 확인한 문구 — 2026-10-03

GitHub OAuth 로그인 후 GitViz 저장소를 분석하고 코드분석 목록의 `app/tasks/analyze_repository.py` 행을 눌러 파일 상세를 열었다. API와 저장값은 새 계산 버전을 사용하지만 설명에는 이전 정책이 남아 있다.

- “관측 구간은 마지막 refactor 분류 커밋 이후”를 제거하고 스냅샷의 고정 `window_start`/`window_end`를 설명한다. 기본 180일이며 refactor로 초기화하지 않는다.
- “함께 확인할 후보 파일 (IDF 보정 점수 순)” 및 “IDF 보정 점수로 정렬”은 기본 raw coupling/support 순서에 맞춘다. IDF를 사용하려면 사용자가 선택할 수 있게 하고 실제 요청에 `sort_by=idf`를 전달한다.
- 화면의 Risk Score·위험도순 표기는 유지보수 점검 우선순위 의미로 정리하고 API의 `maintenance_priority`와 설명을 연결한다.
- 완료 모달의 커밋 수는 수집 기본 브랜치 누적 건수이고 결과 화면은 분석 구간 건수다. 이번 GitViz는 각각 91/86, Hello-World는 3/0이었다. “수집 커밋”과 “관측 구간 커밋”으로 구분한다.

인수 확인: 타임라인→네트워크→코드분석에서 같은 repo/snapshot이 유지되고, 파일 상세 설명도 같은 스냅샷의 기간과 실제 정렬 정책을 가리켜야 한다. 이번 웹 테스트에서 화면 이동의 repo=2/snapshot=2 유지와 상세 API 조회는 성공했다. 시각화 코드는 이번 검증에서 변경하지 않았다.

## AI 리뷰 화면 계약 — S9

화면: `frontend/pages/ai-review.html` (설계: [AI_S9_UI_DESIGN.md](AI_S9_UI_DESIGN.md), API: [AI_INTERFACE_CONTRACT.md](AI_INTERFACE_CONTRACT.md)). 시각화 화면의 `snapshot`·`sort_by` 상태와 별개이며 서로 섞지 않는다.

**주소가 유일한 기준**: `?repo=<id>&pr=<번호>&input=<uuid>&job=<uuid>`(재실행은 `&retry=<job>`, 새 generation은 `&force=1`). `repo`가 없으면 아무것도 불러오지 않고 저장소 선택을 안내한다. localStorage·기본 repo=1·마지막 작업 기억을 쓰지 않는다. 시각화 화면의 `goPage`가 옮기는 `snapshot`은 AI 화면으로 가져가지 않는다(`goAiReview`는 `repo`만 옮긴다). 저장소가 바뀌면 PR·입력·작업을 가져가지 않으며, 응답의 `repository_id`가 주소와 다르면 그리지 않는다.

**상태 표시 규칙**
- 작업 상태는 `queued`/`running`(+`stage`)/`retry_wait`/`cancel_requested`/`succeeded`/`failed`/`cancelled`를 따로 보인다. 모델 진행률(%)을 만들지 않는다.
- coverage는 PR 전체 / 선택 코드 확보 / 분석 범위를 따로 말한다. 실행 전 `analysis_coverage=none`은 "아직 분석 전"이며 실패가 아니다.
- **지적 0건**은 "분석한 범위에서 모델이 지적을 보고하지 않았다. 문제가 없다는 뜻이 아니다. 사람 검증 전"으로 쓴다. "오류 없음·문제 없음·이상 없음"을 쓰지 않는다. 구조 검증 통과는 "내용이 옳다는 보증이 아님"을 함께 쓴다. 사람 검증은 유효/오탐/불확실/미검증 개수로 보인다.
- `OUTCOME_UNCERTAIN`·`PROVIDER_TIMEOUT`·`RESULT_LOST`는 "비용이 발생했을 수 있음"과 "자동으로 다시 실행하지 않음"을 알리고, 재실행은 사용자가 누를 때만 동의를 다시 받아 시작한다.
- 결과가 이전 SHA의 것이면(현재 PR head와 다름) 그렇게 표시한다.

**원본 코드 위치**: 지적의 `location(path, side, start_line, end_line)`은 입력의 고정 diff 줄 번호다. 근거 보기는 `GET /inputs/{input_id}/files/{source_file_id}?side=&start_line=&end_line=`로 앞뒤 6줄을 불러 해당 범위를 강조한다. 입력 보관 기간이 지나면 410 `INPUT_EXPIRED`이며, 그때는 결과에 저장된 `quote`만 보이고 이유를 설명한다.

**출력 안전**: 서버·모델이 준 문자열(PR 제목·설명, 코드, 지적, 요약)은 `textContent`로만 넣는다. 외부 URL·이미지·링크를 데이터에서 만들지 않는다. API key는 입력 즉시 비우고 응답에는 마스킹 힌트만 쓰며 URL·저장소·콘솔에 남기지 않는다.

**검증**: `node scripts/test_ai_frontend.cjs`(순수 모듈), `scripts/test_ai_pulls_api.py`, 브라우저 확인용 모의 서버 `scripts/ai_ui_mock_server.py`. 운영 DB 스냅샷 검증(`verify_snapshot_integration.py`)은 출시 전 필수 확인이며 S9에서는 미확인이다.
