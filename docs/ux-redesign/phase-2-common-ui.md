# Phase 2 — 공통 디자인 시스템·데스크톱 셸 구현 보고

작업일: 2026-10-29 (Asia/Seoul) · 소스 기준: `c459f29` + 이 작업의 미커밋 변경

[Phase 1 진단](phase-1-c459f29/project-audit.md) · [정보 구조](phase-1-c459f29/information-architecture.md) · [와이어프레임](phase-1-c459f29/wireframes.md) · [구현 계획](phase-1-c459f29/implementation-plan.md)

## 적용 범위와 설계 조정

기존 HTML/CSS/ES module·Chart.js·D3·Electron 구조에서 공통 UI를 구현했다. 패키지 추가/제거, 프레임워크 교체, 서버 API·분석식·DB 모델·main/preload 권한 변경은 없다. 소스와 `desktop/dist` 개발 빌드에 적용했으며 기존 `desktop/out` 설치/배포 패키지는 재생성하지 않았다.

Phase 1의 독립 개요·설정 페이지는 현재 존재하지 않는다. 이번에는 기존 개발 활동/협업/파일/AI/분석 이력 화면을 실제 링크로 연결했다. 개요를 가짜 화면으로 만들지 않았다. 테마 선택은 공통 헤더에서 실제 동작한다. API 키 관리와 AI 작업 단계는 기존 화면에 유지하며 후속 단계에서 이동할 수 있다.

root 로컬 화면은 `/index.html`, 원격 UI는 `/web/...`를 유지한다. 두 경계에 같은 CSS·셸 모듈을 사용하지만 workspace bridge를 원격 화면으로 확장하지 않는다. 로컬 기능은 기존 섹션/ID/이벤트를 보존하면서 sidebar의 섹션 탐색과 패널 스타일로 연결했다. 기존 서버 저장소 ID 입력 등 기능별 재설계는 이 단계에 확대하지 않았다.

## 수정·추가 파일

| 파일/모듈 | 변경 내용 |
|---|---|
| [tokens.css](../../frontend/css/tokens.css) | 표면/텍스트/상태·데이터색, 글자/간격/반경/움직임 토큰과 기존 CSS 변수 alias |
| [components.css](../../frontend/css/components.css) | 실제 native button/input/select, 검색·filter·tabs, badge/panel/header, 상태/skeleton/tooltip/dialog 공통 스타일 |
| [shell.css](../../frontend/css/shell.css) | app grid, nav/toolbar/main/status, detail panel, 로컬 section, 960/1280 breakpoint |
| [components.js](../../frontend/js/ui/components.js) | 안전한 text DOM state, loading/empty/error/retry, modal focus/inert/Tab/Escape/복귀, tooltip, legacy action 접근성 |
| [theme.js](../../frontend/js/ui/theme.js) | 첫 paint 전 system/light/dark 결정, UI preference만 저장, OS·storage 변경 동기화 |
| [shell.js](../../frontend/js/ui/shell.js), [workspace-shell.js](../../frontend/js/ui/workspace-shell.js) | 기존 DOM을 이동하는 공통 셸, current repo/snapshot 표시, 허용된 기존 페이지 링크, local 섹션 연결 |
| [chart-theme.js](../../frontend/js/ui/chart-theme.js) | Chart.js theme plugin: 배경/텍스트/grid/tooltip·데이터 series 색 갱신, 데이터·축 계산 보존 |
| [index.css](../../frontend/css/index.css), [root.css](../../frontend/css/root.css), [desktop styles](../../desktop/ui/styles.css) | 중복 테마 변수 제거, shared 스타일 import. desktop은 API 초기화 없이 UI만 공유 |
| [navbar-loader](../../frontend/js/navbar-loader.js), [navbar fragment](../../frontend/pages/components/navbar.html), [login](../../frontend/js/login.js) | 공통 셸 장착, 기존 auth IDs 유지, native account controls, aria-expanded/disabled·Escape, 현재 repo AI 진입 |
| [modal-loader](../../frontend/js/modal-loader.js), [modal fragment](../../frontend/pages/components/modal.html) | fragment의 중복 HTML/head/script 제거, detail을 shell에 연결, 기존 close 동작을 보존한 dialog 향상 |
| [drilldown](../../frontend/js/drilldown.js), [actions](../../frontend/js/platform/actions.js) | 공통 상태 UI·응답 세대 guard, 기존 click action의 Enter/Space 지원 |
| [main](../../frontend/js/main.js), [search](../../frontend/js/search.js), [page-context](../../frontend/js/page-context.js) | 공통 loading/empty/error/retry 실제 사용, 기존 오류 안내 semantics |
| [timeline](../../frontend/js/timeline.js), [network](../../frontend/js/network.js), [utils](../../frontend/js/utils.js) | chart/heatmap/군집 테마 토큰 및 공통 avatar 색 사용. 산식·집계·정렬 유지 |
| [frontend index](../../frontend/index.html), 각 `frontend/pages/*.html` | theme bootstrap, 한국어 문서 lang, 파일 tree 레이아웃 class, workspace shared bootstrap. 화면 기능/IDs 보존 |
| [remote-compat E2E](../../desktop/tests/remote-compat.e2e.ts) | 기존 sandbox 페이지 검증에 셸·맥락·테마·resize·detail/모달/툴팁·요청 경쟁 검사 추가 |
| [metric display test](../../tests/frontend/test_metric_display.cjs) | 공통 DOM state를 사용하는 상세 UI에 맞게 최소 DOM 대역 보완. 기존 지표 의미 assertion 보존 |

