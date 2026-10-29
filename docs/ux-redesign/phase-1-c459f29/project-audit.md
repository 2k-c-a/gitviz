# GitViz 프로젝트 진단 — Phase 1

검토일: 2026-10-29 (Asia/Seoul) · 기준: `c459f29b14f36511cc3429141a399e6c370709d7`

## 범위와 읽는 순서

요청한 커밋과 조사 시점 HEAD가 일치한다. 추적 소스 변경은 없었고, 기존 `Claude outputs/` 미추적 보고서 5개는 보존했다. 이번 작업은 진단과 설계이며 코드·패키지·DB·API·분석식을 변경하지 않는다. 기존 문서는 시점이 서로 달라 소스를 우선했다. 루트·상위 경로 조사와 파일 목록에서 적용할 AGENTS.md는 발견하지 못했다.

- [기존 UX 문제](ux-problems.md)
- [정보 구조·사용자 흐름·디자인 방향](information-architecture.md)
- [화면별 와이어프레임](wireframes.md)
- [단계별 구현 계획](implementation-plan.md)

**확인**은 기준 커밋 소스·설정 또는 아래 명시한 실행 관찰이다. **추론**은 사용자 영향에 대한 전문가 가설이다. **제안**은 미래 설계이며 현재 구현이 아니다. **미확인**은 인증·데이터·환경 또는 검증 범위 때문에 판단하지 않은 항목이다. 사용성 실험을 실시하지 않았으므로 소요 시간·불편 빈도를 측정값처럼 쓰지 않는다.

## 1. 기술과 실행 구조

| 영역 | 확인한 구현 | 주요 근거 |
|---|---|---|
| 원격 분석 UI | 프레임워크 없는 HTML/CSS/ES modules. Chart.js 4.4.1, D3 7.8.5를 로컬 vendor로 제공 | [frontend](../../../frontend/index.html), [공통 초기화](../../../frontend/js/main.js), [CSS 진입점](../../../frontend/css/index.css) |
| Windows 클라이언트 | Electron 45.0.0, TypeScript 6.0.3, Electron Forge 8.0.1, Squirrel 사용자별 설치 | [package](../../../desktop/package.json), [Forge 설정](../../../desktop/forge.config.ts) |
| 데스크톱 빌드 | tsc 두 설정으로 main/preload 컴파일, build.mjs가 정적 자산·계약 복사. React/Vite 빌드는 이 경로에 없음 | [build.mjs](../../../desktop/scripts/build.mjs), [tsconfig](../../../desktop/tsconfig.json) |
| 서버 | Python, FastAPI, Uvicorn, SQLAlchemy, Alembic, PostgreSQL, Redis/Celery, GitPython, NetworkX | [requirements](../../../requirements.txt), [main](../../../app/main.py), [compose](../../../docker-compose.yml) |
| 로컬 기능 | Python workspace agent를 별도 exe로 실행, stdio 계약 통신, SQLite 저장소·암호화 자료. 원격 서버와 별개 | [agent](../../../desktop/src/agent.ts), [workspace 진입점](../../../app/workspace/__main__.py), [store](../../../app/workspace/store.py), [패키징](../../../workspace_agent.spec) |
| 보안 경계 | sandbox/contextIsolation, Node renderer 차단, 고정 IPC·AJV 계약, 자산 allowlist·CSP. HTTPS 서버 origin은 main 환경 설정 | [window](../../../desktop/src/window.ts), [preload](../../../desktop/src/preload.ts), [protocol](../../../desktop/src/app-protocol.ts), [remote 계약](../../../desktop/src/remote-contracts.ts) |

Python 3.12는 README 실행 전제이며 모든 requirements가 각 Python 버전에서 검증되었다는 뜻은 아니다. Node 엔진은 `>=24.20.0 <25`다. 버전은 최신 제품 정보가 아니라 이 커밋의 고정 설정이다.

