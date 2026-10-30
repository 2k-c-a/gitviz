# 단계별 구현 계획과 검증 기준

기준: `c459f29` · 2026-10-29 · **향후 계획이며 이번 단계에서 구현하지 않는다.**

[프로젝트 진단](project-audit.md) → [문제 목록](ux-problems.md) → [정보 구조·디자인](information-architecture.md) → [와이어프레임](wireframes.md)

## 0. 범위·의존성·변경 원칙

이번 Phase 1 산출물은 문서 5개와 기존 문서 안내의 추가 링크뿐이다. 아래 단계는 다음 작업을 위한 실행 명세이며 변경 승인을 이미 받은 구현 결과로 취급하지 않는다. 기술 스택·계산식·API·데이터 모델을 바꾸는 일을 UI 작업에 몰래 포함하지 않는다.

후속 작업은 기준 커밋 이후 변경과 로컬 사용자 수정부터 다시 비교해야 한다. 지금의 미추적 `Claude outputs/` 보고서는 삭제/덮어쓰지 않는다. existing frontend를 desktop build가 복사한다는 사실을 고려해 `desktop/dist`·`out`을 직접 편집하지 않는다. 원본 수정→기존 build→패키지 점검 순이다.

```text
1 공통 디자인·앱 골격 ─┬─ 2 탐색·개요 ─ 3 활동·기여자 ─ 4 협업·파일
                      └─ 5 리뷰·설정 (현재 repo/세대 규칙은 2에 의존)
2~5의 기능 회귀·접근성·창 크기 검사는 각 단계에서 실시
2~5 완료 + 계약 의존 작업 결정 → 6 전체 과제 검증·마감
```

공통 정확성 이슈 U02/U12/U13은 관련 화면 구현 전에 재현하고 해당 단계에서 해결한다. 예쁜 화면을 완성한 다음에 의미/포커스/상태를 보정하는 순서로 두지 않는다.

## 1. 공통 디자인 시스템과 앱 레이아웃

**구현 목표:** U01/U11/U12/U15/U16 해결 기반. 같은 nav/header/context/status/detail 구조와 light/dark/system 토큰을 원격·로컬에 적용한다. shell은 보안 경계를 유지한 시각/상태 계약의 통일이다.

| 작업 | 수정 대상(예정) | 기존 기능 영향 | 선행 의존성 | 완료 조건 | 테스트 방법 | 예상 위험 |
|---|---|---|---|---|---|---|
| 1A. 토큰·controls·table·async 상태 | `frontend/css/root.css`, `main.css`, `bar.css`, `index.css`, `desktop/ui/styles.css`; 필요 시 신규 공통 CSS/JS 모듈 | 모든 화면의 크기·테마·포커스 | IA 토큰·W 공통 검수표 | semantic token 한 체계, 기본13~14px, 숫자/결측/경고 구분, control focus 표준 | 컴포넌트 fixture와 두 테마·대비 측정·reduced motion | 기존 inline style 우선순위/차트 hardcoded color와 충돌 |
| 1B. App shell·nav·작업 상태 | `frontend/pages/components/navbar.html`, `js/navbar-loader.js`, `main.js`, `routing.js`, `pages/workspace.html`, `js/workspace/*` | legacy 전역 함수/data-action·초기화 순서·화면 전환 | 1A, 원격/로컬 route 명세 | 현재 mode/repo 표시, 600px에서 메뉴/CTA 도달, 기존 local 섹션이 목적별 뷰에서 접근 가능 | navigation 대역/Electron, 기본900×640·최소600×400, Alt+Left 의미 확인 | 숨긴 섹션 DOM 제거 시 기존 JS id 참조 깨짐. 한 번에 제거하지 않기 |
| 1C. detail/요청/포커스 계약 | `js/drilldown.js`, `pages/components/modal.html`, `js/page-context.js`, `js/viz-state.js`, `js/platform/actions.js` | 상세 stack·back·focus·URL 보존 | 1A/1B | 이전 응답 덮어쓰기0, opener 복귀, native button/link, 탭 키 규약 | 지연/역순 응답 fixture, Tab/Enter/Escape, keyboard-only 열기/닫기 | account/snapshot 변경 시 캐시 혼입, history 이중 push |
| 1D. packaged 자산 연결 | `desktop/scripts/build.mjs`, 필요 시 `src/app-protocol.ts`의 자산/문서 allowlist | 빌드/배포된 경로·CSP | 1B의 파일 경로 확정 | 새 UI가 실제 build에 포함되고 기존 경로 열림 | 기존 build·bundle audit·sandbox tests, 패키지 자산 비교 | 신규 route 추가 시 preload root 조건 우회 또는 브리지 과노출 |

