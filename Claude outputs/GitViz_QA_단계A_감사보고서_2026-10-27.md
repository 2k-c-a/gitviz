# GitViz 독립 QA·보안 검증 — 단계 A 보고서 (읽기 전용 감사·검증 계획)

- 작성: 2026-10-27 (Asia/Seoul)
- 대상: `C:\Users\user\gitviz` (사용자 PC, 원격 셸로 접근)
- 수행 범위: **Level 1 읽기 전용 조사만 수행했다.** 소스·설정·패키지·DB·문서는 수정하지 않았고, 테스트·빌드·서버 기동도 하지 않았다.
  - Git 명령은 `GIT_OPTIONAL_LOCKS=0`과 `--no-optional-locks`를 붙여, index 갱신 같은 부수 효과가 없도록 실행했다.
  - `.env` 파일은 값을 출력하지 않았다. 키 이름과 DB 호스트 종류(원격 여부)만 확인했다.
- 근거 표기:
  - [코드 확인] 실제 소스를 읽고 확인한 것
  - [문서상 요구] 설계 문서에 요구사항으로 적힌 것
  - [구현 에이전트 보고] `docs/migration/validation/*.md`와 `.validation/` 로그에 적힌 실행 결과. **이번에 재현하지 않았다.**
  - [정적 분석에서 의심] 코드를 읽고 의심한 위험. 재현하지 않았다.
  - [미검증]

---

## 1. 요약

- **문서**: 설계 문서 00~14가 모두 존재한다(`docs/migration/`, 15개 파일, 1,333줄). 상대 링크 310개 중 깨진 링크는 0개다. E01~E10, N01~N08, P01~P08, S01~S08, T01~T15, C01~C08, F01~F08, U01~U06, Q01~Q10, ADR01~ADR09 ID가 모두 정의돼 있다.
  - 다만 05·07·08·12 문서 상당 부분이 여전히 "신규 예정·구현 전" 상태로 서술돼 있다. 실제 코드·테스트와 어긋나는 곳이 여러 군데다(8장).
- **Git 상태**:
  - 브랜치 `fix/search-score-caption`, HEAD `a8351f0`(origin/main보다 1커밋 앞섬).
  - 변경 45개, 삭제 29개, 미추적 52개 경로(파일 기준 265개).
  - **P01~P07 구현 전체(`desktop/`, `app/workspace/`, `tests/`, `contracts/`, 데스크톱 인증 서버 코드, migration)가 미커밋 상태다.** 이것이 감사 대상 코드 그 자체이므로, 손실 위험이 가장 크다.
- **아키텍처** [코드 확인]: 설계 문서 08과 같은 구조로 구현돼 있다.
  - Electron main(15개 IPC 모듈), sandbox renderer, 명명형 preload 브리지
  - stdio JSON으로 연결된 Python agent(로컬 SQLite schema 6), 기존 FastAPI 서버 + 데스크톱 bearer 인증
  - TCP 리스너가 없고, agent 실행 환경변수는 허용 목록으로 제한한다.
  - 원본 쓰기와 검증 명령 실행은 main이 네이티브 확인 창을 띄운 뒤 발급하는 1회용 capability가 있어야 진행된다.
- **판정**: 이번 단계에서는 어떤 요구사항도 "검증 완료"로 판정하지 않는다.
  - 구현 에이전트가 기록한 통과 결과(workspace 97개·host 22개 등)는 출처가 있는 보고일 뿐이며 재현하지 않았다.
  - N08(통합 알림)과 REQ-P05(설치·서명·업데이트)는 **미구현**이다.
- **출시 판정 전망: 출시 차단.** N08 미구현, P08~P10 미착수, 정책 미결(D01·Q04·Q08), 미커밋 상태, Windows CI 부재 때문이다(9장).
- **실행 환경 제약(중요)**: 이 세션의 셸은 사용자 PC 안의 **Linux VM**이다(Ubuntu 22.04, Python 3.10, 의존성 없음). Electron 바이너리와 venv는 Windows 전용이다. 따라서 Windows 테스트는 이 셸에서 실행할 수 없고, 별도 방법이 필요하다(10장).

---

## 2. 조사 범위