```text
Electron main.ts
 ├─ gitviz://app/index.html → 로컬 workspace renderer
 │   └─ window.workspace → preload → IPC → Python agent → 로컬 Git/SQLite/암호화 자료
 └─ gitviz://app/web/... → 패키징된 기존 원격 분석 UI
     └─ window.desktop.remote → typed endpoint → main HTTPS → FastAPI
         ├─ PostgreSQL: 저장소·분석 작업·불변 스냅샷·AI 작업/결과/키
         └─ Redis/Celery → Git 수집/GitHub API → 지표 계산·AI provider

웹 실행: 같은 frontend → same-origin fetch → FastAPI
```

중요: `desktop/ui/index.html`은 준비 상태용 원본이지만 실제 빌드에서는 `frontend/pages/workspace.html`이 `dist/ui/index.html`로 덮여 패키징된다. 전자의 간단한 화면만 보고 앱을 진단하면 실제 화면을 놓친다. `test-react/`, `build/`, `dist/`, `.validation/`, `outputs/`, `reports/`의 존재만으로 제품 렌더러나 배포 상태로 인정하지 않았다.

## 2. 폴더와 책임

| 경로 | 책임 |
|---|---|
| `frontend/pages`, `js`, `css`, `vendor` | 원격 분석 화면·동작·스타일·차트 라이브러리. `js/workspace`는 로컬 작업 화면 모듈 |
| `desktop/src`, `contracts`, `scripts`, `tests` | main/preload·OS 연동·원격 브리지·계약·자산 패키징·Electron 검사 |
| `app/api`, `routers` | 분석/스냅샷/AI/인증 HTTP 경로 |
| `app/collectors/git`, `github`, `identity` | clone·이력·파일 변경, REST/GraphQL PR·리뷰·릴리즈, 신원 결합 |
| `app/tasks`, `services`, `models`, `schemas` | 비동기 수집/AI 작업·지표·권한·저장·검증 |
| `app/workspace`, `tests/workspace` | 로컬 프로젝트·변경 캡처·리뷰·제안 적용/복구·영향·검증·알림 |
| `contracts/workspace/v1`, `contracts/desktop/v1` | 로컬 IPC wire 형식, 원격 AI 요청 body 계약 |
| `migrations` | 서버 Alembic schema. 로컬 SQLite migration은 workspace 모듈 |
| `scripts`, `.github/workflows` | 회귀·연동·VM·패키징·검증 스크립트/워크플로 |
| `docs` | product/architecture/contracts/operations/migration/validation/audit/archive 문서 체계 |

새 문서는 기존 문서군과 구별되는 `docs/ux-redesign/phase-1-c459f29/`에 둔다. 기존 migration 설계를 현재 구현으로 복사하지 않는다.

## 3. 진입점·내비게이션·상태·공통 스타일

- **확인:** [main.ts](../../../desktop/src/main.ts)는 단일 인스턴스와 `APP_URL` 진입, agent 시작·원격 세션 복구를 관리한다. [window.ts](../../../desktop/src/window.ts)의 기본 창은 900×640, 최소 600×400이며 창 위치·크기·최대화 상태를 저장한다.
- **확인:** 원격 화면 이동은 [navbar-loader.js](../../../frontend/js/navbar-loader.js)의 `location.href`가 주 경로다. [routing.js](../../../frontend/js/routing.js)의 `.page.active` 전환 코드도 남아 있어 탐색 방식이 혼재한다. SPA 프레임워크 라우터는 없다.
- **확인:** [viz-state.js](../../../frontend/js/viz-state.js)는 `repo/snapshot/sort_by`를 URL에서 읽고 같은 저장소 탭 이동 때 보존한다. [api.js](../../../frontend/js/api.js)는 snapshot meta를 조회해 주소에 결과 ID를 고정한다. desktop은 저장소 미선택 시 기본 저장소를 쓰지 않으나 웹에는 localStorage/default 1 경로가 있다.
- **확인:** 상태는 화면별 모듈 변수·Map·DOM·window 함수·CustomEvent로 나뉜다. 로컬 프로젝트 선택은 select change, 원격 상태 초기화는 계정 변경 이벤트로 전파한다. 중앙 상태 관리 라이브러리는 없다.
- **확인:** AI는 [ai-state.js](../../../frontend/js/ai-state.js)의 URL·guard·멱등 intent, [ai-poll.js](../../../frontend/js/ai-poll.js)의 폴링을 쓴다. 일반 분석 [poll.js](../../../frontend/js/poll.js)는 직렬 조회·오류 backoff·Retry-After·화면 세대 무효화를 구현한다.
- **확인:** 원격 공통 navbar/modal은 fetch 삽입이며, [actions.js](../../../frontend/js/platform/actions.js)는 명명된 `data-action` click만 위임한다. 공통 `repoHeader/page-context/fmt/policy/drilldown`이 있다. [root.css](../../../frontend/css/root.css) 토큰과 다수 inline style, [desktop styles](../../../desktop/ui/styles.css)의 별도 dark 테마가 공존한다.

