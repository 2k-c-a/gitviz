# 기존 UX 문제와 개선 우선순위

기준: `c459f29` · 2026-10-29 · [조사 범위·실행 한계](project-audit.md) · [해결 구조](information-architecture.md) · [구현 계획](implementation-plan.md)

사용성 인터뷰/실험 없이 코드와 기존 패키지를 조사한 진단이다. '사용자 영향'은 모두 추론이다. **실행 확인**은 직접 본 범위, **코드 확인**은 정적 구현 근거, **검증 필요**는 환경·데이터를 갖춘 후 재현할 항목이다. P0은 출시 전 핵심 흐름의 정확성/접근 보장, P1은 주 작업의 탐색·이해, P2는 효율·시각 마감이다. 횟수나 개발 공수에 대한 실측 우선순위가 아니다.

## U01. 앱이 두 제품처럼 나뉘는 진입·전역 내비게이션

1. 현재 상태: **실행+코드 확인.** root workspace와 `/web/index.html`은 링크로 오가며, 전자는 단일 긴 문서, 후자는 웹 navbar/hero/footer다. root 고정 dark와 원격 밝은 화면을 직접 보았다.
2. 문제: 앱의 시작 목적과 현재 작업 영역이 명확히 이어지지 않는다.
3. 원인: 기존 웹 UI 재사용과 local workspace 기능 추가에 각각 별도 layout/style을 사용했다.
4. 사용자 영향: 원격 분석을 하려는 사용자가 로컬 도구들 사이에서 시작점을 찾고, 이동 후 현재 맥락을 다시 해석할 가능성.
5. 개선: 공통 형태의 좌측 탐색·헤더·상태 영역. 원격 저장소와 로컬 프로젝트 모드를 명시하고 별도 실행 경계는 유지한다. desktop에서는 소개/footer보다 탐색/최근 저장소를 먼저 보여준다.
6. 우선순위: **P1**, 전체 개편의 선행 작업.
7. 관련: [workspace HTML](../../../frontend/pages/workspace.html), [desktop CSS](../../../desktop/ui/styles.css), [원격 index](../../../frontend/index.html), [navbar](../../../frontend/pages/components/navbar.html), [build](../../../desktop/scripts/build.mjs).

## U02. 기존 분석 결과로 이동하는 검색 동작의 계약 불일치

1. 현재 상태: **코드 확인.** `loadRegisteredMap()`은 `/repositories?limit=200`을 요구하나 API Query는 `le=100`. 비정상 응답이면 map을 채우지 않고 조용히 반환한다. desktop endpoint 정의 자체는 number를 허용한다.
2. 문제: is_registered 카드가 ID를 얻지 못해 '상세 분석 보기'에서 `startAnalysis(url)`로 fallback할 수 있다. 실서버 재현은 하지 않았다.
3. 원인: 검색 응답에 repository ID가 없고 별도 목록 join을 쓰며, 목록 상한을 서로 다르게 가정한다. POST 등록은 기존 저장소라도 분석 작업을 시작한다.
4. 사용자 영향: 기존 결과를 보려다 로그인/진행 모달 또는 불필요한 재분석에 진입할 가능성.
5. 개선: 우선 유효한 목록 상한 사용과 명시적 미해결 상태. ID를 확정하지 못하면 자동 POST하지 않는다. 모든 저장소를 정확히 찾는 정책은 현 API 제한 안에서 설계하고, 필요한 ID 조회 계약 확장은 별도 검토한다.
6. 우선순위: **P0**, 화면 미화보다 먼저 재현·해소.
7. 관련: [search.js](../../../frontend/js/search.js)의 `loadRegisteredMap/goToDetail`, [repositories.py](../../../app/api/repositories.py)의 `list_repositories/create_repository`, [remote-contracts](../../../desktop/src/remote-contracts.ts).

## U03. 로컬 프로젝트와 원격 저장소 연결에 내부 ID를 요구

1. 현재 상태: **실행+코드 확인.** root에서 '서버 저장소 ID' 숫자 입력 후 계정/저장소 확인 연결.
2. 문제: 사용자가 서버 내부 ID를 알아야 한다.
3. 원인: 구현 계약의 repositoryId를 그대로 사용자 입력으로 노출했다.
4. 사용자 영향: URL을 알고도 연결하지 못하거나 다른 저장소 ID를 입력하고 실패할 가능성.
5. 개선: '원격 저장소 연결' 버튼 → 본인/최근 저장소 이름·owner/repo로 선택 → canonical URL·계정 확인. ID는 내부 전달로 유지한다. 접근 확인/native 확인은 생략하지 않는다.
6. 우선순위: **P1**.
7. 관련: [workspace HTML](../../../frontend/pages/workspace.html), [projects.js](../../../frontend/js/workspace/projects.js)의 `remoteAction`, [remote workspace](../../../desktop/src/remote-workspace.ts).