**단계 종료 gate:** shell 새 도입 후에도 원격 typed transport와 root workspace bridge 범위를 유지하고 기존 기능 버튼을 도달 가능하게 한다. route가 달라져 API 전달을 늘려야 하면 UI-only 완료로 숨기지 않는다.

## 2. 레포지토리 탐색 및 분석 대시보드

**구현 목표:** U02/U04/U05/U06/U16. W01~03으로 기존 결과 열기·새 분석·진행 조회·개요를 구별한다.

| 작업 | 수정 대상(예정) | 기존 기능 영향 | 선행 의존성 | 완료 조건 | 테스트 방법 | 예상 위험 |
|---|---|---|---|---|---|---|
| 2A. 검색/최근·내 분석 통합 | `frontend/index.html`, `pages/search.html`, `myprojects.html`, `js/search.js`, `myprojects.js`, `utils.js` | 기존 검색 카드·URL 입력·로그인·분석 시작 | 1B, repository ID 해결 정책 | `limit=200` 불일치 해소, 조회 클릭이 POST하지 않음, 미확정/진행/결과없음 구분 | 기존 repo/목록상한/권한불가 fixture, HTTP method 기록, 검색0·429·네트워크 | 조회 전용 ID 매핑이 현 목록 범위에서 불가능할 수 있음 → 별도 계약 gate |
| 2B. 진행 패널·작업 재진입 | `js/modal.js`, `poll.js`, 공통 status UI, `main.js`, `js/workspace/inbox.js` 연계 | polling/backoff·stale 교체·마지막 성공 결과 | 1C, 2A 접수 동선 | 닫기≠취소, re-entry GET, 완료 ID 확정, stage/조회실패/작업실패 분리 | fake timer·429 Retry-After·401·응답유실·닫기/재열기 | 화면마다 poller 중복/서버 부하, 새 분석 자동 생성 |
| 2C. 개요 화면 | `pages/timeline.html`, `js/timeline.js`, `api.js`, `page-context.js`, 필요 신규 overview 모듈 | 기존 timeline 내용을 summary와 detail로 분리 | 1A/1B/1C, 2A snapshot context | W03 KPI·활동·파일/사람 링크, 부분 panel 실패 복구, 기간/버전 상시 표시 | sample0/null/partial/legacy snapshot fixture, 하나 endpoint 실패 | 잘린 API 목록을 전체 통계로 집계하거나 기본180일을 전체 이력으로 오인 |
| 2D. 저장소 내 AI 진입 | `navbar-loader.js`, `ai-state.js`, page context | 전역 AI 재선택 정책 | 2A repo 확정 | repo 있으면 보존, 없을 때만 picker; 다른 repo의 input/job 초기화 | current repo→AI→back, 계정/저장소 변경 | 분석 snapshot을 AI head로 잘못 전달 |

**단계 종료 gate:** A/B/C 과제를 keyboard로 수행할 수 있고, 이미 존재하는 결과를 여는 요청은 작업을 만들지 않는다. 새 개요가 없어져도 기존 snapshot URL을 열 수 있는 redirect/호환 정책이 있어야 한다.

## 3. 개발 활동과 기여자 탐색

**구현 목표:** U07/U10/U12/U13. W04/W05로 시간/사람 탐색을 명확하게 한다.