## 4. 실제 구현 화면 목록

| 화면 | 현재 기능·연결 | 한계/구분 |
|---|---|---|
| workspace | 폴더 등록·선택, Git 상태, 고정 변경 캡처, 로컬/공개 연결 BYOK 리뷰, 리팩토링 제안 승인·적용·복구, 정적 영향, 도구 탐지·검증·전후 비교, 통합 알림, 원격 연결 | 한 긴 문서에 섹션 배치. 분석 대시보드가 아님 |
| 원격 index | URL 분석 시작, 최근 분석 요청, 소개·footer | 데스크톱에서도 웹 랜딩 구조 유지 |
| search | GitHub 텍스트 검색·정렬·상위 20건, 예비 점수·메타데이터, 분석 시작/상세 이동 | 검색 페이지네이션 UI 확인 못함. 등록 목록 요청 계약 불일치 있음 |
| myprojects | 본인 분석 프로젝트·진행·재분석·AI 진입 | 전역 메뉴에서는 사용자 dropdown 안에 있음 |
| timeline | 구간 커밋·90일 활성 기여자·릴리즈·버그 수정 분류 비율, 월별 혼합 차트, 16주/상위 8명 히트맵, 상위 5명, 릴리즈·유형 도넛 | 새로 제안하는 '개요' 페이지는 아직 없음 |
| network | 기본 상위 50노드 D3 force 그래프, 커밋 크기·군집색·허브 테두리, 상위 8쌍, 기여자 드릴다운 | 이름 hover, click/drag 중심. 방향 화살표·검색/zoom UI는 이 모듈에 없음 |
| code-analysis | 접이식 원형 디렉터리 트리, 지표별 색/정렬, 전체 파일 목록·경로 검색, 기여 집중·공동 변경 목록 | 실제 import dependency 그래프가 아니라 파일 계층/이력 지표 |
| drilldown 모달 | 기여자 주요 파일·협업자, 파일 점수 근거·수정자 지분·공동 변경 후보, 내부 stack back | 독립 기여자 상세 페이지 없음. 목록 상한 있음 |
| history | 스냅샷 목록·더 보기, 이전/이후 선택·비교 | 같은 저장소/계산 버전 기준. 경로 변경은 제외+추가 |
| ai-review | 프로젝트 선택 → PR/커밋 → 파일 → 고정 입력 → 동의 → 설명/리뷰 → 근거·피드백·이력 문맥, 팀 결과 읽기 | 키/모델 관리가 이 화면 안에 있음. 별도 앱 설정 화면 없음 |

근거: 각 [pages](../../../frontend/pages/timeline.html) HTML, [workspace](../../../frontend/pages/workspace.html), [timeline](../../../frontend/js/timeline.js), [network](../../../frontend/js/network.js), [code-analysis](../../../frontend/js/code-analysis.js), [drilldown](../../../frontend/js/drilldown.js), [AI](../../../frontend/js/ai-review.js), [history](../../../frontend/js/history.js).

