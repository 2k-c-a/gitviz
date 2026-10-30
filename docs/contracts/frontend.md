# 프론트 상태·표현 계약 안내

검토일: 2026-10-23 · 코드 기준: `a8351f0` · [문서 안내](../index.md)

확인: [S9 설계 원본](../archive/originals/docs/AI_S9_UI_DESIGN.md)과 [시각화 전달 원본](../archive/originals/docs/VISUALIZATION_HANDOFF.md)은 유용한 상세 기준과 과거 과제를 함께 담는다. S9의 v1.4 및 `/ai/v1` 표기는 현재 DTO 1.9·`/api/ai/v1`과 구분해서 읽는다.

## URL과 응답 혼입 방지

| 화면 | 현재 기준 | 근거 |
|---|---|---|
| 분석 시각화 | repo·snapshot·sort_by를 이동에 유지. 다른 repo에 snapshot 혼입 금지 | [navbar](../../frontend/js/navbar-loader.js), [상태](../../frontend/js/viz-state.js) |
| 분석 repo fallback | 일반 분석 API에는 query → localStorage → 기본 1이 남아 있음 | [api.js](../../frontend/js/api.js) |
| AI | 명시한 repo·pr/commit·input·job·shared로 상태 결정. repo 없는 기본 저장소 fallback 금지 | [ai-state](../../frontend/js/ai-state.js), [ai-review](../../frontend/js/ai-review.js) |
| AI 비동기 | navigation guard·abort·직렬 polling·429/backoff·종료 중단 | [ai-poll](../../frontend/js/ai-poll.js), [AI 프론트 검사](../../tests/frontend/test_ai_frontend.cjs) |

AI의 엄격한 URL 규칙을 일반 분석 화면에도 이미 동일하게 적용됐다고 설명하지 않는다. 현재 공유 조회는 `?repo=<id>&shared=<result UUID>`를 사용하며 개인 job 조회와 분리한다.

## 사용자 표현

- 실행 전 read는 ‘코드 확보됨/전송 예정’이며 ‘전송됨’이 아니다. profile 변경은 미리보기·동의를 다시 확인한다.
- local provider는 key가 필요 없고 외부 전송 비활성에서도 실행 가능하다. 클라우드 버튼 규칙을 로컬에 일괄 적용하지 않는다.
- 지적 0건은 오류 없음이 아니고 사람 검증 전 상태와 실제 선택·분석·전체 분모를 표시한다.
- 구조·파일·줄·인용 검증과 내용 정확도는 구분한다. 코드·모델 문자열은 실행 가능한 HTML로 다루지 않는다.
- 결과 불명·정산 후 저장 실패는 비용 가능성과 명시적 재실행을 알린다. 접수 여부가 모호하면 같은 멱등 key를 유지한다.
- 일반 분석의 현재 지표·결측·구형 계산 설명은 [분석 계약](analytics.md)을 따른다. refactor 문구가 구형 snapshot 설명인지 먼저 확인한다.
- 공유 화면은 결과·인용·판단·메모를 보여 주지만 입력 원문·쓰기 권한을 열지 않는다. ‘팀원’이라는 UI 이름만으로 조직 구성원 확인을 보장하지 않는다.

추정: 원본의 오래된 수정 과제는 현행 계약에서 떼고 Historical 상태로 연결하는 것이 적절하다.

미확정: 현재 실제 브라우저·OAuth·배포 서버에서의 전체 흐름은 이번 문서화에서 재실행하지 않았다. 순수 모듈 검사 통과만으로 DOM·레이아웃·인증 E2E를 통과 처리하지 않는다.