| 작업 | 수정 대상(예정) | 기존 기능 영향 | 선행 의존성 | 완료 조건 | 테스트 방법 | 예상 위험 |
|---|---|---|---|---|---|---|
| 3A. 활동 차트·유형·표 대체 | `pages/timeline.html`, `js/timeline.js`, 공통 chart style | Chart.js 3축 혼합·도넛·히트맵 표시 변경 | 2C, 1A | 단위별 분리 차트, 동일 x축, 표시 범위와 분석 기간 구별, 숫자 표 제공 | sparse 월·무변경·월 경계·색각/키보드, 기존 응답과 숫자 대조 | smoothing이 관측값을 과장, summary null과 monthly0 의미 혼동 |
| 3B. 기여자 목록·상세 | `js/timeline.js`, `drilldown.js`, `api.js`, 새 panel component | top-contributors/owned-files/collaborators limit 사용 | 1C, 2C | D 경로에서 필터/선택/scroll 유지, 수신상한 표기, 지분 분모 설명 | 긴 이름·계정미연결·동률·100명 heatmap 상한, 역순 응답 | 전체 기여자 API pagination 부재; '수신 목록 검색'을 전체 검색처럼 표현 |
| 3C. 데이터 quality 표현 | `policy.js`, `fmt.js`, `page-context.js`, tooltip/legend | 정의/문구·결측 표현 | 3A/3B | 기존 formula/version 의미 보존, 0/—/low_support 구분 | `test_metric_display`, `test_viz_modules`와 데이터 fixture 비교 | 간결화 과정에서 핵심 한계/분모 삭제 |

**단계 종료 gate:** C/D 과제와 기본 창에서 chart/표의 단위·전체/부분 범위를 읽고 사람→파일→사람을 맥락 손실 없이 복귀한다. 원격 per-contributor commit feed 등 없는 API를 전제로 하지 않는다.

## 4. 협업 네트워크와 파일 분석

**구현 목표:** U08/U09/U11/U12/U13. W06/W07의 그래프·표·상세 선택을 연결한다.

| 작업 | 수정 대상(예정) | 기존 기능 영향 | 선행 의존성 | 완료 조건 | 테스트 방법 | 예상 위험 |
|---|---|---|---|---|---|---|
| 4A. 협업 toolbar·방향·pair table | `pages/network.html`, `js/network.js`, `api.js`, 공통 graph controls | D3 force·node click·top-pairs | 1C, 3B | search/표시상한/fit/zoom/정지, 선택 이웃, 방향값/전체metrics 구별, pair keyboard 탐색 | nodes0/edges0/50·200·1000(측정 후)·긴이름·고립·부분수집 fixture | force 성능/overlap, 전체 고립값과 표시 degree 혼동, API 상한 넘어 검색 |
| 4B. 파일 목록·계층·공동 변경 탭 | `pages/code-analysis.html`, `js/code-analysis.js`, 공통 table·CSS | 현재 tree/list/contribution/coupling 패널 재구성 | 1A/1C, 3C | 파일 목록 우선, path 유지, 정렬/결측 규칙 동일, 상한·실험 IDF 표시 | 큰 tree·긴경로·동명·빈필터·null·IDF>1, 기본/최소 창 | 전체 tree DOM 비용, 일부 후보만 가져와 전체 정렬처럼 표시 |
| 4C. 파일 상세·사람/관련 파일 왕복 | `js/drilldown.js`, `api.js`, panel context | file detail·ownership·coupled candidates | 3B, 4B | F 경로, PR/commit 단위·양방향 분모/support, snapshot SHA 링크 | commit fallback/PR partial/대규모제외/기여미식별·빠른 선택·404 | score를 결함 확률, co-change를 static dependency로 오인 |
| 4D. 분석 이력/비교 탐색 연결 | `pages/history.html`, `js/history.js`, `viz-state.js` | 기존 snapshot 비교/커서 | 2C, 4C | 이전/이후 기준 표시, 같은 버전 규칙, 기준 snapshot별 상세 | 결과0·1개/동일결과/기간차이/version mismatch/rename | 비교 값의 상대 정규화 의미 손실 |