기존 Phase 1 문서와 미추적 사용자 보고서를 덮어쓰지 않았다. `docs/index.md`에 이 보고서 링크를 추가했다. `desktop/dist`는 기존 build script로 생성했으며 직접 편집하지 않았다. 자산 inventory가 `/web/css`, `/web/js/ui`를 자동 포함하므로 build/protocol allowlist 변경은 필요하지 않았다.

## 컴포넌트 사용 규칙

- Button/Input/SearchInput/Select는 native element를 공통 스타일로 사용한다. `btn-primary/btn-secondary/rbtn` 기존 class도 공유한다. 인증이 필요한 native button은 실제 disabled 속성과 aria 상태를 함께 설정한다.
- Tabs는 기존 페이지 이동을 그대로 유지하는 `sub-tabs` navigation과 기존 `range-btns` 토글을 사용한다. 페이지 이동을 ARIA의 단일 문서 tabpanel로 잘못 표시하지 않는다. clickable legacy element는 role/button·tabindex·Enter/Space를 보완한다.
- PageHeader는 기존 `.dash-head/.dash-title/.dash-sub`를 공통 크기/표면으로 연결하고 `.dash-title`에 heading semantics를 부여한다. 새 화면은 `.ui-page-header`를 사용할 수 있다.
- Panel은 기존 `.panel/.metric-card`를 공유한다. 상세 panel은 기존 `drilldown-modal` ID와 내부 back stack을 유지한다. 1280 CSS px 이상에서 오른쪽, 그 미만에서는 main을 대체한다.
- Loading/Empty/Error는 `createState({kind,title,message,retry})`의 실제 DOM이다. key/입력 문자열은 HTML로 해석하지 않는다. loading은 live 상태·busy/spinner/장식 skeleton, error는 alert와 실제 retry callback을 제공한다.
- Tooltip은 `data-tooltip`과 hover/focus, aria-describedby·Escape·scroll 위치 갱신을 제공한다. 표시된 내용만 설명하며 동작을 tooltip에만 숨기지 않는다.
- 기존 class 기반 Modal은 열 때 title focus, modal 배경 inert, Tab 순환, Escape를 기존 close 함수에 연결한다. 닫기는 분석/리뷰 취소를 뜻하지 않는다. 넓은 창의 detail은 nonmodal로 main 접근을 유지한다.
- `createState/observeActions` 등은 현재 화면에서 사용한다. 별도 UI 프레임워크, 사용하지 않는 component catalog, 제품용 가짜 데이터/동작 없는 메뉴는 추가하지 않았다.

