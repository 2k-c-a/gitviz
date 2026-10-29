# Phase 3 — 핵심 화면과 기존 분석 기능 통합

작성일: 2026-10-29. 기준 커밋: `c459f29`와 같은 작업 디렉터리의 Phase 1·2 결과. 구현·검증 범위는 이번 작업에서 실행한 코드와 결과로 판단한다. 실서비스 연동 완료를 의미하지 않는다.

## 사전 확인과 설계 조정

[프로젝트 진단](phase-1-c459f29/project-audit.md), [UX 문제](phase-1-c459f29/ux-problems.md), [정보 구조](phase-1-c459f29/information-architecture.md), [와이어프레임](phase-1-c459f29/wireframes.md), [구현 계획](phase-1-c459f29/implementation-plan.md), [Phase 2 기반](phase-2-common-ui.md)을 읽고 기존 API·스냅샷·차트·리뷰 코드를 확인했다.

- Vanilla JS ES modules·HTML·CSS, Chart.js·D3, Electron의 기존 렌더링과 typed IPC를 유지했다. 패키지·백엔드 계산·DB·데이터 모델을 변경하지 않았다.
- 새 `overview.html`을 저장 결과의 기본 진입점으로 추가했다. 기존 `timeline.html`, 파일·네트워크·이력 주소는 계속 동작한다.
- `/tree`에는 파일별 마지막 수정 시각이 없다. 개요와 파일 목록은 `observation_count`에 따른 **관측 구간 변경 횟수**로 표시한다. 날짜순 최근 변경 목록을 만들지 않았다.
- 월별 데이터는 관측 기록이 있는 월만 제공한다. 표시 범위는 마지막 3·6개 관측 월 또는 전체이며 임의로 새 기간을 분석하거나 누락 월을 0으로 채우지 않는다.
- AI 결과는 실제 `priority`, `human_validation`, `location.path`, 제목으로 탐색한다. `priority`는 검토 순서이며 장애 심각도가 아니다.
- 서버에 저장하는 원격 리뷰 키와 로컬 워크스페이스의 일회성 BYOK 키를 구분했다. 서버 설정·암호화·전송 동의·멱등성 정책은 기존 구현을 사용한다.

## 구현한 화면과 연결

| 영역 | 실제 연결과 동작 | 주요 파일 |
| --- | --- | --- |
| 저장소 검색·선택 | 기존 GitHub 검색, 이름·설명·메타데이터, 검색 중·빈 결과·오류, 선택 후 저장 결과/명시적 새 분석 분리. 검색어·정렬 URL 복원. 늦은 검색 응답 폐기 | [search.js](../../frontend/js/search.js), [search.html](../../frontend/pages/search.html) |
| 분석 시작·완료 | 기존 POST·상태 폴링·진행 모달 재사용. 완료한 repo/snapshot을 개요로 전달. 최근·내 분석의 저장 결과도 개요로 연결 | [modal.js](../../frontend/js/modal.js), [main.js](../../frontend/js/main.js), [myprojects.js](../../frontend/js/myprojects.js) |
| 분석 개요 | summary KPI, 월별 커밋, 상위 기여자, 메시지 분류, 변경 빈도 상위 파일, 전체 네트워크 지표·상위 쌍. 패널별 독립 로딩·실패·재시도 | [overview.html](../../frontend/pages/overview.html), [overview.js](../../frontend/js/overview.js) |
| 개발 활동·기여자 | 서로 다른 단위의 3축 혼합 차트를 단일 지표 선택으로 변경. 월 선택 수치, 숫자 대안, 유형·릴리즈·주간 히트맵. 상위 50명 안에서 이름 검색→기여 파일 상세 | [timeline.html](../../frontend/pages/timeline.html), [timeline.js](../../frontend/js/timeline.js) |
| 협업 네트워크 | 기존 가중치·클러스터 계산 재사용. 노드 상한 25/50/100/200, 최소 강도, 표시 노드 이름 검색, 확대·축소·이동·초기화·배치 정지. 노드/키보드 목록 선택→관련 노드·선 강조 및 기여자 상세 | [network.html](../../frontend/pages/network.html), [network.js](../../frontend/js/network.js) |
| 파일·공동 변경 | 50행 페이지 탐색, 경로 검색·독립 목록 정렬, 변경 횟수, 상세 열기와 GitHub 링크 분리. 목록/트리/공동 변경/기여 집중도 전환. 검색·페이지·정렬·보기 URL 복원 | [code-analysis.html](../../frontend/pages/code-analysis.html), [code-analysis.js](../../frontend/js/code-analysis.js) |
| 공통 상세 | 기존 기여자↔파일↔공동 변경 파일과 스택 뒤로 가기 유지. 상세 요청 세대 보호·재시도 유지. 협업자 강도는 API 값 그대로 표시 | [drilldown.js](../../frontend/js/drilldown.js) |
| AI 리뷰 | 기존 PR/커밋→파일→미리보기→동의→작업→결과/근거 코드/피드백 유지. 검토 순서·검증 상태·파일/제목 필터 추가. 필터 입력의 포커스와 현재 결과의 미저장 피드백 초안을 화면 메모리에 유지. 미리 불러온 지적과 전체 결과 수를 구분. 모델 선택과 키 설정 진입 정리 | [ai-review.js](../../frontend/js/ai-review.js), [ai-review.html](../../frontend/pages/ai-review.html) |
| 설정·키 관리 | 기존 제공자·모델·키 저장/교체/삭제·사용량 UI 재사용. repo 없이도 로그인 후 설정 가능. 검증한 내부 리뷰 주소로 돌아가기. 복귀 후 새 미리보기·동의 | [settings.html](../../frontend/pages/settings.html), [shell.js](../../frontend/js/ui/shell.js) |