**단계 종료 gate:** E/F와 모든 정렬 정책에서 숫자·정렬·한계가 기존 응답과 일치한다. 실제 import 분석·시간순 파일 feed는 UI 단계에 추가하지 않는다. 대용량 성능은 baseline을 먼저 측정하고 그래프 안정화/선택 지연과 표 탐색 시간을 별도 기록한다.

## 5. AI 코드 리뷰 및 설정

**구현 목표:** U05/U14/U15/U16. W08/W10/W11에서 원격/로컬의 동의·credential 경계를 보존하며 위치와 흐름을 개선한다.

| 작업 | 수정 대상(예정) | 기존 기능 영향 | 선행 의존성 | 완료 조건 | 테스트 방법 | 예상 위험 |
|---|---|---|---|---|---|---|
| 5A. PR·commit 리뷰 단계/결과 배치 | `pages/ai-review.html`, `js/ai-review.js`, `ai-state.js`, `ai-api.js`, `ai-poll.js`, `css/ai-review.css` | input/job route·선택·동의·결과/피드백 | 1C, 2D | G 전체, 같은 멱등 key 복구, model/input 변경 동의 해제, 결과0/불명/미검증 구분 | 기존 `test_ai_frontend`와 새 화면 E2E: PR/SHA·만료·stale·비용한도·취소·응답유실 | UI 이동 중 동의/키버전 stale, 자동 재실행·중복 비용, 공유결과 쓰기 노출 |
| 5B. 설정과 key관리 이동 | `ai-review.js`의 renderKeys 분리, 새 settings UI/module, `navbar-loader.js`, 필요 build/protocol | 기존 key 등록·교체·삭제·preferred profile | 1B, 5A return route | H 전체, 원격 서버 저장/일회 main 보관/키불필요 구분, 복귀 preview 재조회 | mock key값 로그/URL/storage 비노출, 거절/429/삭제 jobs 영향, registry allowed 상태 | 새 저장소를 만들어 경계를 바꾸거나 서버 키를 local에서 자동 재사용 |
| 5C. 로컬 도구 재배치·이름 연결 | `pages/workspace.html`, `js/workspace/projects.js`, `review.js`, `proposal.js`, `validation.js`, `impact.js`, `comparison.js`, `inbox.js`, `navigation.js` | 기존 읽기/리뷰/적용/검증/native 동의 | 1B, 2A의 저장소 선택 UI | W11 모든 기존 기능 접근 가능, ID입력 대신 선택, 알림은 이동만, 복구 경고 유지 | 기존 workspace/review/refactor/validation/inbox E2E, target FK/입력만료/적용복구 fixture | hidden DOM/event 초기화·IPC sender gate 파손, 전송 동의와 쓰기 승인 결합 |
| 5D. 설정·진단 상태 분류 | 기존 desktop status/auth/diagnostics UI와 새 settings | local health·remote login·업데이트 status | 1B, 5B | 서버 미설정/agent 오류/로그인필요를 근거 범위 내 구분, native 창 경계 유지 | offline/auth expired/agent unavailable/diagnostics cancel | generic error를 잘못 원인 단정, 임의 origin/editor 실행 경로 허용 |

**단계 종료 gate:** AI/credential backend·cloud bridge를 UI 편의만으로 변경하지 않는다. 로컬 영구 키 저장·서버/PC provider 통합은 별도 설계 승인이 필요한 범위다. 통합 검증은 fake provider부터 실시하고 실제 코드 전송·유료 호출은 대상/예산/동의가 확보된 별도 실행으로 남긴다.

## 6. 전체 UX 검증 및 마감

**구현 목표:** 디자인 변경의 결과를 과제·회귀·환경별로 확인하고 알려진 제한과 배포 가능 범위를 확정한다.

