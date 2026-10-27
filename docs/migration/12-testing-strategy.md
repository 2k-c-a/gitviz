# 검증 전략과 이번 작업 기록

목적: 구현 단계의 테스트와 문서 작성 단계의 실제 검증을 구분한다. 근거: [01 O16](01-repository-analysis.md), [CI](../../.github/workflows/backend-validation.yml), [기존 검증 상태](../validation/current-status.md). 관련: E01~E10·N01~N08·P01~P08, [02 추적표](02-requirements-analysis.md), [13 단계](13-implementation-plan.md). Windows matrix·언어·editor·SLO는 Q01/Q06/Q07/Q10이 미정이다.

## 예정 테스트 목록

다음 경로는 **전부 신규 생성 예정**이며 지금 존재하거나 실행됐다는 뜻이 아니다. unittest 기반 workspace 테스트를 제안하여 pytest 설치를 필수로 추가하지 않는다. P01 host 테스트 도구·npm script 이름은 아래 기준으로 고정하며, 실제 파일/구현과 실행 증거는 아직 없다.

| ID | 예정 테스트·범위 | 객관적 통과 기준 |
|---|---|---|
| T01 | `tests/workspace/test_scoped_files.py`: unit+실제 FS, traversal/link/junction·worktree grant | 선택 root 밖 본문 접근 0, 외부 metadata는 명시 grant만 |
| T02 | `tests/workspace/test_git_status.py`: 실제 임시 Git, staged/unstaged 동시·rename·untracked·conflict·unborn·detached·worktree·CRLF | CLI fixture 일치·index/파일 hash 전후 불변 |
| T03 | `tests/workspace/test_review.py`: fake provider·실제 local store·동의/제외/schema/timeout/uncertain | 동의 bytes 일치·private/unknown cloud 차단·중복 호출 방지·위치 검증 |
| T04 | `tests/workspace/test_apply.py`: approve/reject/re-request·locked file·reparse race·중간 종료 | 승인 없는 쓰기 0·외부 변경 덮어쓰기 0·journal 복구 |
| T05 | `desktop/tests/editor.test.ts`: fake executable+Windows 실제 adapter | 경로/위치 인자 정확·옵션 주입/임의 shell 차단·대체 안내 |
| T06 | `tests/workspace/test_runner.py`: fixture script·설정 변경·stdout/stderr flood·grandchild·timeout | 계획/실행 일치·한도·자식 종료·diagnostic 연결·무승인 실행 0 |
| T07 | `tests/workspace/test_impact.py`: parser fixture·같이 수정 이력·unresolved symbol | 근거 종류·위치·SHA·지원 범위 구별·추론의 사실 승격 0 |
| T08 | `tests/workspace/test_comparison.py`: 동일/상이한 도구·환경·지원 안 됨 | 비교 조건 불일치 시 not_comparable·미측정 null·근거 보존 |
| T09 | `tests/workspace/test_notifications.py`: 중복·다중project·read CAS·삭제 target·cursor | 단일 dedupe·필터/읽음·안전한 대상 이동·재시작 보존 |
| T10 | `desktop/tests/bridge-security.test.ts`, `desktop/tests/workspace.e2e.ts`: 실제 sandbox 창·XSS·subframe·remote navigation | raw native API/토큰 접근 불가·sender 검증·CSP·typed RPC |
| T11 | `scripts/test_desktop_auth.py`, `desktop/tests/auth.e2e.ts`: PG+mock OAuth/system browser staging | state/challenge/pollSecret/expiry/1회교환·재송신·revocation·계정 분리 |
| T12 | `tests/workspace/test_recovery.py`: SQLite lock/full disk·key 없음·kill point·migration | 원본/backup 보존·safe recovery·interrupted/uncertain·새schema 쓰기 차단 |
| T13 | `desktop/tests/install-update.ps1`: clean VM·서명/위조/네트워크 중단·downgrade·remove | 개발 runtime 없이 설치·정상 update/기존binary 유지·project 보존 |
| T14 | `desktop/tests/remote-compat.e2e.ts`: 기존 web+desktop 동시·API timeout/401/429·offline | cookie/Bearer 분리·권한 유지·부분 실패 격리·기존 E 화면 전부 유지 |
| T15 | `scripts/test_desktop_api_compat.py`: 이전 server/current server·schema capability·기존 AI DTO | 기존 응답/권한 의미 유지·지원 안 되는 기능 명시 차단 |

T03/T04의 fake provider 통과는 실제 모델 품질·patch 유효성 보증이 아니다. 실제 모델 smoke·사람 정답 평가·비용/한도는 별도 budget와 동의로 실행한다. 지원 언어별 positive/negative·보안·성능·테스트 누락 사례가 필요하다.