전역 구조: 저장소 탐색 / 최근·내 분석 / URL 분석 시작 → 현재 저장소의 분석 개요·개발 활동·협업 네트워크·파일 분석·분석 이력·AI 리뷰, 그리고 설정·로컬 워크스페이스. repo/snapshot 맥락을 보존하며 설정에서 input/job 등은 검증된 AI 주소로만 복귀시킨다.

## 공통 코드와 기존 기능 영향

- [analytics-view.js](../../frontend/js/ui/analytics-view.js): 공통 패널 로딩·빈 결과·재시도, 기존 DOM을 보존하는 패널 바인딩, 네이티브 버튼, 기여자 목록, 유형 막대, 단일 지표 Chart 렌더링. 기존 Phase 2의 `element`·`createState`·테마 토큰을 재사용한다.
- [analysis.css](../../frontend/css/analysis.css): 개요 그리드·목록·필터·보기 전환, 긴 파일 경로 줄바꿈. 빈 로딩 슬롯이 KPI 그리드 셀을 차지하지 않도록 처리했다.
- [api.js](../../frontend/js/api.js): 문서 수명 안에서 같은 snapshot/endpoint의 요청을 합치고 성공 결과를 제한된 캐시에 둔다. 실패는 캐시에서 제거해 재시도 가능하며 계정 변경 시 폐기한다. 화면 간에는 repo/snapshot 주소를 유지한다. 다른 HTML로 이동한 후 HTTP 요청까지 없어지는 영속 캐시는 아니다.
- [chart-theme.js](../../frontend/js/ui/chart-theme.js): 새 차트도 토큰의 테마 변경을 반영한다. 차트의 숫자·단위·월별 수치 대안을 제공한다.
- [app-protocol.ts](../../desktop/src/app-protocol.ts), [workspace-navigation.js](../../frontend/js/platform/workspace-navigation.js): 같은 앱의 overview/settings 문서만 기존 허용 목록에 추가했다. 새 네트워크 권한이나 로컬 브리지 노출을 추가하지 않았다.
- 기존 분석·BF80·파일 결합 점수·네트워크 가중치·AI API 계약·poller·동의·피드백은 교체하지 않았다. 파일 결합의 횟수/분모/방향별 비율/PR·커밋 fallback 설명도 유지한다.
- 네트워크 화면의 필터는 표시 그래프에만 적용한다. 전체 스냅샷 지표와 상위 협업 쌍은 전체 기준임을 표시한다. 같은 PR/파일 공동 수정·리뷰 관측 관계를 파일 간 공동 변경 횟수나 실제 인적 협업으로 단정하지 않는다.

## 실행한 검증

각 영역 구현 후 `npm --prefix desktop run build`와 Electron의 기존 화면 기본 로딩 검증을 실행하고 다음 영역으로 진행했다. 네트워크 초기화 순서 오류와 KPI 빈 슬롯 배치 문제는 검증 중 수정했다.

| 검증 | 결과와 범위 |
| --- | --- |
| 최종 build | 통과: main/preload TypeScript, 실제 렌더러 자산 복사·번들 구성 |
| desktop typecheck / lint | 통과. 프론트 JS 전체도 `node --check`로 구문 검사했다. Vanilla JS의 정적 타입 보장을 뜻하지 않는다 |
| desktop 단위 테스트 | 48/48 통과. 암호화 파일 게시 등 Windows 파일시스템 기능이 필요한 검사는 정상 Windows 실행 환경에서 수행 |
| `test_viz_modules.cjs` | 통과: 주소·표시·정책·폴링 순수 모듈 |
| `test_metric_display.cjs` | 통과: null/0/자료 부족/이전 버전·정렬 의미. 케이스마다 서버 응답을 교체하므로 캐시 계정 변경 이벤트로 독립시킴 |
| `test_workspace_navigation.cjs` | 통과: 명시적 맥락·허용 문서·자동 실행 없음 |
| `test_ai_frontend.cjs` | 39/39 통과: 동의·세대·멱등성·오류·API·폴링·키 비노출 등 |
| [remote-compat.e2e.ts](../../desktop/tests/remote-compat.e2e.ts) | 통과: 실제 sandbox 렌더러의 기존 화면과 overview/settings, 내비게이션·snapshot·테마·차트·상세 크기·포커스·CSP·401 처리. 잘못된 IPC를 거절하는 의도된 `INVALID_REQUEST` 로그 포함 |
| [workspace.e2e.ts](../../desktop/tests/workspace.e2e.ts) | 통과: 원격→로컬 왕복과 브리지 경계, 자동 연결/실행 없음 |
| [phase3-flows.e2e.ts](../../desktop/tests/phase3-flows.e2e.ts) | 통과: 아래 실제 DOM 클릭 흐름. 전용 임시 프로필·검증용 API transport만 사용 |
| [ui-preview.e2e.ts](../../desktop/tests/ui-preview.e2e.ts) | 통과: 실제 Electron offscreen software paint, 라이트/다크, 900·1280·1440 화면 캡처와 다크 텍스트·표면 검사 |