| 작업 | 수정 대상(예정) | 기존 기능 영향 | 선행 의존성 | 완료 조건 | 테스트 방법 | 예상 위험 |
|---|---|---|---|---|---|---|
| 6A. A~H 과제 검증 | 해당 UI, `desktop/tests/*`, `scripts/test_*frontend*`, 신규 시나리오 문서 | 전체 사용자 여정 | 2~5 | 기존결과 조회 POST0, 선택/상세 불일치0, 미동의 호출0, 맥락손실0, 주요 키보드 도달 | 실제 사용자/개발자 대상 과제, 성공·오선택·복귀·질문·시간 기록 | 도구 자동화 성공을 실제 사용성 개선과 혼동 |
| 6B. 창·DPI·테마·접근성 | 공통 CSS·chart/table/panel, 기존 E2E 보완 | 모든 화면 | 1~5 | 최소/기본/대형 창·두 테마, 주요 contrast/focus/읽기순서 충족 | 600×400/900×640/1280×800/1920×1080,100~200%DPI, keyboard+screen reader | CSS px/OS scaling 혼동, 고정 footer가 내용/포커스 가림 |
| 6C. 계약·정합성·성능 회귀 | 기존 순수 module/desktop/agent test, fixture/perf 기록 | 수치·경계·패키지 | 2~5, 의존 gate 해결 | 기준 payload 수치/범위 보존, 허용목록·CSP 통과, 대형 자료 한계 명시 | lint/typecheck/build→unit→fixture E2E→전용PG/Redis→실연동 순, 기존 suite 활용 | 테스트 fixture가 실제 endpoint 상한/오류를 숨김(U02 재발) |
| 6D. 배포본 확인·기록 | `desktop/scripts/build.mjs`, 기존 package tests, docs/validation 기록 | packaged UI/agent·설치기 | 6A~6C | 최종 소스와 ASAR 자산 일치, 실제 패키지에서 시작/복귀 검증, 미검증 범위 명시 | 새 격리 profile/clean VM의 기존 절차, 지원되는 인증 환경 | 로컬 dev만 통과·실제 build가 오래된 asset 포함, signing/update 미완료 |

P0 잔여는 일반 출시 gate를 막는다. U01 등 탐색 핵심 P1은 개선 목표에 대해 해결/남음/보류 근거를 남긴다. P2 미완료는 영향·대안·후속 범위를 기록한다. 화면이 표시되는 것만으로 완료 처리하지 않는다.

## 7. API·IPC 의존 작업과 결정 지점

아래는 현 기능과 분리한 기술 backlog다. 필요한 항목만 다음 단계에서 범위를 확정하고 별도 변경으로 구현한다.

| 항목 | 현재 제약 | UI 범위의 가능한 대응 | 별도 작업이 필요한 조건 |
|---|---|---|---|
| 검색 결과→정확한 기존 repository ID | 검색 응답 ID 없음, 목록 최대100·현 요청200 | 유효 상한·실패 표시, 미확정 시 POST 금지, 본인 목록 연계 | 등록 전체를 정확히 resolve할 조회 계약이 없으면 ID lookup/응답 필드 설계 |
| 사용자 지정 분석 기간 | 서버 start/end 가능, desktop allowlist 불가 | 기존 fixed 기간만 표시, default 새 분석 | desktop query schema·route·테스트 확장 결정 |
| 파일 시간순 commit 이력 | 현재 상세는 집계, 전용 feed 미확인 | 집계 근거·SHA 파일 링크 | 접근·snapshot 범위·rename·pagination 계약 새 설계 |
| 모든 기여자/파일 후보 탐색 | 일부 API limit, network1000/heatmap100 상한 | bounded 확장·수신 내 검색·상위N 표기 | 전역검색·대규모 pagination 필요 시 계약/성능 설계 |
| local/remote 공통 shell runtime | root만 workspace bridge 제공 | 같은 UI 모양, 구분된 privileged 실행 경계 | route 변경·bridge 노출 확장은 보안 검토·계약 회귀 |
| 저장 key 정책 통일 | 원격서버 영구키 vs local일회메모리 | 같은 설정 진입에 별도 범위 안내 | 영구 local vault/키 migration을 원하면 별도 보안 설계 |
| background 작업 목록 | remote/local/AI의 별도 lifecycle, 추적16개 | 조회 진입만 통일, 현재 관찰 범위 표기 | 상시 전체 감시·다중창 동기화 요구 시 별도 lifecycle 설계 |