## U04. 작업 진행 상태가 모달과 여러 목록에 분산

1. 현재 상태: **코드 확인.** 분석 모달에는 단계·rate-limit/stale·실패/마지막 성공 결과가 구현돼 있다. 닫으면 poll은 중단하며, 홈 목록과 local 알림의 업데이트 방식도 다르다.
2. 문제: 모달을 닫은 뒤 현재 작업을 찾는 위치가 일정하지 않다. '진행 상태가 전혀 없다'는 진단은 부정확하다.
3. 원인: 일반 분석·AI·로컬 검증 각각의 job controller와 알림 경로를 화면별로 붙였다.
4. 사용자 영향: 작업이 중단됐다고 오해하거나 다시 시작할 수 있다.
5. 개선: 선택 저장소 헤더에 작업 상태, 전역 작업함 진입, 상세 진행 패널. 모달 닫기/조회 중단/작업 취소를 다른 행동으로 표시. 기존 상태 API로 조회하고 새 서버 알림 보장으로 확대하지 않는다.
6. 우선순위: **P1**.
7. 관련: [modal.js](../../../frontend/js/modal.js), [poll.js](../../../frontend/js/poll.js), [main.js](../../../frontend/js/main.js), [inbox.js](../../../frontend/js/workspace/inbox.js).

## U05. 현재 저장소·관측 결과와 AI 진입 맥락 단절

1. 현재 상태: **코드 확인.** 분석 탭은 repo/snapshot/sort_by를 URL로 보존한다. 그러나 전역 `goAiReview()`는 항상 repo 없는 AI 주소로 가서 프로젝트를 다시 선택하게 한다.
2. 문제: 이미 저장소를 선택한 상황에서도 재선택한다.
3. 원인: AI 독립 프로젝트 선택을 전역 진입의 유일한 정책으로 사용했다.
4. 사용자 영향: 반복 선택과 다른 저장소 리뷰의 혼동 가능성.
5. 개선: 저장소 안의 AI 리뷰는 현재 repo를 전달하고, 저장소 밖 진입에서만 선택 화면 사용. 분석 snapshot과 AI base/head는 다른 기준이므로 각각 표시한다. 저장소 변경 시 이전 input/job을 계승하지 않는다.
6. 우선순위: **P1**.
7. 관련: [navbar-loader](../../../frontend/js/navbar-loader.js)의 `goAiReview`, [ai-state](../../../frontend/js/ai-state.js), [viz-state](../../../frontend/js/viz-state.js).

## U06. 개요가 없고 분석 화면별 긴 정보 묶음

1. 현재 상태: **코드 확인.** timeline은 KPI·월별 혼합 차트·히트맵·상위 기여자·릴리즈·유형을, code-analysis는 tree·전체 표·기여 분포·공동 변경을 세로로 나열한다.
2. 문제: 전체 현황 파악과 특정 사람/파일 탐색의 우선순위가 섞인다.
3. 원인: 지표 구현 단위로 패널을 누적하고 탐색 목적에 따른 진입 화면이 없다.
4. 사용자 영향: 먼저 볼 데이터 결정과 반복 스크롤 부담 가능성. 실제 탐색 시간은 미측정.
5. 개선: 개요는 핵심 KPI·활동 요약·점검할 파일/상위 협업 링크만. 활동/협업/파일은 목적별 화면, 파일 내 목록/트리·공동 변경은 탭으로 전환한다.
6. 우선순위: **P1**.
7. 관련: [timeline HTML](../../../frontend/pages/timeline.html), [code HTML](../../../frontend/pages/code-analysis.html), [workspace HTML](../../../frontend/pages/workspace.html).

## U07. 기여자·파일 상세에서 목록 맥락이 가려짐

1. 현재 상태: **코드 확인.** 공용 overlay가 내부 stack back을 제공한다. 파일/협업자 부분은 별도 160~180px 높이 스크롤이고 닫으면 stack을 지운다.
2. 문제: 원래 표와 상세를 나란히 비교하기 어렵고 다중 스크롤로 탐색 깊이가 쌓인다.
3. 원인: 모든 드릴다운을 모달에 같은 방식으로 넣었다.
4. 사용자 영향: 여러 파일을 비교하거나 기여자→파일→다른 기여자로 이동할 때 복귀 비용 증가 가능성.
5. 개선: 넓은 창에서 우측 상세 패널, 작은 창에서 전체 콘텐츠 상세. 부모 목록/필터/선택/스크롤과 상세 내부 back stack 보존. 상한 및 전체 건수 표시.
6. 우선순위: **P1**.
7. 관련: [drilldown.js](../../../frontend/js/drilldown.js), [modal HTML](../../../frontend/pages/components/modal.html), [main CSS](../../../frontend/css/main.css).

