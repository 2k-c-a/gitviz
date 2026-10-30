# 현재 repository 조사

목적: 전환의 사실 기준과 재사용 경계를 기록한다. 범위: 현재 작업 트리의 소스·설정·문서 정적 조사. 운영 DB·외부 계정·최신 CI 실행 결과는 미확인이다. 관련: E01~E10, P01~P08. 결정·가정은 [14](14-decisions-and-open-questions.md), 통합 설계는 [08](08-system-architecture.md).

## 근거 목록

| ID | 실제 확인 파일과 관찰 |
|---|---|
| O01 | [main](../../app/main.py): FastAPI router 등록, 세션, AI 예외 handler, `frontend/` 정적 mount. 실행 중 schema 생성 없음 |
| O02 | [requirements](../../requirements.txt), [README](../../README.md): Python 3.12 안내, pip, FastAPI 0.136.3·SQLAlchemy 2.0.50·Celery 5.6.3·GitPython 3.1.50 명시. 실제 설치 버전 전수 확인 아님 |
| O03 | [compose](../../docker-compose.yml), [Celery](../../app/core/celery_app.py): PostgreSQL 16·Redis 7, 분석 큐와 AI prepare/generate/maintenance 큐, 별도 dispatcher |
| O04 | [분석 API](../../app/api/analytics.py), [snapshot route](../../app/api/snapshot_route.py), [snapshot 서비스](../../app/services/analysis_snapshots.py): HTTP는 저장된 결과 조회, 계산 함수는 별도 사용 |
| O05 | [저장소 API](../../app/api/repositories.py), [점수](../../app/services/lightweight_score.py): GitHub 검색·목록·등록·재분석·상태. 경량 점수와 파일 상대 위험 점수는 다른 척도 |
| O06 | [Git 수집](../../app/collectors/git/clone_manager.py): `storage/repos/<id>`, clone/fetch·PR ref 수집, 수집용 clone에 hard reset 존재 |
| O07 | [인증 router](../../app/api/auth.py), [인증 서비스](../../app/services/auth_service.py): OAuth state·`read:user,repo` scope·서버 client secret·AES-GCM 토큰 저장·쿠키 user ID |
| O08 | [동기 DB](../../app/core/database.py), [비동기 DB](../../app/database.py), [migration env](../../migrations/env.py): 동기/비동기 Base 분리, 원격 async CA 미설정 시 TLS 검증 해제 경로 |
| O09 | [모델](../../app/models/repository.py), [snapshot](../../app/models/analysis_snapshot.py), [AI 입력](../../app/models/ai_input.py), [결과](../../app/models/ai_result.py): 서버 ID·PostgreSQL 전용 제약·고정 리비전·해시 |
| O10 | [AI DTO](../../app/schemas/ai.py), [AI API 안내](../contracts/ai-api.md), [AI client](../../frontend/js/ai-api.js): 계약 1.9, PR/커밋 선택·CSRF·멱등 key·오류 envelope |
| O11 | [provider registry](../../app/services/ai/providers/registry.py), [동의](../../app/services/ai/consent.py), [입력 정책](../../app/services/ai/content_policy.py), [검증기](../../app/services/ai/validators.py): provider/profile 고정·원문 제외·위치/인용 검증 |
| O12 | [생성](../../app/services/ai/generation_service.py), [job](../../app/services/ai/job_service.py), [runner](../../app/services/ai/job_runner.py), [outbox](../../app/services/ai/outbox.py): DB·lease·작업·비용·외부 호출 수명 관리 |
| O13 | [이력 근거](../../app/services/ai/history_context.py), [접근](../../app/services/ai/access.py), [공유](../../app/services/ai/shared_views.py): 공동 변경은 별도 근거, cloud는 공개 확인 필요, 공유 GET은 현재 repo read 기준 |
| O14 | [frontend API](../../frontend/js/api.js), [URL 상태](../../frontend/js/viz-state.js), [AI 상태](../../frontend/js/ai-state.js), [poll](../../frontend/js/poll.js): URL snapshot 고정, 일반 repo 기본값, AI 세대·취소·backoff |
| O15 | [timeline](../../frontend/js/timeline.js), [network](../../frontend/js/network.js), [code analysis](../../frontend/js/code-analysis.js): Chart.js·D3 그래프·파일 트리. HTML에서 CDN 자산 사용 |
| O16 | [CI](../../.github/workflows/backend-validation.yml), [검증 안내](../validation/current-status.md), [문서 검사](../audit/check_documents.py): Linux PG/Redis·Node 검사와 문서 보존 검사. Windows 앱 빌드 job은 확인 못함 |
| O17 | [환경 예시](../../.env.example), [gitignore](../../.gitignore): 환경변수·암호화 key·TLS CA·로컬 provider 설정, `.env`·clone 제외. 실제 `.env` 값은 이번 조사에서 읽거나 문서에 복사하지 않음 |
| O18 | [로그인 UI](../../frontend/js/login.js), [컴포넌트 로더](../../frontend/js/navbar-loader.js), [분석 poll](../../frontend/js/poll.js): location 이동·fetch·DOM·브라우저 저장소 의존 |