## 5. GitHub 호출과 데이터 파이프라인

**확인:** [repositories.py](../../../app/api/repositories.py)의 검색은 GitHub REST search를 서버에서 호출하고 메타데이터 기반 예비 점수를 붙인다. 이는 코드 품질 평가가 아니다. 저장소 등록은 로그인·URL 정규화·현재 GitHub ID/접근 확인 후 `_trigger_analysis`로 큐에 보낸다. 등록된 URL의 POST도 작업을 시작할 수 있으므로 단순 상세 조회 대체로 쓰기 부적절하다.

**확인:** [analyze_repository.py](../../../app/tasks/analyze_repository.py)는 clone/fetch → 기본 브랜치·커밋·파일·변경 저장 → PR 목록/GraphQL 상세(파일·커밋·리뷰) → 릴리즈 → 신원 정리 → 파생 지표 → 불변 snapshot 저장 순이다. 증분 수집 cursor/buffer와 전체 수집 경로가 있다. GitHub REST client는 pagination·rate-limit 대기 콜백을 쓰며 GraphQL 상세 batch와 개별 fallback이 있다. PR 파일 상세는 complete/partial/failed 및 API cap 등의 이유를 기록한다. Git 커밋과 PR 연결을 수집했다고 모든 GitHub 계정 신원 연결이 보장되지는 않는다.

**확인:** [analysis_snapshots.py](../../../app/services/analysis_snapshots.py)의 기본 관측 기간은 180일 `[start,end)`다. 종료 시점 기본 브랜치 target SHA의 도달 가능 커밋·대상 파일 트리·병합 PR 등으로 SQL 입력을 제한하고, 계산 버전·기간·SHA·canonical payload hash를 저장한다. [SnapshotAnalyticsRoute](../../../app/api/snapshot_route.py)가 HTTP 분석 응답을 가로채므로 실제 조회는 저장된 snapshot payload를 잘라 반환한다. 페이지에서 실시간 재계산하지 않는다.

**설계 제약:** 과거 snapshot의 기간을 클라이언트 필터로 바꿔 같은 결과인 것처럼 표시하면 안 된다. 기간 변경은 새 분석이며 desktop register/reanalyze allowlist에는 현재 기간 query가 없다. 서버에 매개변수가 있다고 데스크톱 UI에서 곧바로 제공할 수 있다고 단정할 수 없다.

## 6. 지표와 해석 경계

### 커밋 분류·버그 수정 비율

[commit_classifier.py](../../../app/collectors/git/commit_classifier.py)의 제목 첫 줄에 Conventional Commits prefix 우선 → 영어 keyword(bugfix → feature → refactor) → other 규칙을 적용한다. feature/bugfix/refactor/docs/chore/other 6종이며 source/confidence를 저장한다. prefix 0.95, keyword 0.60, default 1.00은 검증된 정확도나 결함 확률이 아니다. 한국어 자연어 의미 분류 모델은 확인되지 않았다.

[analytics.py](../../../app/api/analytics.py)의 summary는 관측 커밋 중 `commit_type='bugfix'` 수 ÷ 전체 관측 커밋 ×100(소수 1자리), 분모 0이면 null이다. '버그 발생률'이나 품질 점수로 해석하면 안 된다. monthly 응답은 기존 구현에서 null 비율을 0으로 변환하는 경로도 있어 summary 결측 처리와 다르다. 이번 단계에서 산식을 수정하지 않는다.

### 파일별 변경·기여 집중·점검 우선순위

원천은 [commit_file_changes](../../../app/models/commit_file_change.py)와 [수집기](../../../app/collectors/git/commit_file_change_collector.py)다. 파일 변경 빈도·churn·bugfix 분류 횟수 등을 계산한다. 현재 v3 maintenance priority는 이 세 신호의 percentile midrank를 동일 가중 평균한 상대 점수다. 기여 집중도·공동 변경은 점수 가산 요소가 아니다.