## 토큰과 레이아웃 정책

Light: canvas `#F5F7FA`, surface `#FFFFFF`, text `#172033`, secondary `#4B5B70`, border `#CCD5E0`, accent `#1D4ED8`.

Dark: canvas `#11161D`, surface `#1A222D`, text `#E6EDF5`, secondary `#B0BED0`, border `#425269`, accent `#8CB7FF`. Primary action은 흰 글자와 `#2563EB`를 사용한다.

상태(success/warning/error/info)와 chart categorical/heatmap 토큰을 분리했다. 현재 timeline·Chart.js·network 군집·활동 heatmap이 데이터 토큰을 실제 사용한다. 기존 파일 점수 시각화의 고유 색 인코딩/threshold는 분석 화면 재설계에 남겨 두었다. 색만으로 데이터 의미를 새롭게 정의하지 않았다.

Segoe UI/Malgun Gothic, 코드 Cascadia Mono/Consolas, 본문14px·control13px·보조12px·heading20px·KPI28px, weight400/500/600/700. 간격4/8/12/16/24/32px, radius6/8px, 패널은 border·overlay만 shadow, transition120/180ms와 reduced-motion 정책을 사용한다. 일부 기존 inline 크기/개별 시각화는 이번 공통 기반 이후 단계에서 정리한다.

1280px 이상 nav224px·유동 main·선택 detail320~440px, 960~1280px 미만 nav56px rail, 960px 미만 접힌 nav와 명시 닫기/외부 클릭/Escape. main/detail은 별도 scroll이고 작은 창 detail이 main을 대체한다. root window의 최소600×400·native 제목줄·기존 상태 보존 정책은 변경하지 않았다.

테마 preference 키는 `gitviz.ui.theme`이며 비민감 UI 설정만 저장한다. 키/토큰/동의/분석 데이터를 저장하지 않는다. 선택 변경은 Chart.js instance도 즉시 갱신한다. 새 문서에서도 선택이 유지되고 system은 OS 설정을 따른다.

## 기존 기능에 미친 영향

분석 HTTP 요청·snapshot 고정·기간·계산 버전·수집/근거 상태·정렬·AI 동의/멱등/키 보관·로컬 승인/검증 흐름은 유지했다. 원격 화면의 기존 콘텐츠를 새 main으로 옮기므로 DOM ID·canvas·event handler를 다시 만들지 않는다. 원격 shell은 추가 분석 API를 호출하지 않고 기존 제목/문맥 DOM을 관찰한다.

작업 중 필요한 최소 보정:

1. 검색의 등록 목록 요청200건을 API 허용100건으로 맞췄다. 기존 결과 ID 미확정 시 자동 POST/재분석 fallback을 제거하고 안내한다. 서버/API 응답 형식은 바꾸지 않았다.
2. 기존 상세는 늦게 끝난 요청이 새 선택을 덮을 수 있어 render generation을 추가했다. 닫힌 panel과 이전 선택의 응답을 폐기한다.
3. 전역 AI 진입은 repo만 전달하고 이전 PR/input/job을 전달하지 않는다. AI에서 repo를 선택/변경하는 같은 문서 흐름에도 sidebar를 갱신한다.
4. modal fragment의 완전한 문서 wrapper/중복 navbar·main script를 제거해 공통 component fragment로 정리했다.

## 실행한 검증