새 사용자 흐름 검증:

1. 검색→대상 선택→저장 결과 개요. 이때 분석 POST가 발생하지 않고 메타 요청을 합치는지 확인.
2. 명시적 새 분석 요청→기존 상태 폴링→완료→완료 snapshot의 개요. 단일 POST 확인.
3. 개요의 유형 API만 503→다른 패널 유지→해당 패널 재시도 성공.
4. 개발 활동→기여자→수정 파일→뒤로 가기. 비율 선택 시 0–100 축과 월별 숫자 선택 확인.
5. 그래프 확대→이름 필터→노드 선택 강조·상세→표시 상한 변경의 실제 query 확인.
6. 52개 파일의 50/2행 페이지 이동→검색→상세→트리 전환→새로고침 맥락 복원. 공동 변경 3회·방향 비율→파일 상세 확인.
7. 설정에서 가상 키 저장·교체·삭제→서버 API 호출과 입력 즉시 비움·본문/localStorage 원문 비노출 확인.
8. 커밋 선택→파일 선택→미리보기→설정 왕복→새 동의→리뷰 실행→두 지적 중 검토 순서·텍스트 필터→입력 포커스·미저장 메모 보존→근거 코드. PR 선택→파일 목록도 별도 확인.

검증용 응답은 `desktop/tests`에만 있다. 제품 UI에 가짜 분석 데이터나 동작하지 않는 버튼을 넣지 않았다. 캡처는 `.validation/phase3/screenshots`에 있으며 실서비스 분석 결과가 아닌 검증용 데이터임을 하단에 표시했다.

## 남은 제약과 후속 검증

- 실제 GitHub OAuth·원격 분석 서버·Postgres/Redis/Celery의 새 분석, 유료 제공자/로컬 모델 호출은 실행하지 않았다. 외부 호출의 성공·비용·운영 환경 성능을 이번 결과로 보장하지 않는다. 설치된 전체 앱의 배포 패키지 검증도 별도다.
- PR 경로는 선택→파일 목록까지, 커밋 경로는 동의→결과까지 UI 실행 검증했다. 서버가 수행하는 실제 수집·모델 추론은 전용 환경에서 확인해야 한다.
- 등록 검색 응답은 repository_id를 제공하지 않는다. 최근 100개와 로그인한 내 분석 목록의 GET 매핑을 사용하며, ID를 찾지 못하면 명시적으로 안내한다. 자동 POST로 대체하지 않는다. 더 오래된 타인 분석을 직접 여는 기능에는 서버의 권한 검사 포함 조회 API가 필요하다.
- 파일별 최종 수정 시각/날짜순 변경 이력은 연결할 데이터가 없어 추가하지 않았다. 기존 변경 빈도와 공동 변경 관계는 연결했다.
- 화면의 필터는 제공된 범위 안에서 동작한다. 기여자는 상위 50명, 그래프는 표시 상한, 공동 변경 목록은 기존 서버 상위 50쌍, 상세 공동 변경은 기존 상위 20개 정책이다. AI 결과 필터는 불러온 지적에 적용하고 더 보기로 확장한다.
- 대규모 실제 저장소·고밀도 그래프·장문 리뷰의 성능 프로파일링, Windows DPI 125/150/200%, 스크린리더의 실제 발화·키보드 탐색, 고대비 테마는 후속 검증이 필요하다. 임의의 가상 스크롤 라이브러리는 추가하지 않았다.
- 분석 이력·비교와 로컬 프로젝트 도구는 기존 기능을 유지했다. 이번 단계에서 해당 기능을 전면 재설계하지 않았다.

완료 판단: 기존 API에 연결된 핵심 UI 구현과 격리된 실제 렌더러 검증은 완료했다. 실서비스 통합 수용 검증은 미완료이며 별도 환경과 계정으로 확인해야 한다.