## U08. 협업 그래프의 방향·이름·범위 탐색 부족

1. 현재 상태: **코드 확인.** 상위 50노드·8쌍 고정 요청. D3 node click/drag와 title hover, 선은 화살표 없는 line이다. 검색·명시적 zoom·일시정지·fit UI가 없다.
2. 문제: 방향 그래프라고 설명하지만 관계 방향은 보이지 않는다. 전체 metrics와 표시 노드 집합의 범위도 다르다.
3. 원인: 그래프 그리기 중심 구현과 전체/표시 부분집합 메타의 표현 부족.
4. 사용자 영향: 이름 찾기·쌍 비교·방향 해석에 어려움, 화면의 고립을 전체 고립으로 오인할 가능성.
5. 개선: 기여자 검색/표시 상한·최소강도 toolbar, fit/zoom/정지, 선택 이웃 강조. 방향선·정확한 A→B/B→A 값은 선택 시 표시. 상위 쌍 표를 그래프 옆에 둔다. 전체 metrics와 현재 표시 N명을 분리한다.
6. 우선순위: **P1**.
7. 관련: [network.js](../../../frontend/js/network.js), [network HTML](../../../frontend/pages/network.html), [serve_snapshot](../../../app/services/analysis_snapshots.py).

## U09. 파일 계층과 공동 변경을 함께 이해하기 어려움

1. 현재 상태: **코드 확인.** 파일 tree는 디렉터리 계층이며 공동 변경 관계선이 아니다. 공동 변경 쌍 목록은 같은 페이지 아래 별도 패널이다. 공통 파일 상세는 방향 비율·support를 제공한다.
2. 문제: tree와 관계 그래프의 의미를 혼동하거나 선택 파일에서 관련 후보로 이동하려고 긴 페이지를 내려갈 수 있다.
3. 원인: '코드분석' 이름 아래 다른 탐색 모델을 혼합했다.
4. 사용자 영향: 실제 import 관계로 오해하거나 필요한 후보를 놓칠 가능성.
5. 개선: '파일' 화면에 '파일 목록/계층', '공동 변경' 탭. 선택 파일을 패널로 열어 수정자와 관련 파일 연결. 단위 PR/commit, 양방향 분모·관측 건수, fallback 이유를 같은 표에 유지한다.
6. 우선순위: **P1**.
7. 관련: [code-analysis.js](../../../frontend/js/code-analysis.js), [drilldown.js](../../../frontend/js/drilldown.js), [analytics.py](../../../app/api/analytics.py)의 `_compute_coupling`.

## U10. 수량이 다른 차트와 작은 테이블 글자

1. 현재 상태: **코드 확인.** timeline 180px 높이에 커밋/활성 기여자/버그 수정 비율 3축 혼합이며 비율 축은 숨김이다. 여러 표와 범례가 11px, 경로는 160px 말줄임 등으로 제한된다.
2. 문제: 서로 다른 단위를 직접 비교하거나 전체 경로를 구분하기 어렵다. 실제 데이터의 겹침과 대비 실패는 미측정.
3. 원인: 작은 패널 안에 다수 지표를 겹치고 축 설명을 tooltip에 의존한다.
4. 사용자 영향: 활동과 수정 비율의 변화 크기를 잘못 읽거나 동명 파일 혼동 가능성.
5. 개선: 개요는 단일 커밋 bar, 활동 화면은 동일 x축의 분리 차트와 단위 표시, 숫자 표 대체. 표 13px 이상 기본, 경로 열 확장/복사, 숫자 우측 정렬, sticky header.
6. 우선순위: **P1**(단위), **P2**(밀도 마감).
7. 관련: [timeline.js](../../../frontend/js/timeline.js)의 `renderTimelineChart`, [root CSS](../../../frontend/css/root.css), [main CSS](../../../frontend/css/main.css).

## U11. 데스크톱 창 크기 대응이 휴대폰 breakpoint 중심