`_ownership_analysis`는 식별된 작성자의 파일 변경 커밋 수 지분, Shannon entropy, 정규화 entropy, 누적 80%의 최소 인원 BF80을 계산한다. BF80은 실제 지식 소유 인원이 아니다. 기여 1명이면 정규화 entropy 0, 식별 관측이 없으면 null이다. 표본 5건 미만을 별도 표시한다.

**미구현 범위:** 파일 상세 API는 집계 근거를 제공하지만 파일별 커밋을 시간순 페이지네이션하는 전용 API/화면은 확인되지 않았다. 서버 파일 이력과 로컬 변경 snapshot/diff는 별도 자료다. 새 와이어프레임에서도 시간순 커밋 이력은 의존 기능으로 분리한다.

### 파일 공동 변경

`_compute_coupling`은 유효 PR 파일 자료가 있으면 PR 단위, 없으면 commit 단위로 fallback한다. snapshot은 대규모 변경 기준 `max(15,ceil(0.1×target_tree_files))`로 제외하고, 함께 변경 2회 이상인 쌍만 남긴다.

- symmetric score = 공동 변경 횟수 / min(A 변경 단위 수, B 변경 단위 수)
- A→B = 공동 변경 횟수 / A 변경 단위 수, B→A는 반대 분모
- 선택 IDF 정렬 = score × log(전체 단위/A 단위) × log(전체 단위/B 단위). 1을 넘을 수 있으며 확률 아님
- 기본은 score·support 정렬, '강함' 표시 임계 0.7, low support는 5 미만

미래 수정 영향 확률이나 정적 import 의존성의 증거로 표시하면 안 된다. 파일명 변경 추적·과거 실제 tree 크기 대신 target tree proxy 사용의 한계가 있다.

### 협업 네트워크

`_fetch_network_edges`는 같은 PR의 **같은 파일을 실제 수정한** 기여자 쌍 수를 PR 수로 정규화한 co_edit_raw와 리뷰어→작성자 리뷰 수/전체 리뷰 수를 결합한다. 방향별 weight = `0.6×co_edit_raw/2 + 0.4×review_directed`. co_edit는 여러 파일에서 집계될 수 있어 항상 0~1인 확률이라고 보장할 수 없다. PR이 없는 경우 co-edit commit fallback은 없다.

`_compute_network_metrics`는 NetworkX DiGraph, distance=1/strength의 betweenness, 양방향 강도를 합친 무방향 Louvain(seed=42)을 쓴다. top-pairs는 양방향 강도 합계다. 현재 D3는 방향별 선을 화살표 없이 그리므로 API의 방향 의미가 UI에서 드러나지 않는다. snapshot 응답은 상위 노드로 잘라도 metrics는 전체 그래프 값을 유지한다. 필터 후 보이는 그래프의 지표로 재해석하면 안 된다.

로컬 [impact](../../../app/workspace/impact.py)의 정적 parser 근거는 원격 공동 변경과 다른 경로다. 오래된 원격 함수 docstring의 'AST 없음'을 제품 전체에서 정적 분석이 전혀 없다는 뜻으로 일반화하지 않는다.

## 7. AI 키·리뷰 경로

| 구분 | 확인된 저장·사용 | UX에서 유지할 경계 |
|---|---|---|
| 원격 PR/커밋 리뷰 BYOK | AI 화면 password 입력 → credential API → provider 확인 → 서버 credential version에 암호화 저장. 화면은 hint/version 반환. 작업에 profile fingerprint/credential version 고정 | '이 PC 저장'으로 안내하면 안 됨. 교체·삭제와 대기 작업 영향 안내 |
| 로컬 workspace cloud BYOK | [cloud-review.ts](../../../desktop/src/cloud-review.ts)가 미리보기 동안 main 메모리에만 키 보관. 계정·공개 저장소 연결 proof 확인 후 고정 HTTPS provider 호출 | 원격 저장 키 자동 재사용/영구 저장을 약속하면 안 됨 |
| PC 모델 | 키 불필요. 로컬 workspace 기본 Ollama profile과 서버 provider registry의 local 실행 경로는 구별 | 서버가 원격이면 'local provider'가 사용자 PC라고 무조건 단정하지 않기. 실제 실행 위치 계약 확인 |