## 구현 후 사용할 명령

아래는 **향후 실행 계획**이다. 신규 파일이 생기기 전 실행하지 않는다. cwd는 repo root. Windows 전용 fixture는 Windows job에서 실행한다.

```powershell
# P01 이후 정의할 npm scripts: host lint/typecheck/unit/build
npm --prefix desktop ci
npm --prefix desktop run lint
npm --prefix desktop run typecheck
npm --prefix desktop test
npm --prefix desktop run build
# workspace 테스트가 작성된 후
.\venv\Scripts\python.exe -B -m unittest discover -s tests/workspace -p 'test_*.py'
# P03 이후, 격리 PG에만 실행
.\venv\Scripts\python.exe -B scripts/test_desktop_auth.py
.\venv\Scripts\python.exe -B scripts/test_desktop_api_compat.py
# P09 이후, 설정된 실제 Electron E2E
npm --prefix desktop run test:e2e
# P10 이후, disposable clean VM
powershell -NoProfile -File desktop/tests/install-update.ps1
```

Python lint/type 검사는 현재 표준이 없으므로 P00에서 팀 기준을 결정하고 추가 의존성·적용 범위를 기록한다. 기존 전체 코드에 일괄 포맷을 적용하지 않는다. host lint/typecheck·agent 검사·package build·해당 단계 테스트가 통과해야 다음 단계로 간다. 없는 npm script나 타입 검사를 현재 통과했다고 기록하지 않는다.

기존 회귀는 `scripts/test_ai_contracts.py`, `test_analytics_calculations.py`, `test_metric_policy.py`, `test_backend_improvements.py`, `test_repository_snapshots.py`, `test_lightweight_score.py`, `test_ai_queue_integration.py`, `verify_ai_mvp_integration.py` 및 `test_ai_frontend.cjs`, `test_metric_display.cjs`, `test_viz_modules.cjs`를 재사용한다. PG는 `gitviz_test` 전용 DB, 큐 검사는 일회용 Redis DB를 사용한다. 기존 CI·스크립트에서 필요한 precondition을 확인하고 운영 DB/Redis로 실행하지 않는다.

## 이번 문서 작업에서 실제 수행한 검증

문서 작성 전 application/config/package/schema 파일과 기존 문서의 SHA-256 기준선 345개를 읽기 전용으로 수집했다. `.env` 내용은 출력·문서화하지 않았다. 작업 전부터 존재한 변경·삭제·신규 문서를 원복하지 않았다. 이번 검증은 2026-10-23 현재 작업 트리에서 수행했다.

| 검증 | 이번 실행 결과 |
|---|---|
| 실제 파일·router·모델·설정·현재 문서 조사 | 수행, 근거 O01~O18 |
| `python -X utf8 -B docs/audit/check_documents.py` | exit 0. 보존 원문 31개·인벤토리 31개·Markdown 64개 도달·로컬 링크/앵커 584건 통과. 파일 존재/구조 검사이며 자연어 사실성 보증 아님 |
| `python -X utf8 -B docs/audit/test_check_documents.py` | 최초 기본 TEMP에서 sandbox 권한 오류(exit 1). 프로세스 TEMP/TMP만 허용된 전용 경로로 바꾼 재실행은 10개 통과(exit 0). repository 설정 변경 없음 |
| 저장하지 않는 표준 Python 문서 검사 | exit 0. 신규 문서 15개, E10/N8/P8·F8/C8/U6/S8/T15, P00~P10 각 단계 필수 항목·code fence·언어 혼입 검사 통과. 의미 일관성은 별도 수동 대조 |
| application/config와 기존 문서 기준선 비교 | 기존 345개 중 `docs/index.md` 외 344개 동일. index는 신규 설계 링크 한 행을 제거한 바이트 hash가 원본과 일치. 신규 파일은 `docs/migration/*.md` 15개만, 기존 파일 삭제 없음 |
| 앱 unit/integration·PG/Redis·migration·서버 시작 | **미실행** — 문서만 작성 |
| Windows installer·실제 Electron·OAuth·AI·editor·runner E2E | **미실행** — 구현 없음 |
| 성능·메모리·실제 모델 품질·최신 원격 CI | **미확인** |

이전 대화의 테스트 통과 수와 현재 [검증 상태](../validation/current-status.md)의 기록은 역사적 근거이며 이번 실행으로 재표기하지 않는다. 이번 변경은 문서 검증만으로 완료 판단한다.

## 2026-10-24 P00 검증 기준 고정

이 절은 예정 인수 기준이다. 이전 절의 2026-10-23 실행 기록과 구분한다. T10/T12의 P01 부분은 다음 gate를 모두 충족해야 종료한다.