1. 현재 상태: **코드 확인.** 웹 768px 아래 KPI 세로 적층·표 최소700px, network 고정460px. 파일 tree는 inline `1fr 300px` 두 열, SVG 560px. root 최소600×400과 별도 max-width640px CSS다.
2. 문제: 앱 최소 창·중간 폭에서 주요 조작과 긴 경로의 배치 정책이 통일되어 있지 않다.
3. 원인: 웹 responsive 보완과 desktop 기본 창 제약을 함께 설계하지 않았다.
4. 사용자 영향: 작은 창에서 가로/세로 중첩 스크롤과 상세 영역 압축 가능성. 600px 실제 overflow는 다음 단계 재현 필요.
5. 개선: 1280+/960~1279/600~959 CSS px 세 단계, 상세 panel→전체 콘텐츠 전환, 도구 모음 wrap/overflow, table 내부만 가로 스크롤. 최소 창과 Windows 125/150/200% DPI에서 검증한다.
6. 우선순위: **P1**.
7. 관련: [window.ts](../../../desktop/src/window.ts), [index CSS](../../../frontend/css/index.css), [code HTML](../../../frontend/pages/code-analysis.html), [desktop CSS](../../../desktop/ui/styles.css).

## U12. 키보드·접근성의 공통 규칙 부재

1. 현재 상태: **코드 확인.** navbar/sub-tab/행/기여자 이름에 clickable div가 있고 delegated click만 처리한다. D3 노드는 click/drag만. 일반 drilldown overlay에는 dialog semantics/focus trap/복귀 처리가 보이지 않는다. 반면 AI project picker는 native dialog와 cancel을 쓴다. 한국어 분석 HTML 중 `lang=en`도 있다.
2. 문제: 일부 주요 탐색을 Tab/Enter만으로 수행할 수 없다. 모든 화면에 접근성이 없다는 뜻은 아니다.
3. 원인: native control 대신 pointer 동작 위주로 쌓인 component와 모달 방식 차이.
4. 사용자 영향: 키보드·스크린리더 사용자의 기능 도달 제한 가능성.
5. 개선: 링크/버튼 semantics, nav current, tabs 키 규약, focus-visible, drawer 열기/닫기 포커스 복귀, graph 대체 표, `lang=ko`, 상태 live region. Escape는 패널만 닫고 작업을 취소하지 않는다.
6. 우선순위: **P0**, 새 공통 component 수용 조건.
7. 관련: [navbar](../../../frontend/pages/components/navbar.html), [actions](../../../frontend/js/platform/actions.js), [drilldown](../../../frontend/js/drilldown.js), [network](../../../frontend/js/network.js), [AI picker](../../../frontend/js/ai-project-picker.js).

## U13. 상세 요청 경쟁과 화면 단위 오류 결합

1. 현재 상태: **코드 확인.** drilldown은 async renderTop 중 요청 세대 guard가 없고 응답을 공용 body에 쓴다. timeline은 여러 endpoint를 Promise.all로 묶어 하나 실패하면 초기 전체 렌더가 실패한다. AI는 guard가 이미 있다.
2. 문제: 빠른 상세 전환에서 이전 응답이 덮을 가능성, 보조 패널 실패가 정상 주요 데이터를 가릴 가능성. 네트워크 지연으로 재현하지 않았다.
3. 원인: 일부 화면만 요청 세대/부분 실패 규칙을 적용했다.
4. 사용자 영향: 선택한 사람/파일과 맞지 않는 상세 또는 사용 가능한 정보의 접근 제한 가능성.
5. 개선: repo/snapshot/selection 세대와 AbortController, 늦은 응답 무시. 핵심 meta 실패는 전체 오류, 부가 panel 실패는 해당 panel 재시도. 계정 변경 시 모든 캐시 폐기.
6. 우선순위: **P0**(정합성), **P1**(부분 복구).
7. 관련: [drilldown.js](../../../frontend/js/drilldown.js)의 `renderTop`, [timeline.js](../../../frontend/js/timeline.js)의 `initTimelinePage`, [api.js](../../../frontend/js/api.js)의 `snapshotMetas`.

## U14. API 키 설정 위치와 보관 경계가 분산