| 검증 | 결과·범위 |
|---|---|
| `npm --prefix desktop run build` | 통과. tsc main/preload와 기존 asset build. 설치기 빌드 아님 |
| `npm --prefix desktop run typecheck` | 통과 |
| `npm --prefix desktop run lint` | 통과. 기존 desktop src/contracts/tests/scripts/ui 범위 |
| 신규 `frontend/js/ui/*.js` ESLint | 5파일 통과. 기존 설치된 ESLint/recommended와 browser globals 사용, 새 설치 없음 |
| `npm --prefix desktop test` | 48/48 통과. 제한 실행에서는 임시 파일 게시/보호 관련2개가 STORAGE_UNAVAILABLE; 같은 suite를 실행 권한 확보 후 재실행해 모두 통과. 제품 암호화 코드는 수정하지 않음 |
| `node scripts/test_viz_modules.cjs` | 통과: URL·format·policy·poll |
| `node scripts/test_metric_display.cjs` | 통과: null/0/표본·집계·기존 지표 설명 |
| `node scripts/test_workspace_navigation.cjs` | 통과: local↔remote context 경계 |
| `node scripts/test_ai_frontend.cjs` | 39/39 통과: 기존 AI 상태·동의·멱등·transport |
| 실제 Electron `remote-compat.e2e` | 통과. 7개 기존 원격 화면 + root workspace, nav 선택/URL repo·snapshot, 두 테마·문서 이동 유지·chart 갱신, 요청 크기600×400/900×640/1280×800/1920×1080의 실제 CSS viewport 전환, 상세 공간, 역순 응답, modal Tab/Escape/복귀·tooltip 이벤트 |
| 실제 Electron `workspace.e2e` | 통과. 실제 앱 protocol 문서간 이동, repo/snapshot hints, 자동 연결/실행 없음 |
| 문서·diff 검사 | 보존 원본31개·문서85개 도달성·로컬 링크/anchor942개 통과, `git diff --check` 통과 |

Electron 검사는 격리 임시 profile·대역 HTTP로 실행했다. 실제 OAuth·GitHub 데이터 수집·유료 AI·사용자 local project 명령/쓰기 실행을 사용하지 않았다. Chromium sandbox/CSP/contextIsolation을 끄지 않았으며 원격 문서에서 workspace가 노출되지 않음을 확인했다. `INVALID_REQUEST` main 로그1건은 금지된 bridge 요청을 의도적으로 검증한 것이다.

최종 resize 검사는 hidden fixture의 background throttling을 끄고 요청한 실제 viewport 너비에 도달한 후 검사했다. 이는 테스트 창에만 적용하며 제품 window 설정은 유지한다. 별도 개발 앱 진입도 시도했지만 정상 대화형 창을 확인하지 못해 일반 main/agent 시작 성공으로 기록하지 않는다. 앱 실행 확인은 새 build 자산을 로드한 실제 Electron renderer 회귀 범위다.

검증용 결과/로그는 로컬 `.validation/ux-phase2/`에 있으며 제품 데이터나 배포 artifact가 아니다. 자동 `capturePage`는 숨긴 창의 오래된 frame 또는 `UnknownVizError` 때문에 최종 시각 검증 증거로 채택하지 않았다. 툴팁의 focus 이벤트는 hidden fixture에서 명시적으로 발생시켜 검사했다. 자동 검사 통과를 실제 DPI 전체·screen reader 사용성·완전한 육안 검증으로 보고하지 않는다.

## 남은 문제와 다음 단계