근거: [ai-review](../../../frontend/js/ai-review.js)의 `renderKeys`, [credential service](../../../app/services/ai/credentials.py), [crypto](../../../app/services/ai/crypto.py), [workspace review](../../../frontend/js/workspace/review.js). API 키는 입력 코드용 키와 분리한 AES-256-GCM key bundle/AAD로 서버 암호화한다. Electron 세션 및 로컬 암호화 key 보호는 safeStorage/DPAPI 경로다. SQLite 전체가 암호화된 DB라는 주장은 하지 않는다.

**확인:** 원격 리뷰는 PR 또는 전체 SHA 커밋 선택·파일 목록 → 고정 base/head/manifest 입력 생성 → 코드 미리보기·모델/키 버전·전송/비용 동의 → AI job/outbox → 준비/생성 worker → 결과/finding/위치 검증·저장 → 사람 피드백 경로가 있다. [ai-api](../../../frontend/js/ai-api.js), [AI inputs](../../../app/api/ai/inputs.py), [job service](../../../app/services/ai/job_service.py), [generation](../../../app/services/ai/generation_service.py)가 분리되어 있다. AI는 기본 disabled이며 배포 설정에 따라 404가 가능하다. 실제 모델 응답 품질은 이번에 시험하지 않았다.

고정 입력 변경·모델 변경 때 동의를 다시 받아야 한다. 멱등 key, 결과 불명·저장 실패·취소 후 비용 가능성, 인용 가능한 변경 라인·사람 미검증 표시는 보존 대상이다. GitHub 자동 댓글 게시 구현으로 확대 해석하지 않는다.

## 8. 상태 처리와 실제 실행 관찰

**소스 확인:** 일반 분석은 pending/running/rate_limited/done/failed/cancelled/stale을 구분하고, 모달 닫기는 폴링 중단이지 서버 작업 취소가 아니다. 실패 시 마지막 성공 snapshot 제안 경로가 있다. [page-context](../../../frontend/js/page-context.js)는 결과 조회·수집 범위·분석 근거 3상태를 분리하고 404/409/연결 오류를 안내한다. AI는 queued/running/retry_wait/cancel_requested/succeeded/failed/cancelled 및 outcome_uncertain을 구별한다. null·0·자료 부족의 구별을 새 UI에서도 유지해야 한다.

**직접 관찰(기존 패키지, 기본 크기):** `desktop/out/GitVizWorkspace-win32-x64/GitVizWorkspace.exe`를 새 빌드 없이 실행했다. workspace에서 긴 세로 스크롤·큰 버튼·밀집된 라벨/기본 form control을 보았다. '로컬 구성을 준비할 수 없습니다', '원격 서버 설정 또는 연결을 확인하세요'가 표시되었다. 링크로 원격 랜딩에 이동하면 밝은 테마·웹 상단 navbar·소개 hero/footer로 바뀌고 '목록을 불러올 수 없습니다'가 나타났다.

ASAR에서 읽은 `dist/ui/index.html`, `styles.css`, `web/index.html`, `web/js/search.js`를 해당 추적 소스와 바이트 비교해 모두 일치했다. `dist/src/app-protocol.js`도 현재 로컬 컴파일 산출물과 일치했다. 다만 전체 ASAR가 c459f29로 재현 빌드되었다는 증거는 아니며, 후자의 TS 소스와의 동치도 증명하지 않는다. 실행 관찰은 해당 패키지와 환경에 한정한다.