1. 현재 상태: **코드 확인.** 원격 AI 화면 안에서 서버 저장 key 관리, workspace 리뷰는 이번 미리보기용 key 입력. 전역 설정 화면은 없다.
2. 문제: 동일 API key라는 말로 서로 다른 보관/사용 정책을 이해해야 한다.
3. 원인: 원격 credential 서비스와 로컬 cloud transport를 기능별로 노출했다.
4. 사용자 영향: 키를 어디에 저장했는지, 교체가 어느 리뷰에 영향을 주는지 혼동 가능성.
5. 개선: 설정에 계정/모델·키/로컬 환경/진단을 모으고, '서버 저장 키'와 '이번 로컬 리뷰 전용'을 명시. 리뷰에서 key 상태 요약과 설정 return link 제공. 현재 없는 영구 로컬 키 저장은 이번 재설계의 기정 기능으로 넣지 않는다.
6. 우선순위: **P1**.
7. 관련: [ai-review.js](../../../frontend/js/ai-review.js)의 `renderKeys`, [cloud-review.ts](../../../desktop/src/cloud-review.ts), [credentials.py](../../../app/services/ai/credentials.py).

## U15. 데이터 제한 설명이 본문을 많이 점유

1. 현재 상태: **코드 확인.** 결과 banner는 조회/수집/근거를 잘 분리하나, 파일/로컬 화면에는 긴 주의문이 여러 섹션에 반복된다. 기술 details 접기 구현도 이미 있다.
2. 문제: 필요한 의미 구분과 초기 읽기 부담이 함께 커진다.
3. 원인: 구현/해석 한계의 설명을 대부분 상시 본문으로 제공한다.
4. 사용자 영향: 핵심 지표·행동을 찾기 어렵고 경고를 반복 문구로 흘려볼 가능성.
5. 개선: 항상 보이는 짧은 상태/표본·단위, 필요 시 정의/기술 details로 단계적 공개. 불완전 수집·결과 불명·외부 전송 동의·적용 승인처럼 판단 필수 내용은 접어 숨기지 않는다.
6. 우선순위: **P2**, 정보 누락 없이 정리.
7. 관련: [page-context.js](../../../frontend/js/page-context.js), [code HTML](../../../frontend/pages/code-analysis.html), [workspace HTML](../../../frontend/pages/workspace.html).

## U16. 환경 오류와 초기 빈 상태의 복구 동선

1. 현재 상태: **실행+코드 확인.** package root에 로컬 구성 준비 실패/서버 연결 확인, 원격 랜딩 최근 목록에 '목록을 불러올 수 없습니다'를 보았다. root 진단 저장/상태 확인은 있지만 랜딩 목록에는 같은 자리 재시도 버튼이 없다.
2. 문제: 미설정·로그인 필요·연결 장애·분석 없음이 주 작업 시작점에서 충분히 행동 중심으로 구분되지 않는다.
3. 원인: transport 예외의 generic catch 문구와 모듈별 상태 처리.
4. 사용자 영향: 무엇을 설정해야 하는지 알기 어렵거나 빈 목록으로 오인할 가능성.
5. 개선: 오류 코드가 보장하는 범위에서만 상태 분류, '다시 확인/계정 연결/설정/최근 성공 결과' CTA. 알 수 없는 실패는 원인을 단정하지 않고 진단 열기. agent 실패와 원격 조회를 독립 기능으로 취급한다.
6. 우선순위: **P1**. 실행 실패 원인 자체는 **검증 필요**이며 이번 단계에서 수리하지 않는다.
7. 관련: [main.js](../../../frontend/js/main.js)의 `loadRecentRepos`, [desktop-auth.js](../../../frontend/js/workspace/desktop-auth.js), [status.js](../../../desktop/ui/status.js), [transport](../../../frontend/js/platform/transport.js).

## 우선순위 묶음과 성공 신호

| 순서 | 문제 | 다음 단계의 확인 신호(목표) |
|---|---|---|
| 선행 정확성 | U02/U12/U13 | 상세 조회가 POST하지 않음, 키보드 주요 흐름 도달, 역순 응답에도 선택·상세 일치 |
| 앱 골격 | U01/U03/U05/U11/U16 | 현재 저장소/모드가 항상 보임, ID 직접 입력 제거, 최소 창에서 CTA 도달 |
| 분석 탐색 | U04/U06/U07/U08/U09/U10 | 진행 조회 재개, 맥락 보존 드릴다운, 방향·분모·전체/표시 범위 구분 |
| 마감 | U14/U15 및 스타일 | 보관 위치 이해, 경고/상태 중복 감소, 두 테마와 숫자 가독성 |

실험은 A~H 과제를 설명 없이 수행하도록 하고 성공/실패·되돌림·오선택·질문·소요 시간을 기록한다. 목표는 A 기존 결과 열기에서 불필요한 분석 생성 0회, D/F 상세 복귀 시 필터·선택·스크롤 손실 0회, G 명시 동의 없는 호출 0회다. 일반 과제 시간 단축률은 초기 측정 후 정하며 현재 개선율로 보고하지 않는다.