## 구조·진입·개발 도구

`frontend/{pages,js,css}`는 HTML/ES 모듈 기반이며 현재 서비스에서 사용하는 React·TypeScript·Vite manifest는 발견하지 못했다. `test-react/`는 조사 시 비어 있었다. `routing.js`의 화면 전환 코드와 별도 HTML 이동이 공존하므로 SPA라고 가정하지 않는다. `app/api`는 분석·AI, `app/routers`는 인증, `app/tasks`는 큐, `app/collectors`는 외부 수집, `app/services`는 정책·저장·분석, `migrations`는 Alembic이다.

현재 실행 안내: pip 설치 → compose DB/Redis → Alembic → uvicorn `app.main:app` → 일반 분석 worker. AI 사용 시 AI worker·dispatcher도 필요하다. 이번에는 이 명령을 실행하지 않았다. 프런트엔드 별도 번들 빌드·전용 lint·정적 타입 검사 구성은 확인하지 못했다. Docker compose는 개발용 프로세스 안내이며 운영 인프라·백업·배포 자동화의 증거가 아니다.

## 기능·사용자 흐름

홈/검색 → 저장소 등록·분석 진행 → timeline/network/code-analysis → 파일·기여자 drilldown → history snapshot 비교가 기존 분석 흐름이다. `myprojects.html`은 GitHub 접근 기반 목록이며 로컬 폴더 목록이 아니다. `ai-review.html`은 PR/커밋 선택 → 입력 준비·미리보기 → 모델·동의 → summary/review → 결과·피드백·이력 근거 흐름이다. 실제 OAuth/브라우저 E2E는 미실행이다. 기능별 API·화면은 [02 추적표](02-requirements-analysis.md)에 명시한다.

## DB·인증·외부 의존

서버 PostgreSQL은 Repository, Commit, File, CommitFileChange, Contributor/Alias, PullRequest/PrCommit/PrReview/PullRequestFile, Release, AnalysisJob/Snapshot/RepositoryAccess를 저장한다. AI는 PRRevision(커밋 source 포함), AIInput/File, AIJob/Attempt/Outbox/Idempotency, AIResult/Finding/Feedback, Credential/Version, Usage/Budget/Audit, Evidence를 갖는다. User는 별도 async Base다. 과거 handover/team 모델이 존재해도 해당 제품 API가 등록됐다는 뜻은 아니다.

접근 목록 저장과 실제 GitHub 접근 재검증은 다르다. 쿠키는 서명이지 암호화가 아니므로 user ID 외 비밀을 넣지 않는다. GitHub client는 timeout·rate limit/retry를 제공한다. AI는 provider 고정과 외부 전송 정책·예산·암호화·TTL을 갖추지만 그 정확한 의미를 로컬 SQLite에서 자동으로 얻을 수는 없다.

## 파일·통신·브라우저·오류

- 조사한 `app/`·`frontend/`에는 일반 사용자 파일 업로드/다운로드용 `UploadFile`/`FileResponse`/download UI, WebSocket/SSE/EventSource 경로를 확인하지 못했다. AI 입력 파일 HTTP 조회는 존재한다. 이번 기능에 범용 업로드·실시간 소켓을 새로 추가하지 않는다.
- 현 진행 조회는 HTTP polling이다. `fetch` 상대 경로·credentials·URLSearchParams·history·location·local/sessionStorage·DOM·Chart/D3를 번들 환경에 연결해야 한다.
- AI는 정규화 오류·Retry-After·request ID·입력/작업 상태를 사용한다. 일반 API와 auth의 HTTPException 응답은 동일 계약이 아니며 auth callback은 예외 문자열을 detail에 포함한다. agent 오류를 기존 전체 API에 적용했다고 설명하지 않는다.
- Python logging과 collector/task의 print, UI console.error가 공존한다. 중앙 수집·로그 비식별·진단 내보내기의 운영 검증은 미확인이다.

## 재사용 판단

UI의 차트·표·URL·polling, 분석 산식·snapshot 계약, AI provider·diff line-map·content policy·출력 검증은 보존 대상이다. HTTP 의존부는 transport 어댑터를 추가한다. SQL/세션을 직접 참조하는 분석·AI orchestration은 agent에 그대로 import하지 않는다. clone manager는 사용자 폴더에 재사용 불가다. 기존 웹 기능을 agent로 전부 이전하지 않는다.

관련 결정: ADR01~ADR05. 미확인: 실제 운영 토폴로지·DB revision·실제 모델 품질·팀 Rust/Node 숙련도·Windows 최소 버전·설치 정책. 질문은 [14](14-decisions-and-open-questions.md)에 연결한다.