## 8. 테스트 실행 목록과 권장 증거

기존 파일을 먼저 재사용한다. 이번 Phase 1에서 아래 전체를 실행했다는 뜻은 아니며 실제 실행 결과는 [project-audit](project-audit.md)에만 기록한다.

| 층 | 기존 명령/파일 | 검증 범위 |
|---|---|---|
| 문서 | `python -B docs/audit/check_documents.py` | 상대 링크/anchor·index 도달성·보존원본 해시 |
| 프론트 순수 | `node scripts/test_viz_modules.cjs`, `test_metric_display.cjs`, `test_ai_frontend.cjs`, `test_workspace_navigation.cjs` | URL·policy·폴링·AI 동의/intent·로컬 탐색 |
| desktop 정적/unit | `npm --prefix desktop run lint`, `run typecheck`, `run build`, `test` | TS/JS·계약·bridge·암호화·배포 모듈 |
| renderer E2E | `desktop/tests/remote-compat.e2e.ts`, `workspace.e2e.ts`, `review.e2e.ts`, `inbox.e2e.ts` 등 | sandbox 실제 문서와 대역 transport/agent, UI 경로 |
| agent | `python -B -m unittest discover -s tests/workspace` | 프로젝트·캡처·리뷰·적용/복구·검증·알림 |
| 서버/지표 | `scripts/test_analytics_calculations.py`, `test_metric_policy.py`, `test_repository_snapshots.py`, `test_ai_contracts.py`, `test_desktop_auth.py` | 수치·기간·immutable payload·권한·AI 계약 |
| 실연동·패키지 | 기존 staging/clean VM 프로토콜 | OAuth 승인·원격 결과·agent·실제 창·패키징 일치 |

E2E는 `desktop/tests/profile.ts` 방식의 격리 profile을 유지한다. 서버 DB/큐 테스트는 전용 test 환경과 fixture를 사용한다. 전체 suite가 파일 적용·명령 실행·DB 쓰기를 포함할 수 있으므로 테스트 이름만 보고 사용자 실제 자료에서 실행하지 않는다.

증거에는 commit/dirty 상태·환경·명령·exit·실제/대역 구분·데이터 규모·화면 크기/DPI·실패 재현 단계를 남긴다. 성능 예산은 현재 기준을 먼저 측정해 확정하고, '50명 잘 보임'을1000명 보장으로 쓰지 않는다. UX 과제 시간 개선율은 baseline 확보 후 산정한다.

## 9. Phase 1 인수 조건과 남은 불확실성

- 문서 5개가 기준 커밋·근거·미확인·제안을 분리하고 서로 연결된다.
- A~H, 모든 요청 기능, 로컬 확장 기능 보존, 화면 상태/resize/keyboard가 설계에 포함된다.
- 구현 작업은 파일·기능 영향·선행 작업·완료 조건·시험·위험으로 쪼개져 다음 단계에서 착수 가능하다.
- 소스·패키지·DB·API·모델 변경이 없고 기존 문서 본문을 대체하지 않는다. 기존 문서 안내에는 추가 링크만 둔다.
- 기존 패키지의 실제 빈/실패 화면은 확인했다. 성공한 원격 결과·실제 모델 응답·모든 DPI/크기·실제 사용자 사용성은 미확인이다. 로컬 준비 실패의 원인은 이번 UX 감사에서 진단/수리하지 않는다.
- 배포본 전체의 commit 재현성·공개 서명·업데이트 준비는 별도 기술 위험이다. 문서/순수 테스트 통과로 배포 완료를 선언하지 않는다.

다음 단계의 첫 구현 단위는 **1A~1C 공통 shell·component·상태 계약과 2A의 결과 조회 정합성**이다. 이번 단계는 이 문서화와 진단에서 종료한다.