| gate | 객관적 기준 |
|---|---|
| 공통 계약 | 같은 fixtures.json을 TS/Python에서 실행해 accept/reject와 오류 분류 일치. 중복key/UTF-8/깊이/길이/추가필드/UUID/타입강제변환 거부 |
| stdio·수명 | split/coalesced frame, stdout 오염, 지연·중복·미등록 응답, timeout, early EOF, 기동 중 부모 종료, 강제 종료 후 잔존 자식0 |
| 실제 창 | sandbox/context isolation/Node 금지 유지, subframe·다른 origin·remote navigation·XSS에서 raw native API/key 접근 불가 |
| import | 독립 agent import/기동이 app.main·서버 DB engine·Celery·서버 secret 설정을 로드하지 않음 |
| 비밀·ACL | 재시작 decrypt, ciphertext/AAD 변조 거부, key 소실·OS 보호/ACL 실패 시 차단. 시험 canary가 일반 로그에 없음 |
| DB | 한 writer·FK·WAL·backup API·transaction migration, lock/full disk·kill point·미지원 schema에서 원본/backup 보존 |
| package | Python/Node 개발 runtime 없는 Windows11 x64 표준 사용자 VM에서 bundled agent 기동·종료. installer 인수와 구분 |
| 기존 기능 | 기존 DTO/계산/지표/UI 순수 회귀 재실행, 승인 범위 밖 파일 hash 불변 |

Node 내장 node:test, Python unittest를 사용한다. TypeScript strict·Ruff·Pyright strict는 신규 desktop/workspace 범위만 검사한다. 기존 전체 코드 포맷이나 lint 정리는 포함하지 않는다. preload는 sandbox에서 실제 빌드 산출물을 로드해 확인한다. 순수 mock 성공으로 실제 창 gate를 대체하지 않는다.

아래 명령은 **미실행 계획**이며 script/config 파일 생성 후 적용한다. cwd는 repo root, 별도 venv를 사용한다. Python fixture의 TEMP/TMP는 허용된 전용 임시 경로를 쓰고 생성한 시험 자료만 정리한다.

```powershell
npm --prefix desktop run lint
npm --prefix desktop run typecheck
npm --prefix desktop test
npm --prefix desktop run test:foundation
npm --prefix desktop run build
.\.validation\venv-workspace\Scripts\python.exe -B -m ruff check --no-cache --config ruff-workspace.toml app/workspace tests/workspace scripts/build_workspace_agent.py
# desktop의 npm script typecheck:agent가 root config를 참조하도록 고정
npm --prefix desktop run typecheck:agent
.\.validation\venv-workspace\Scripts\python.exe -B -m unittest discover -s tests/workspace -p 'test_*.py'
.\.validation\venv-workspace\Scripts\python.exe -B scripts/build_workspace_agent.py
npm --prefix desktop run package
```

`typecheck:agent`는 desktop cwd에서 `pyright --project ../pyright-workspace.json`을 실행한다. build는 컴파일/자산 검사, package는 agent 포함 실행 디렉터리 생성이다. host Electron test는 `test:foundation`에서 실제 프로세스를 실행한다. 사용자 repo의 코드 실행/AI 호출과 분리한다.

P00 전체 정책·의존성 검토는 미완료이며 실제 Windows/보안/성능 통과로 표시하지 않는다. 이번 문서 반영의 명령·종료코드·변경 보호 결과는 [implementation-baseline](validation/implementation-baseline.md)에 기록한다. Q06 언어/parser·Q07 editor·Q10 제품 SLO는 후속 결정이며 15/5/10초 RPC 제한을 제품 성능 측정치로 쓰지 않는다.

## P01 실제 구현 기록

2026-10-24 사용자 승인으로 P01 기반을 구현·검증했다. [실행 기록](validation/p01-2026-10-24.md)에 실제 명령·실패·결과·미실행 gate를 구분한다. 예정 T10/T12 전부 통과나 P01 전체 완료를 뜻하지 않는다.

## P01 잔여 검증 갱신 — 2026-10-24

[잔여 검증 기록](validation/p01-remaining-2026-10-24.md)에22개 중단 경계·SQLite FULL·실제 Electron 복구·clean VM·실제 NTFS 용량 고갈 인수를 기록했다. Python/Node 없는 P01Tester 표준 계정에서8개 앱 검사군과 EXE/ASAR/agent 해시 일치, 별도64MiB 가상 디스크의 여유0바이트·ENOSPC·WAL 롤백·원본/백업 보존·공간 확보 후 복구를 확인했다. 승인된 P01 기반 범위는 구현·검증 완료다. P02 이후·installer/서명/배포·전체 제품 검증은 포함하지 않는다.