**미확인:** 로컬 준비 실패 원인(DPAPI/agent/schema/환경 중 무엇인지), 원격 서버 origin 설정·접속·인증, 성공 데이터의 실제 차트 겹침·대용량 성능, 600×400/다중 DPI/키보드 전 경로. 환경 실패를 보편적인 제품 결함으로 단정하지 않는다. 로그인·저장소 등록·분석/모델 호출·키 입력·명령 실행·파일 적용은 수행하지 않았다. `.env`·key·인증서 내용도 읽지 않았다.

## 9. 테스트·실행 방법과 이번 검증

현재 문서의 [서버 실행](../../operations/setup.md), [Windows 설치](../../operations/windows-desktop.md), [검증 절차](../../validation/protocols/document-consistency.md)를 참고한다. 아래는 향후 실행 안내이며 이번 설치·build·DB 변경을 수행했다는 뜻이 아니다.

```powershell
# 기존 의존성/agent 준비 이후 desktop
npm --prefix desktop run build
npm --prefix desktop start
# unit: build 산출물 대상
npm --prefix desktop test
# Python 로컬 workspace
python -B -m unittest discover -s tests/workspace
# 서버에는 별도 PostgreSQL/Redis, Alembic, uvicorn, Celery가 필요
```

`desktop start`만으로 agent exe를 생성하지 않는다. [agent build script](../../../scripts/build_workspace_agent.py)와 Forge의 agent 자원 경로를 확인해야 한다. 검증 프로젝트 코드를 실행하는 workspace validation은 단순 화면 검사와 별개의 승인 작업이다. DB/queue 시험은 전용 test 환경을 사용해야 하며 전체 suite를 현재 사용자 DB에 실행하지 않는다.

이번 재실행 결과:

| 명령 | 결과 | 증명 범위 |
|---|---|---|
| `node scripts/test_viz_modules.cjs` | exit 0 | URL·format·policy·폴링 순수 모듈 |
| `node scripts/test_metric_display.cjs` | exit 0 | 집계·지표 설명 회귀 |
| `node scripts/test_ai_frontend.cjs` | exit 0, 39/39 | AI 상태·동의·멱등·transport 대역 검증 |

이 결과는 시각적 품질·실제 OAuth·실제 서버/모델 통합의 통과를 뜻하지 않는다. 신규 제품 테스트는 추가하지 않았다.

문서 작성 후 `python -B docs/audit/check_documents.py`를 실행해 exit 0을 확인했다. 보존 원본31개 해시·인벤토리, 전체84개 Markdown의 index 도달성, 로컬 링크/anchor910개 검사를 통과했다. 추적 파일 diff는 `docs/index.md`의 안내 링크1행 추가뿐이며, 신규 설계 문서5개 외 애플리케이션 소스·의존성 파일 변경은 없다. 문서 검사 통과는 내용의 자연어 정확성·실연동 통과를 증명하지 않는다.

## 10. 재설계의 기술적 경계

1. 원격 UI와 로컬 privileged UI를 하나의 DOM으로 합치면 안 된다. workspace 브리지는 현재 root document에만 노출된다. 같은 디자인 shell과 두 실행 경계의 연속 탐색부터 설계한다.
2. 파일 시간순 커밋 이력·임의 기간 재계산·모든 기여자의 무제한 페이지네이션은 현 UI만 고쳐 완성할 수 없다. API/IPC 계약 의존 사항으로 표시한다.
3. 기존 계산 버전/스냅샷/소유권/공개 저장소 증명/동의 경계와 PR·commit fallback 표기를 유지한다.
4. 대용량 전체 tree·payload와 NetworkX 계산 비용, 화면 표/그래프 가독성을 구별해 측정해야 한다. UI 디자인으로 서버 성능 개선을 보장하지 않는다.
5. 배포 서명·업데이트 설정·패키지 버전과 agent protocol 버전은 별도 검증 대상이다. 이번 UX 단계가 배포 승인이나 보안 감사 완료를 의미하지 않는다.