| 영역 | 확인한 것 |
|---|---|
| 설계 문서 | 00·01·02·04·05·07·09·10·12·13·14는 전문, 03·06·08·11은 구조·관련 절. validation/p01~p07 기록은 요약·결과 절 |
| 현행 문서 | docs/index.md, contracts/{analytics,metrics,ai-api}.md, architecture/{system,repository-access-and-snapshots}.md, decisions/index.md, validation/current-status.md, product/{current-scope,roadmap}.md |
| Electron | `desktop/src/`: main·window·app-protocol·ipc·desktop-ipc·agent·auth·remote·remote-contracts·secrets·proposals·validation·editor·projects·preload. package.json·eslint 설정 |
| Agent | `app/workspace/`: migrations·*_schema·scoped_files·git_service(일부)·review_transport·review(일부)·proposals/apply/validation의 capability 검사부·jobs(스레드 구조) |
| 서버 | app/main.py·desktop_auth_middleware.py·services/desktop_auth.py·api/desktop/auth.py·api/ai/dependencies.py·routers/auth.py(변경부)·api/*(라우트 전수)·models(전수) |
| 테스트·CI | scripts/test_*·verify_* 40개(DB·Redis·네트워크 의존 추정), tests/workspace 22개 파일(98개 test), desktop/tests 단위 8개(22개 test)·e2e 10개, .github/workflows/backend-validation.yml |
| 미조사 | frontend JS의 개별 innerHTML 지점, impact·comparison·runner 내부 로직 전수, collectors, AI 서비스 전체 경로, Forge·패키징 설정 상세 |

---

## 3. 저장소·Git 상태 (보호 대상 기록)

| 항목 | 값 |
|---|---|
| 루트 | `C:\Users\user\gitviz` (원격 `https://github.com/2k-c-a/gitviz.git`) |
| 브랜치/HEAD | `fix/search-score-caption` / `a8351f0bf24c78625990f8756e5a03fc42a002c6` (2026-10-19) |
| origin/main 대비 | 0 behind / 1 ahead. origin/fix/search-score-caption과는 동일 |
| 작업 트리 | M 45 · D 29(구 문서 31개 이동분 포함) · ?? 52경로(265파일) |
| 줄바꿈 영향 | 전체 diff는 +10,327/−14,780줄이지만, 공백·CR을 무시하면 +294/−4,747줄이다. 대부분 줄바꿈 변환에 따른 전체 파일 diff다. 새 `.gitattributes`도 미추적 상태다 |
| stash | 1개 (`codex/metric-policy-integration-validation` 전환 전) |
| ignore 처리 | `.env`, `.validation/`, `venv/`, `build/`, `dist/`, `outputs/`, `certs/`, `desktop/node_modules`, `desktop/dist` |
| 감사 종료 시 상태 | `git status --porcelain` md5 `b57d6962…` (감사 중 변경 없음) |
| 주의 | `.validation/*.pid` 5개와 `live-services.json`(10-07 backend·worker·dispatcher PID)이 남아 있다. 사용자 프로세스일 수 있으므로 종료하지 않는다. `.validation/`에는 Windows ISO·VM·부트 이미지 같은 대용량 산출물도 있다 |
| `.env` | DATABASE_URL이 **원격 Supabase 풀러(ap-northeast-2)**를 가리킨다(값은 미출력). `app/core/database.py`와 `app/database.py`가 이 파일을 load_dotenv로 읽는다. 테스트 격리에 매우 중요하다(결함 A02) |

---

## 4. 실제 아키텍처와 주요 실행 경로 [코드 확인]

```
renderer (gitviz://app, sandbox, contextIsolation, nodeIntegration=false, devTools=false, webview 금지, 권한요청 전부 거부)
  ├─ desktop/ui/* (CSP: default-src 'none'; script-src 'self'; connect-src 'none')
  └─ /web/pages/*.html 기존 7화면 (CSP: script-src 'self'; style 'unsafe-inline'; connect-src 'self')
      │ preload: window.desktop{auth,remote}, window.workspace{~40개 명명 함수}
      ▼ ipcMain.handle ─ trustedSender(sender=main frame, URL∈app 문서 allowlist) + 키 allowlist + ajv schema
Electron main
  ├─ 네이티브: 폴더 선택, 승인 dialog(제안 적용·복구·검증 신뢰·에디터 등록·등록 해제), SecretStore(safeStorage/DPAPI)
  ├─ Agent: 고정 경로 resources/agent/gitviz-workspace-agent.exe, env 허용목록(SystemRoot·WINDIR·TEMP·TMP·LOCALAPPDATA)
  │    stdio JSON line(256KiB, 동시 16, 시스템 5s / 일반 15s / 일부 60s timeout) → 실패 시 agent 영구 unavailable(재시작 없음)
  │    bootstrap 프레임으로 32B 키 전달(handshake 후 1회)
  └─ Remote: GITVIZ_SERVER_ORIGIN(https만) + Bearer, endpointKey allowlist(≈50개), redirect=error, 8MiB, 15s
Python agent (app/workspace): dispatcher → projects/scoped_files/git_service(fsmonitor·filter·ext-diff·textconv 비활성, 시스템/전역 config 무시)
  → snapshots/review(127.0.0.1:11434 Ollama 고정)/proposals/apply(journal·2단계 게시)/validation/runner(Job Object)/impact/comparisons
  → SQLite schema 6 (21개 표)
서버 FastAPI: SessionMiddleware(바깥) → DesktopAuthMiddleware(Bearer 검증, 쿠키 혼합 거부) → 15개 router
  AI CSRF는 검증된 desktop principal이면 생략. 분석 조회는 SnapshotAnalyticsRoute(접근 재확인 + 해시 무결성)
```

| 경로 | 흐름 (근거 파일) |
|---|---|
| 프로젝트 등록 | `workspace:select-project` → 네이티브 폴더 선택 → agent `selectAndRegister` inspect(최대 5단계 확인) → register (projects.ts) |
| 고정 변경 → 로컬 리뷰 | capture → reviews.preview(previewHash·manifestHash) → reviews.start(`acknowledged=true` + 해시 일치) → 스레드에서 Ollama 호출 (review.py, jobs.py, review_transport.py) |
| 제안 → 적용 | proposals.create → main이 get으로 해시 대조 → **네이티브 확인 창** → capability 생성 → agent approve(해시 저장) → apply(capability·executionKey 결합) (proposals.ts, proposals.py, apply.py) |
| 검증 실행 | detect → plan → main이 prepare → **네이티브 신뢰 확인 창** → approve(confirmationHash + capability) → start (validation.ts, validation.py, runner.py) |
| 에디터 | 네이티브 exe 선택 → Authenticode 서명·제품명·SHA-256 확인 → 실행 시 해시 재확인 + files.locate + 소스 해시 재확인 → argv(--goto) (editor.ts) |
| 데스크톱 로그인 | transactions → 시스템 브라우저 authorize(쿠키 + 전용 CSRF + binding) → exchange(UUID Idempotency-Key, 쿠키·Authorization 동시 전송 금지) → DPAPI vault (auth.ts, desktop/auth.py) |

---

## 5. 기존 테스트 환경과 실행 진입점

| 묶음 | 진입점 | 규모 | 전제·부작용 (조사 결과) |
|---|---|---|---|
| 서버 회귀 (CI) | `python scripts/test_*.py`, `node scripts/*.cjs` | Python 33·verify 8·cjs 3 | CI는 Linux + PG16(`gitviz_test`) + Redis. 다수 스크립트에 `gitviz_test` 이름 가드가 있음. **가드가 없는데 DB를 쓰는 것으로 보이는 스크립트**: test_ai_provider(dummy URL 주입), test_progress_reporter, test_repository_snapshots, verify_analysis_sql, verify_other_repo, verify_production_integration, verify_schema_v2, verify_snapshot_integration |
| workspace 단위 | `python -B -m unittest discover -s tests/workspace -p "test_*.py"` | 22개 파일, 98개 test | 임시 디렉터리 + 실제 git 사용. 일부는 Windows 전용(`os.name=='nt'` skip) |
| desktop host 단위 | `npm --prefix desktop test` (= `node --test dist/tests/*.test.js`) | 8개 파일, 22개 test | **빌드된 `desktop/dist`를 실행**한다. 현재 dist가 src와 같은지는 알 수 없으므로 build(dist 쓰기, ignore 대상)가 먼저 필요 |
| Electron E2E | `npm --prefix desktop run test:foundation` 외 e2e 9개(실행 스크립트 없음) | 10개 | Windows + Electron. 일부는 패키지·agent freeze 필요 |
| 정적 검사 | `npm --prefix desktop run lint / typecheck / typecheck:agent`, `ruff --no-cache --config ruff-workspace.toml` | — | 읽기 전용(캐시 옵션 확인) |
| 문서 검사 | `python -B docs/audit/check_documents.py` | — | 파일 주석상 import·env·DB·network·쓰기 없음 |
| CI 범위 | backend-validation.yml: docs / postgres / ai-queue job | — | **workspace·desktop 테스트와 Windows job이 없다** (`windows-desktop.yml`은 계획만 있음) |

---

## 6. 요구사항별 검증 추적표

- **판정 기준**: 이번 단계는 코드 존재와 호출 경로만 확인했다. 그래서 모든 항목이 기본적으로 **미검증**이다.
- "보고된 증거" 열은 [구현 에이전트 보고]이며, 단계 B·C에서 재현할 대상이다.
- 요구사항 P와 구현 단계 P는 문서 14의 규칙대로 `REQ-P` / `STEP-P`로 구분했다.

### 6.1 기존 기능 E01~E10

| ID | 핵심 | 구현 위치 [코드 확인] | 기존 테스트 | 보고된 증거 | 판정 | 단계 B·C 추가 검증 |
|---|---|---|---|---|---|---|
| E01 | 타임라인 | analytics `/timeline` → snapshot payload, timeline.js | test_metric_display.cjs, test_viz_modules.cjs, test_repository_snapshots | P03 Electron 기존 7화면 | 미검증 | months 경계(−1·0·2400·2401), snapshot_id 고정, 데스크톱 transport 동일 응답 |
| E02 | 기여자·히트맵 | `/top-contributors`, `/contributor-heatmap` | 동일 | 동일 | 미검증 | weeks 1·104·105, 미식별 기여 분모 |
| E03 | 릴리스 | `/releases` | 동일 | — | 미검증 | limit 경계 |
| E04 | 커밋 유형 | `/commit-types` | test_analytics_calculations | — | 미검증 | 분류 회귀 |
| E05 | 협업 네트워크 | `/network`, `/network/top-pairs` | test_analytics_calculations(co-edit) | — | 미검증 | limit_nodes 1·1000·1001, min_edge_weight |
| E06 | 파일·결합도·트리 | `/risk-scores`, `/bus-factor`, `/coupling*`, `/files/{id}/detail`, `/tree`, contributor drilldown | test_metric_policy, test_analytics_calculations | — | 미검증 | 스냅샷 밖 파일 404, sort_by 잘못된 값 422 |
| E07 | 검색·예비 점수 | `/search/github-repositories`, lightweight_score | test_lightweight_score | — | 미검증 | q 누락 422, GitHub 429/5xx는 mock |
| E08 | 등록·내 프로젝트·진행 | repositories.py 7개 | test_backend_improvements, test_repository_snapshots | — | 미검증 | 409(진행 중), 비공개 + 무토큰, 데스크톱 allowlist에 analysis_start·end 없음(A09) |
| E09 | 스냅샷 이력·비교 | snapshots.py, snapshot_comparison | test_repository_snapshots | — | 미검증 | 계산 버전이 다른 비교 거부, 해시 손상 409, before_id 페이지 |
| E10 | PR·커밋 AI·피드백·이력·공유 | api/ai 10개 router(29개 경로) | test_ai_* 25개, verify_ai_mvp_integration, test_ai_frontend | AI 수용 기록(10-07), P03 회귀 193 | 미검증·**사용자 결정 대기(D01)** | CSRF·bearer 분기, 공유 GET 범위, 멱등·uncertain |

### 6.2 신규 기능 N01~N08 (완료 조건 C01~C08)

| ID | 완료 조건 | 구현 [코드 확인] | 테스트(존재) | 보고된 증거 | 판정 | 핵심 미검증 |
|---|---|---|---|---|---|---|
| N01 | C01 프로젝트 전환·Git 상태·경계 | projects.py, scoped_files.py, git_service.py / projects.ts | test_projects(4)·test_git_status(5)·test_scoped_files(2) / projects.e2e | P02 Python 30·host 13·Electron | 미검증 | junction·UNC·대소문자·유니코드 정규화, late response, index·바이트 불변 |
| N02 | C02 고정 입력·명시 동의·근거·미검증 표시 | snapshots·review·jobs·review_transport | test_snapshots(6)·test_review(17) / review(-preview).e2e | P05 workspace 60 | 미검증 | **동의가 renderer 플래그에만 의존(A03)**, 실제 전송 payload와 동의 해시의 동일성, 비밀 제외 |
| N03 | C03 무승인 쓰기 0·해시 충돌·중단 복구 | proposals·apply·write_files·patches / proposals.ts | test_apply(7)·test_apply_recovery(1)·test_patch_rules(5)·test_p06_migration / refactor(-package).e2e | P06 77개, kill 9개 경계 | 미검증 | 승인 우회 probe, TOCTOU, 잠금, 외부 변경 보존 |
| N04 | C04 경로·위치 전달·미설치 대체 | editor.ts | editor.test(1) | P07 argv, **화면 인수 연기** | 부분 검증 대상(화면 미인수) | 실제 줄·열, 옵션 주입, 해시 변경 시 재설정 |
| N05 | C05 계획대로만 실행·취소·timeout·위치 연결 | detection·validation·runner·diagnostics / validation.ts | test_detection(3)·test_validation(8)·test_runner(8) / validation.e2e | P07 runner 8, npm hook | 미검증 | pre/post hook, 설정 해시 변경 시 재승인, 손자 프로세스 종료, 출력 flood |
| N06 | C06 정적/이력/추론 구분·미분석 범위 | impact.py, impact-worker.ts | test_impact(3), impact.test(2) | P06 | 미검증 | 원격 이력 매핑 미구현으로 historical은 항상 unavailable(설계상 인정) |
| N07 | C07 같은 도구·환경 비교·미측정 null | comparisons.py, comparison-metrics.ts, metrics.ts | test_comparison_metrics(4), metrics.test(1) | P07 | 미검증 | 환경 불일치 시 not_comparable |
| N08 | C08 중복·필터·이동·재시작 보존 | **없음** (events·notifications 모듈, schema 표, RPC 없음) | 없음(T09 미작성) | — | **미구현** | — |

### 6.3 플랫폼 요구 REQ-P01~P08

| ID | 요구 | 구현 | 테스트 | 판정 |
|---|---|---|---|---|
| REQ-P01 | UI 번들·보안 브리지·표준 사용자 실행 | window.ts·app-protocol.ts·preload.ts·ipc.ts | bridge-security.test(1), foundation.e2e | 미검증 (보고: P01 clean VM) |
| REQ-P02 | 웹 OAuth 유지·데스크톱 로그인·토큰/계정 분리 | auth.ts·remote.ts / desktop/auth.py·middleware | test_desktop_auth.py, auth.e2e, remote.test(5) | 미검증. **실제 서버 OAuth는 미실행**(문서에도 남은 gate로 기록) |
| REQ-P03 | 로컬 DB·비밀·강제 종료 복구 | store·migrations·crypto / secrets.ts | test_recovery(6)·test_crypto(2), secrets.test(5) | 미검증 |
| REQ-P04 | 에디터·프로세스·파일 잠금·권한 | editor.ts·runner.py·write_files.py | test_runner, test_windows(3) | 미검증 |
| REQ-P05 | 설치·제거·서명·업데이트 | **없음** (update.ts·installer 없음, forge maker 미확인) | T13 없음 | **미구현** (STEP-P10) |
| REQ-P06 | 네트워크·인증 만료·부분 실패·재실행 | remote.ts·auth.ts | remote-compat.e2e | 미검증 |
| REQ-P07 | 웹·앱 동시 사용·API 호환·프로젝트 분리 | middleware(쿠키·bearer 혼합 거부) | test_desktop_api_compat.py | 미검증. **실서버 동시 사용 인수는 미실행** |
| REQ-P08 | 책임 경계·최소 로그·진단·운영 제한 | stderr 폐기, 오류 코드 정규화 | — | 미검증 (진단 내보내기 미구현) |

### 6.4 보안 요구 S01~S08

| ID | 대응 테스트(문서 지정) | 존재 여부 | 판정 |
|---|---|---|---|
| S01 경로·identity | T01/T02/T04/T05/T06 | 있음 | 미검증 |
| S02 프로젝트·원격 권한 | T02/T09/T11/T14 | **T09 없음** | 미검증 |
| S03 승인·해시·capability·journal | T04/T06/T08/T12 | 있음 | 미검증 |
| S04 비신뢰 소스·동의 | T03/T07/T08 | 있음 | 미검증 (A03 의심) |
| S05 실행 파일·argv·cwd·한도 | T05/T06 | 있음 | 미검증 |
| S06 SecretStore·암호화·ACL·로그 | T03/T09/T10/T12 | 일부 | 미검증 |
| S07 sandbox·sender·CSP | T10 | bridge-security 1개(workspace.e2e.ts 없음) | 미검증 (A05) |
| S08 서명·feed·배포 | T13/T15 | **T13 없음** | 미구현 |

---

## 7. 단계 A 발견 사항 (결함 후보)

> 모든 항목은 정적 분석이나 문서 대조로 찾은 것이며, **재현된 취약점이 아니다.** 단계 B·C의 재현 결과에 따라 심각도를 다시 정한다.

| ID | 심각도(잠정) | 근거 | 내용 | 영향 | 권고(수정은 하지 않음) |
|---|---|---|---|---|---|
| A01 | High (절차·데이터 손실) | 코드 확인 | P01~P07 산출물 전체와 데스크톱 인증 서버 코드·migration이 미커밋 상태다. 구 문서 29개는 삭제로 잡혀 있다 | 덮어쓰기·정리 명령·디스크 장애 시 복구 불가. 검증 대상 버전을 고정할 수 없다 | 단계 B 전에 사용자가 별도 브랜치로 커밋하거나 백업 사본을 만들어 **검증 기준 커밋을 고정** |
| A02 | High (테스트 격리) | 코드 확인 | `.env`의 DATABASE_URL이 원격 팀 공유 DB를 가리킨다. app DB 모듈이 load_dotenv로 읽으므로 가드 없는 스크립트를 그냥 실행하면 공유 DB에 연결된다 | 테스트가 팀 DB를 변경할 위험 | 모든 실행에서 `DATABASE_URL`·`DATABASE_URL_ASYNC`·`REDIS_URL`을 접속 불가 값이나 격리 DB로 **명시 덮어쓰기**(load_dotenv는 기존 env를 덮지 않음). 가드 없는 verify_* 8개는 실행 제외 |
| A03 | Medium (정적 분석에서 의심, S04) | 코드 확인 | 로컬 리뷰의 전송 동의는 renderer가 보내는 `acknowledged=true`와 미리보기 해시로만 확인한다. 제안 적용·검증 실행과 달리 **네이티브 확인 창이나 main 발급 capability가 없다**(projects.ts에서 review-start를 그대로 전달) | renderer가 침해되면 사용자 조작 없이 고정 소스가 127.0.0.1:11434로 전송될 수 있다. 클라우드는 차단돼 있어 외부 유출 범위는 제한적이다. 그 포트에서 대기하는 다른 로컬 프로세스도 소스를 받을 수 있다 | 동의를 main의 네이티브 확인 + capability로 결합하는 방안 검토. 단계 C에서 probe로 재현 시도 |
| A04 | Medium (가용성) | 코드 확인 | agent RPC 하나라도 timeout(15s/60s)이나 프로토콜 오류가 나면 agent를 kill하고 `dead=true`로 영구 전환한다. 재시작은 없다(설계 문서의 P01 "자동 재시작 없음"과 일치) | 대형 저장소 등에서 느린 작업 1건이 전체 로컬 기능을 앱 재시작 전까지 멈춘다. 진행 중 리뷰·적용은 uncertain·recovery 상태가 된다 | 15s 초과 가능 operation(impact.*, detect, preview, 대형 git.status) 측정. 명시적 재시작 UX 검토 |
| A05 | Medium (정적 분석에서 의심, S07) | 코드 확인 | 기존 웹 7화면(`/web/pages/*`)도 trustedSender가 허용하는 문서여서 **브리지 전체에 접근할 수 있다**. frontend/js에 `innerHTML` 사용처가 71개 있고 원격 문자열(커밋 메시지·PR 제목·AI 출력)이 들어간다 | XSS가 생기면 브리지 호출(파일 미리보기·리뷰 시작·원격 bearer 요청)로 이어진다. CSP `script-src 'self'`가 주된 완화책이다 | 단계 C에서 XSS fixture(스크립트·이벤트 핸들러·폼)로 CSP 차단 확인. 웹 화면에는 workspace 브리지를 노출하지 않는 방안 검토 |
| A06 | Low | 코드 확인 | validation 승인의 capability 해시를 `!=`로 비교한다(validation.py:216). proposals는 `secrets.compare_digest`를 쓴다 | 시간차 공격 가능성은 낮다(로컬 pipe, 1회용, 5분) | compare_digest로 통일 |
| A07 | Low | 코드 확인 | 서버 origin을 런타임 환경변수 `GITVIZ_SERVER_ORIGIN`에서 읽는다(https만 허용) | 앱을 실행하는 쪽이 env로 로그인 대상 서버를 바꿀 수 있다(피싱 화면) | P10에서 빌드에 고정. 단계 B에서 env 조작 시 동작 확인 |
| A08 | Info | 코드 확인 | `DESKTOP_AUTH_ENABLED≠1`일 때 Authorization 헤더가 붙은 모든 요청이 middleware에서 401을 받는다(settings()의 404가 HTTPException으로 잡힘). 공개 경로도 포함된다 | 프록시나 다른 클라이언트가 Authorization을 붙이면 기존 기능이 실패한다 | 의도된 동작인지 확인하고 테스트로 고정 |
| A09 | Low (기능 동등성) | 코드 확인 | 데스크톱 remote allowlist에 `registerRepository`·`reanalyze`의 `analysis_start/end`, `commits`의 `limit`가 없다. timeline `months`는 최소 0(서버는 −1 허용) | 웹 UI도 기간 입력을 쓰지 않아 화면 회귀는 아니고 API 동등성만 부족하다 | E08 동등성 표에 기록 |
| A10 | Medium (문서) | 문서·코드 대조 | 8장 참고. 05·07·08·12가 "구현 전·예정"으로 남아 있고, 07의 operation 목록과 12의 테스트·명령 목록이 실제와 다르다 | 감사자·팀이 잘못된 계약을 기준으로 삼을 수 있다 | 문서를 구현 기준으로 정리(승인 후 별도 작업) |
| A11 | High (출시 범위) | 코드·문서 확인 | N08·T09 미구현, REQ-P05·S08·T13 미구현, STEP-P08~P10 미착수, D01·Q04·Q08 미결 | ADR08("전체 기능 인수 후 출시")상 출시 불가 | — |
| A12 | Medium (검증 재현성) | 코드 확인 | CI에 workspace 98개·desktop 22개 테스트와 Windows job이 없다. Windows 실행 증거는 ignore된 `.validation/` 로그뿐이다 | 다른 사람이나 CI가 재현할 수 없다 | windows-desktop CI 추가 검토 |
| A13 | Low | 코드 확인 | 제안 승인 네이티브 창에는 목적·해시·경로만 나오고 diff 본문은 renderer에서만 보인다 | renderer가 침해되면 사용자에게 다른 diff를 보여 줄 수 있다. 다만 적용 대상은 patchHash로 고정된다 | diff 요약이나 줄 수를 네이티브 창에 표시하는 방안 검토 |
| A14 | Info | 코드 확인 | 줄바꿈 변환으로 74개 파일이 전체 diff로 잡힌다. `.gitattributes`도 미추적이다 | 리뷰할 때 실제 변경을 가린다 | 커밋 시 줄바꿈 정규화를 별도 커밋으로 분리 |

긍정적 관찰 [코드 확인, 동작은 미검증]:

- `nodeIntegration=false`, `contextIsolation`·`sandbox=true`, `devTools=false`, webview·권한 요청·외부 내비게이션 차단
- 외부 링크는 github.com HTTPS만, 확인 창을 거쳐 연다
- agent env 허용목록, `shell:false`, Git 하드닝(`core.fsmonitor=false`, 시스템·전역 config 무시, ext-diff·textconv·filter 비활성, 프로토콜 차단)
- 상대 경로 검증(`..`·`.git`·예약 장치명·끝 공백/마침표·`\`·`:` 거부)과 reparse point 거부
- Ollama 엔드포인트를 I/O 경계에서 다시 고정
- 승인 capability가 renderer로 나가지 않음
- 데스크톱 exchange는 쿠키·Authorization 동시 전송을 거부
- 토큰은 해시만 저장

---

## 8. 문서와 코드의 불일치

| 위치 | 문서 내용 | 실제 코드·상태 | 영향 |
|---|---|---|---|
| 05 서두 | "다음 규칙은 신규 설계이며 구현된 동작이 아니다" | F01~F07이 구현돼 있음 | 판정 기준 혼란 |
| 07 operation 표 | `workspace.editors.list/open/configure`, `workspace.projects.connectRemote`, `workspace.notifications.*`, `workspace.events.subscribe/unsubscribe` | 에디터는 main IPC(`configure-editor/open-editor/copy-location`) + agent의 `files.locate`·`projects.setEditor`로 구현. connectRemote·notifications·events는 없음(44개 operation) | N06 이력 연결과 N08 계약 미구현 |
| 07 F04 / 05 F04 | JetBrains 등 adapter | VS Code·Cursor만 지원(Q07 결정, 09에 반영) | 05가 갱신되지 않음 |
| 08 | "아래 desktop/agent 모듈은 모두 예정", `src/update.ts`, `events.py`, `notifications.py`, `windows-desktop.yml` | update·events·notifications·CI 없음, 나머지는 존재 | 상태 표기 오류 |
| 12 | "예정 테스트 목록… 전부 신규 생성 예정", `test_notifications.py`(T09), `workspace.e2e.ts`(T10), `install-update.ps1`(T13), `npm run test:e2e` | 대부분 존재. T09·T13·workspace.e2e.ts·test:e2e 스크립트는 없음. 대신 개별 e2e 9개가 있음 | 테스트 ID와 파일 대응 불명확 |
| 12 명령 | `.validation\venv-workspace` 사용 | venv-workspace가 존재(ignore 대상) | 재현 환경을 문서로만 알 수 있음 |
| ID 체계 | 보안 요구 S01~S08 | 같은 문서군에서 S11~S15를 AI 단계 ID로도 사용 | ID 충돌로 추적 오류 가능 |
| 13 P05 | "PC 로컬 경로 승인 범위 완료" | cloud BYOK는 차단된 채로 코드상 profile이 Ollama 하나뿐(review.py) | 문서와 일치(정보) |
| README·current-scope | 기준 `a8351f0`, 데스크톱 언급 없음 | 미커밋 데스크톱 기능 존재 | 진입 문서에 앱이 빠져 있음 |

---

## 9. 우선순위별 위험과 출시 차단 가능성

| 순위 | 위험 | 차단 여부 |
|---|---|---|
| 1 | N08 미구현, STEP-P08~P10 미착수, 설치·서명·업데이트 없음(A11) | **차단** (ADR08) |
| 2 | 정책 미결: D01 공유 범위, Q04 클라우드 전송, Q08 보존·삭제, Q01·Q02 OS·배포 | **차단** (문서상 출시 gate) |
| 3 | 실제 서버 OAuth와 웹·앱 동시 사용 인수 미실행(P03 잔여), 에디터 화면 인수 연기(P07) | **차단** (C04·T11·T14) |
| 4 | 미커밋 상태로 검증 기준을 고정할 수 없음(A01) | 검증 진행 차단 |
| 5 | 승인·동의·브리지 경계 의심(A03·A05·A13) | 재현되면 High 이상으로 상향 가능 |
| 6 | agent 영구 중단 가용성(A04), CI·재현성 부재(A12) | 조건부 |
| 7 | 실제 모델 품질·지표 타당성 미평가 | 기능 출시와 별개의 품질 gate |

---

## 10. 환경 제약과 미결 질문

**환경 제약**

1. 이 감사 셸은 Linux VM이다. Electron(`electron.exe`)과 venv(`venv\Scripts`, `.validation\venv-workspace`)는 Windows 전용이라 Windows 테스트를 이 셸에서 실행할 수 없다. 방법은 둘 중 하나다.
   - (a) 사용자가 PowerShell에서 제가 드리는 명령을 실행하고 결과 로그를 공유한다.
   - (b) 컴퓨터 사용(Computer Use) 권한을 승인받아 제가 PowerShell을 조작한다.
2. VM의 Python 3.10에는 의존성이 없다. 패키지 설치는 금지 항목이라 Linux에서는 순수 Node 테스트(`*.cjs`)와 문서 검사만 가능하다.
3. 격리된 PostgreSQL·Redis가 필요한 테스트(T11·T15·AI 큐)에는 Docker나 별도 인스턴스가 필요하다(Level 3, 승인 필요).
4. 실제 GitHub OAuth, 실제 AI 제공자, 운영 서버는 Level 5다. 이번 계획에서는 제외하고 별도 승인 대상으로 둔다.

**사용자 결정이 필요한 질문**

- Q-A1: 단계 B 전에 미커밋 작업을 커밋하거나 백업해서 검증 기준을 고정할지(권장), 현재 작업 트리 그대로 검증할지.
- Q-A2: Windows 실행 방식 (a) 사용자 실행 / (b) Computer Use.
- Q-A3: `desktop/dist` 재빌드(ignore된 출력 디렉터리 덮어쓰기)를 허용할지. 허용하지 않으면 host 테스트는 기존 dist 기준이 되어 현재 src를 검증하지 못한다.
- Q-A4: 격리 PG·Redis를 Docker로 띄우는 것을 허용할지(기존 compose 서비스와 다른 이름·포트 사용).
- Q-A5: A03·A05 재현을 위한 probe 스크립트를 **저장소 밖**(예: `%TEMP%\gitviz-qa\`)에 작성해 실행하는 것을 허용할지.

---

## 11. 테스트 계획

### 11.1 공통 격리 규칙 (모든 단계)

- **실행 전후 상태 기록**(저장소 밖에 저장):
  - Git: `git --no-optional-locks status --porcelain=v1 -uall`
  - 소스 해시 목록: node_modules·dist·.validation·venv 제외. 실행 후 같은 명령으로 diff를 확인한다.
- **환경 강제 지정**:
  - `DATABASE_URL=postgresql://qa:qa@127.0.0.1:9/qa_unreachable`
  - `DATABASE_URL_ASYNC=postgresql+asyncpg://qa:qa@127.0.0.1:9/qa_unreachable`
  - `REDIS_URL=redis://127.0.0.1:9/15`
  - `AI_ENABLED=0`(필요한 테스트만 예외)
  - `TEMP`·`TMP=%TEMP%\gitviz-qa\<run-id>`
- **실행 제외**: 가드 없는 DB 스크립트(verify_* 8개, test_progress_reporter 등), test_ai_smoke·실제 연동(NET).
- **모의 서버 포트**: 127.0.0.1:11434에 실제 Ollama가 떠 있으면 모의 제공자 테스트는 중단한다. 실행 전에 포트 점유를 확인한다.

### 11.2 단계 B — 안전한 테스트 실행 (승인 후)

| 순서 | ID | Level | 명령 (cwd = repo root) | 목적·요구 | 예상 부작용 |
|---|---|---|---|---|---|
| B0 | 기준 기록 | 1 | 위 상태 기록 + `node --version; git --version; .\venv\Scripts\python.exe --version` | 환경 버전 | 저장소 밖 파일만 생성 |
| B1 | 문서 | 2 | `python -B docs/audit/check_documents.py`; `python -B docs/audit/test_check_documents.py` | 문서 무결성 | test_check_documents가 TEMP에 fixture 생성 |
| B2 | 순수 Node 회귀 | 2 | `node scripts/test_metric_display.cjs`; `node scripts/test_viz_modules.cjs`; `node scripts/test_ai_frontend.cjs` | E01~E06·E10 프론트 | 없음(확인 필요) |
| B3 | 정적 검사 | 2 | `npm --prefix desktop run lint`; `npm --prefix desktop run typecheck`; `npm --prefix desktop run typecheck:agent`; `.\.validation\venv-workspace\Scripts\python.exe -B -m ruff check --no-cache --config ruff-workspace.toml app/workspace tests/workspace` | REQ-P01·S07 | 없음 |
| B4 | 서버 순수 단위 | 2 | (env 강제 지정 후) `.\venv\Scripts\python.exe -B scripts/<name>.py`. 대상: test_analytics_calculations, test_metric_policy, test_lightweight_score, test_ai_output_validation, test_ai_output_limits, test_ai_contracts(DB 가드 포함) | E01~E10 계산·DTO | 없음(DB 접속 불가 값) |
| B5 | workspace 단위 | 3 | `.\.validation\venv-workspace\Scripts\python.exe -B -m unittest discover -s tests/workspace -p "test_*.py" -v` | N01~N07·S01·S03·S05·T01~T08·T12 | TEMP에 임시 git·SQLite 생성 |
| B6 | desktop 빌드·단위 | 3 | `npm --prefix desktop run build`; `npm --prefix desktop test` | REQ-P01·P02·P03, T05·T10 일부 | `desktop/dist` 덮어쓰기(ignore 대상, **Q-A3 승인 필요**) |
| B7 | 격리 PG·Redis 회귀 | 3 | `docker run --rm -d --name gitviz-qa-pg -p 55432:5432 -e POSTGRES_USER=gitviz_test -e POSTGRES_PASSWORD=qa -e POSTGRES_DB=gitviz_test postgres:16` (Redis는 `-p 56379:6379`) + `GITVIZ_TEST_DATABASE_URL`을 지정하고 test_desktop_auth, test_desktop_api_compat, test_desktop_migration, test_ai_security, test_ai_schema, test_backend_improvements, test_repository_snapshots(네트워크 의존 확인 후), test_ai_job_lifecycle, test_ai_queue_integration 실행 | REQ-P02·P07·T11·T15·E08~E10 | 일회용 컨테이너(종료 시 삭제). 이미지 다운로드 가능 |
| B8 | Electron 기본 | 3 | `npm --prefix desktop run test:foundation` | T10·T12 일부 | 앱 userData는 테스트 profile로 격리되는지 사전 확인 필요 |

### 11.3 단계 C — 격리 환경의 보안·실패 테스트 (승인 후)

| ID | 요구 | 시나리오 | 방법 | 기대 결과 |
|---|---|---|---|---|
| C-SEC-01 | S07·REQ-P01 | 웹 화면 XSS → 브리지 호출 | 모의 서버가 커밋 메시지·PR 제목·AI 출력에 `<img onerror>`, `<script>`, `<form>`을 담아 반환. 해당 화면을 e2e로 연다 | 스크립트 미실행(CSP 위반 로그), 브리지 호출 0 |
| C-SEC-02 | S04·N02 (A03) | 동의 없는 리뷰 전송 | renderer 맥락(devtools 없이 테스트 preload 훅)에서 `previewReview` → `startReview(acknowledged:true)`를 사람 조작 없이 호출. 11434에 모의 수신기를 두고 payload 수집 | 현 설계상 전송이 일어날 것으로 예상. 실제 전송 바이트와 previewHash·manifest 비교. 결과에 따라 A03 판정 |
| C-SEC-03 | S03·C03 | 승인 우회 | apply를 ①approvalId 위조 ②다른 proposal의 approvalId ③만료 후 ④다른 idempotencyKey ⑤approve 뒤 원본 수정 ⑥patch 변경 후 기존 승인으로 호출 | 전부 거부, 파일 해시 불변 |
| C-SEC-04 | S01 | 경로 경계 | `..`, 절대경로, `C:`, `\\?\`, UNC, 8.3 이름, 대소문자 변형, NFC/NFD, junction·symlink, ADS(`file:stream`), 예약 장치명, `.git/…`를 capture·preview·locate·patch 경로에 입력 | PATH_OUTSIDE_SCOPE, 밖의 파일 읽기·쓰기 0 |
| C-SEC-05 | S05·N05 | 명령 실행 경계 | package.json script 변경(승인 후), pre/post hook 추가, `--fix` 모드, 손자 프로세스 무한 실행, stdout 10MiB | 재승인 요구, install·fix 거부, 취소 후 남은 프로세스 0, 1MiB에서 자름 |
| C-SEC-06 | S06 | 비밀 노출 | 소스·출력에 canary 문자열을 심고 리뷰·검증·실패를 실행한 뒤 agent stderr·로그·SQLite 평문 검색 | canary 평문 0(암호화 blob만) |
| C-SEC-07 | S02·REQ-P07 | 쿠키와 bearer 혼합·계정 전환 | 격리 서버에 쿠키 + Bearer 동시 전송, 만료·폐기 토큰, 로그아웃 후 재사용, Authorization만 붙인 공개 경로(A08) | 401, 혼합 거부, A08 동작 확인 |
| C-FAIL-01 | REQ-P03·T12 | 강제 종료 | apply·validation·review 도중 agent·main 강제 종료(테스트 저장소) | recovery_required, interrupted, uncertain. 자동 재전송·자동 쓰기 0 |
| C-FAIL-02 | A04 | timeout 영구 중단 | 대형 fixture 저장소(파일 수만 개)에서 git.status·impact·detect 시간 측정 | 15s 초과 operation 목록과 영향 기록 |
| C-FAIL-03 | N03 | 동시 변경 | 적용 중 외부 에디터 쓰기, 파일 잠금(공유 거부 핸들), 디스크 부족(소형 VHD는 Level 4로 분리) | 덮어쓰기 0, PRECONDITION_FAILED |
| C-DATA-01 | REQ-P03 | migration | schema 1~5 fixture → 6, 실패 주입 후 롤백, 미지원 schema 7 | 백업 보존, 쓰기 차단 |

### 11.4 필수 보안 목표의 측정 방법

| 목표 | 측정 방법 | 이번 상태 |
|---|---|---|
| 승인되지 않은 코드 쓰기 0 | C-SEC-03·C-FAIL-01·C-FAIL-03의 전후 파일 해시와 journal 대조 | 미검증 |
| 승인되지 않은 커밋·푸시 0 | agent·main 코드에서 git 쓰기 명령 정적 전수 + 테스트 전후 `git reflog`·`index` 해시 비교 | 정적 1차 확인: `app/workspace/*.py`에서 commit·push·reset·checkout·add·stash·clean·update-index를 문자열 인자로 쓰는 곳 0건(`'apply'` 1건은 RPC 이름). 인자를 동적으로 조립하는 경로와 desktop/src 쪽 확인은 남아 있다. 동적 검증 미실시 |
| 프로젝트 경계 밖 접근 0 | C-SEC-04 + Windows 파일 접근 감시(Process Monitor 필터, 선택) | 미검증 |
| 승인과 다른 AI 전송 0 | C-SEC-02 수신기에서 바이트 비교 | 미검증 (A03 의심) |
| 비승인 로컬 파일·비밀 외부 전송 0 | C-SEC-06 + 클라우드 경로 차단 확인(PROVIDER_BLOCKED) | 미검증 |

> 위 테스트에서 위반이 관찰되지 않았다고 해서 모든 경로에서 불가능하다는 증명은 아니다. 테스트한 경로의 범위를 결과에 함께 기록한다.

---

## 12. 다음 단계 제안 (승인 요청)

1. **선행(사용자 작업 권장)**: 미커밋 작업을 별도 브랜치에 커밋하거나 백업해 검증 기준 커밋을 고정한다(Q-A1). 제가 커밋하지는 않습니다.
2. **단계 B-1 (Level 2, 승인되면 바로 가능)**: Linux VM에서 B1·B2를 실행한다. 저장소 쓰기는 없다.
3. **단계 B-2 (Level 2~3, Windows 필요)**: Q-A2 방식으로 B0·B3·B4·B5를 실행하고, Q-A3이 승인되면 B6·B8도 실행한다.
4. **단계 B-3 (Level 3, Q-A4 승인 필요)**: B7 격리 PG·Redis 회귀.
5. **단계 C (Level 3, Q-A5 승인 필요)**: 11.3 표의 순서대로 진행하되 A03·A05·승인 우회(C-SEC-01~03)를 먼저 한다. 디스크 고갈·설치 테스트(Level 4)는 별도 계획으로 분리한다.

각 단계를 마칠 때마다 명령, 종료 코드, 통과·실패·건너뜀 수, 전후 Git diff를 보고하고 멈춥니다.