- 독립 개요, 활동/기여자 탭 재설계, graph 탐색·파일 목록 재배치, AI/키 설정 이동은 다음 단계다. 기존 상세 내용·분석 sub-tabs는 임시 연결이며 완성된 최종 화면으로 간주하지 않는다.
- API 저장소 목록 상한100 밖의 ID 조회 문제는 안내로 처리한다. 전체 resolve API나 검색 pagination은 별도 계약 작업이다.
- 그래프 keyboard 대체 표, 모든 inline 색/크기, 전체 contrast/screen reader·Windows DPI100~200% 검증은 남아 있다. 새 navigation/modal focus 기반은 마련했지만 앱 전체 접근성 인증은 아니다.
- 로컬 agent가 없는 개발 실행과 서버 origin 미설정 환경은 준비/연결 실패 상태가 될 수 있다. UI 작업이 기존 agent 패키징·실서버 준비를 해결한 것은 아니다. 실제 성공 데이터와 대용량 성능은 별도 환경에서 검증해야 한다.
- 배포 ASAR/설치기를 다시 만들지 않았으므로 기존 설치 앱은 이 변경을 포함하지 않는다. 현재 결과는 소스와 개발 빌드 기준이다.

이번 작업은 공통 기반에서 종료한다. 다음 구현은 이 토큰·셸·패널·상태 규칙을 사용해 주요 화면을 순차적으로 재설계한다.

## 후속 보완과 화면 확인 — 2026-10-29

사용성 재평가 후 아래를 추가 적용했다. 이 절의 상태가 앞 절의 해당 레이아웃·캡처 제한 설명보다 최신이다.

- 기본900px 폭에서56px 탐색 rail을 유지하고720px 미만에서만 완전히 접는다. 넓은 창의 sidebar는208px이다. 글꼴에 따라 달라지는 문자 아이콘을 공통 SVG로 교체했다.
- 기존 분석 상단 탭을 제거해 sidebar를 저장소 화면 탐색의 단일 진입점으로 삼았다. 기존 제목/메타데이터 DOM은 값 갱신용으로 보존하되 공통 페이지 heading과 중복 표시하지 않는다.
- 조회/수집/근거 상태와 계산 정책은 계속 표시한다. 구체 사유·기간·SHA 등은 같은 배너의 native details에 보존하고, 정상 상태에서 배너를 한 줄로 줄였다. legacy 버전 경고는 지속 표시한다.
- 작은 보조 글자를12px 토큰에 맞추고, heatmap 이름·주 셀 크기를 늘렸다. KPI를 기본 폭에서4열로 배치하고 보조 문구를 줄였다. 그래프 높이는 viewport에 따라240~440px로 제한했다.
- 검색 toolbar/filter와 AI의 기존 탭에 공통 class를 실제 연결했다. 기존 GET/리뷰/동의/결과 처리 동작을 재구현하지 않았다.
- 시각 검수에서 `intro.css`의 전역 `:root` 색상이 기존 alias를 덮는 것을 발견했다. 소개 페이지의 전역 색상·body/reset 중복을 제거해 다크 모드 숫자·상세 표·배너 대비를 복구했다. 이는 기존 body 배경 검사만으로 확인하지 못했던 문제다.
- 버그 수정 메시지 분류 비율을 자동 오류색으로 강조하지 않도록 중립 숫자로 표시하고 명칭을 명확히 했다. 분류·계산식·응답값은 그대로다.

[ui-preview.e2e.ts](../../desktop/tests/ui-preview.e2e.ts)는 실제 bundled HTML/JS/CSS와 typed desktop bridge를 사용하며, offscreen software paint로 최신 frame을 PNG로 저장한다. 기존 숨긴 창의 `capturePage` 오류를 피하는 테스트 전용 경로이며 제품은 이 fixture를 import하지 않는다. 다크 모드 KPI 글자와 배너 배경의 실제 computed color도 검사한다. 미리보기 수치는 명시된 검증용 데이터이고 실제 GitHub 분석 성과가 아니다.

화면 파일은 로컬 `.validation/ux-refinement/activity-900-light.png`, `activity-900-dark.png`, `detail-1280-dark.png`다. 앱 콘텐츠 영역을 렌더링했으며 OS 제목줄 캡처나 실서버 연동 증거로 해석하지 않는다. 기존 renderer 회귀에는 rail 너비·중복 탭 제거 검사를 추가하고 실제 sidebar 링크로 화면 이동을 검사한다.
