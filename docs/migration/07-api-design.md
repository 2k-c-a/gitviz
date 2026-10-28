# HTTP·IPC 계약 설계

목적: 기존 HTTP를 보존하고 신규 local RPC와 desktop 인증을 구별한다. 근거: [01 O07/O10/O12/O14](01-repository-analysis.md), [기존 AI 계약](../contracts/ai-api.md). 관련: E01~E10·N01~N08·P02/P07, [05](05-functional-specification.md), [06](06-data-model.md), [08](08-system-architecture.md). **아래 workspace RPC와 desktop HTTP는 신규 예정**이다. 인증/배포 정책 Q02/Q10, 외부 전송 Q04는 미확정이다.

## 기존 서버 API 보존

E01~E09는 현재 `/repositories`, `/search/github-repositories`, `/repositories/{id}/analytics/*`, `/repositories/{id}/snapshots*`를 유지한다. E10은 `/api/ai/v1/*`를 유지한다. 상세 method·query·권한은 [요구사항 표](02-requirements-analysis.md)와 기존 계약을 따른다. 기존 cookie 요청·CSRF·Idempotency-Key·HTTP status·UUID/정수 ID·cursor·TTL 의미를 바꾸지 않는다. 새로운 로컬 코드 upload endpoint를 기존 `/inputs`에 몰래 추가하지 않는다.

앱 renderer는 `desktop.remote.request`에 임의 URL을 전달하지 않는다. enum endpointKey와 typed path/query/body만 전달한다. main이 method·서버 origin·path·account·payload 상한을 확인하여 URL을 만든다. 토큰·쿠키·CSRF는 renderer와 agent에 돌려주지 않는다. 웹 transport는 현재 same-origin fetch를 그대로 사용한다.

`createAiClient`의 예정 `authMode`는 기본 cookie이며 기존 웹의 loadCsrf·재발급 동작을 보존한다. desktop 모드만 renderer의 `/api/auth/csrf` 호출과 CSRF 헤더 생성을 생략한다. main은 renderer가 보낸 Authorization/Cookie/Origin/CSRF 헤더를 신뢰하지 않고 허용된 body/query만 받아 인증을 직접 구성한다. renderer에서 기존 OAuth callback·token exchange·web logout을 proxy할 수 없으며 로그인/로그아웃은 desktop.auth.*로만 수행한다. 데스크톱 분석 화면은 명시된 remote mapping이 없으면 연결 안내를 표시하고 기존 repo 기본값 1로 자동 조회하지 않는다.

## 신규 desktop 인증 HTTP

system browser는 기존 OAuth 서버를 사용한다. app은 public client이며 OAuth client secret를 포함하지 않는다. 서버가 신규 bearer를 검증한 뒤 기존 user 조회·repository 권한 검사를 재사용한다.

| method·예정 endpoint | 요청·응답·규칙 |
|---|---|
| `POST /api/desktop/v1/auth/transactions` | main이 난수 verifier를 만들고 `challenge=SHA256(verifier)`·deviceLabel 전달. 201 `{flowId,browserUrl,pollSecret,displayCode,expiresAt,pollAfterMs}`. 인증 전 endpoint이므로 IP/발급 수·크기 rate limit |
| `GET /api/desktop/v1/auth/authorize?flow=<UUID>` | browser만 사용. 기존 로그인 필요 시 OAuth state에 server-side flow binding. 로그인 후 앱명·계정·displayCode를 보여주고 사용자가 승인. GET만으로 인가 확정 금지 |
| `POST /api/desktop/v1/auth/authorize` | browser session+CSRF+flow binding 검사 후 authorized. 본문 `{flowId,displayCode,approve}`. 거절은 종료. main의 pollSecret·verifier는 browser에 전달하지 않음 |
| `POST /api/desktop/v1/auth/exchange` | main이 `{flowId,pollSecret,verifier}`와 Idempotency-Key. pending은 202+Retry-After, authorized면 원자 소비 후 200 `{sessionToken,sessionId,user,capabilities,expiresAt,apiCapabilities}` |
| `GET /api/desktop/v1/session` | bearer session·서버 capability/최소 protocol 검사. expired/revoked 401 |
| `DELETE /api/desktop/v1/session` | bearer가 가리키는 세션 폐기, 204; 반복 폐기도 204. 웹 cookie 로그아웃과 별개 |

flow는 5분, poll 간격 초깃값 2초·Retry-After 준수, token은 8시간 후 재로그인(제안 기본값). pollSecret와 verifier는 각각 32byte 이상 난수. exchange는 row lock·challenge constant-time 비교·user binding·expiry 검사·동일 key 재전송 결과 보관(암호화 60초)으로 분실 응답을 복구한다. 다른 key의 재발급은 409. flow 미존재/권한 실패 원인은 과도하게 구분하지 않는다. 완료 후 secret·verifier를 지운다. token·code를 URL·로그·notification에 넣지 않는다.

일반 OAuth의 redirect는 기존 서버 callback을 유지하고, desktop flow일 때 승인 페이지로 돌아오게 하는 additive 분기만 추가한다. 서버 인증 middleware는 유효 bearer를 `request.state.desktop_principal`에 넣고 기존 `_get_session_user_id`가 검증된 principal 또는 web session을 구분해서 읽게 한다. cookie와 bearer가 혼재하면 거부한다. AI CSRF 예외는 **검증된 desktop bearer 요청만** 가능하며 헤더 존재만으로 면제하지 않는다. web mutation의 Origin/CSRF 검사는 유지한다. 새 middleware 적용 전까지 desktop 인증을 완료로 간주하지 않는다.

## local bridge와 agent RPC v1

renderer→preload→main의 operation allowlist와 main→agent의 stdio 요청은 같은 DTO 개념을 사용하지만, main은 native 동작과 agent operation을 따로 dispatch한다. agent는 shell command 문자열을 처리하지 않는다. 전송은 제한 길이의 UTF-8 JSON line, stdout은 protocol 전용·stderr는 비식별 진단이다. 큰 payload/stream는 chunk sequence와 상한을 사용한다.

```json
{"protocolVersion":1,"requestId":"UUID","operation":"workspace.git.status","projectId":"UUID","payload":{"recentLimit":20}}
```

```json
{"protocolVersion":1,"requestId":"UUID","ok":true,"result":{"projectId":"UUID","head":"full-oid-or-null","branch":"main","staged":[],"unstaged":[],"untracked":[],"conflicts":[]}}
```

```json
{"protocolVersion":1,"requestId":"UUID","ok":false,"error":{"code":"PRECONDITION_FAILED","message":"확인한 뒤 파일이 변경되었습니다.","retryable":false,"details":{"proposalId":"UUID"}}}
```

requestId는 실제 UUID여야 하며 위 UUID 문자열은 형식 설명용이다. protocolVersion 다름·추가 필드·미등록 operation·부모 경로·다른 project 리소스를 거부한다. native folder dialog는 renderer 경로를 받지 않는다. main만 발행하는 approval capability는 renderer에서 생성하거나 보존할 수 없다.

## operation 목록

모든 resource mutation은 Idempotency-Key에 해당하는 `idempotencyKey`를 payload 바깥 공통 옵션으로 사용한다. `(projectScope, operation, key)`와 canonical bodyHash를 묶는다. native dialog 자체는 사용자 취소 가능이며 null 결과는 오류가 아니다. 아래 `desktop.*`는 main 전용이며 agent operation allowlist에 넣지 않는다.

| operation | 입력 → 결과·권한 |
|---|---|
| `desktop.auth.start/status/logout` | 시작·flow 상태·본인 session 폐기 → 계정/만료/상태만 반환; token/pollSecret/verifier 원문 반환 금지 |
| `desktop.remote.request` | endpointKey, typed path/query/body, idempotencyKey → 기존 HTTP 응답의 안전한 body/status/header subset; main만 서버 자격증명 사용 |
| `workspace.projects.selectAndRegister` | native dialog 후 main의 선택 grant → ProjectRef; renderer arbitrary root 금지 |
| `workspace.projects.list/get/updateSettings/unregister` | cursor/ID/version → project 목록/설정; unregister는 local 데이터만, 원본 삭제 없음 |
| `workspace.projects.connectRemote` | projectId, accountId, repositoryId, expectedRemoteIdentity → 확인된 연결; GitHub 조회 실패 시 미연결 |
| `workspace.git.status` | projectId, recentLimit 1..100 → F01 상태 |
| `workspace.changes.capture` | selection(facet,path) → snapshotId, files, inputHash, coverage |
| `workspace.files.preview` | snapshotId,fileId,side,line range → 고정 내용; live 파일은 별도 snapshot 필요 |
| `workspace.reviews.preview/start/get/list/cancel/feedback` | preview는 snapshotId/kind(review 또는 refactor)/purpose/profile → previewHash·manifestHash·범위·destination·credentialVersion·한도. start는 확인한 preview 필드와 명시 동의/key → consent 행과 run을 원자 접수. get/list/cancel은 run ID, feedback은 finding ID/version |
| `workspace.proposals.create/get/reject/requestRevision` | create는 검증 완료 refactorRunId → pending proposal(동일 run 재사용), 모델 재호출 없음. get/reject는 proposalId/version, requestRevision은 instruction으로 새 preview 준비·재동의 필요 |
| `workspace.proposals.approve` | native review confirmation의 proposalId,patchHash,preconditionsHash → approvalId; 원문 capability는 main 내부 |
| `workspace.proposals.apply` | proposalId,approvalId,key → journal/apply status; main이 capability를 agent에 주입 |
| `workspace.proposals.recover` | journalId,expectedCurrentHashes,approvalId → 안전한 선택 복구; 같은 쓰기 승인 규칙 |
| `workspace.editors.list/open/configure` | editorId,projectId,fileId,line,column → launched/reason; executable 설정은 native 확인 |
| `workspace.validation.detect/plan/approve/run/get/cancel` | projectId/snapshotId/commandCandidate 또는 planId → 탐지·계획·run·출력·진단 |
| `workspace.impact.analyze` | snapshotId,optional historySnapshotRef → 근거 목록 |
| `workspace.comparisons.create/get` | before/after snapshot,validation IDs,선택 measurement IDs → 기본/지원 parser 지표 측정·저장 후 F07 결과; 외부 도구는 승인된 run만 참조 |
| `workspace.notifications.list/markRead` | filters,cursor,limit 또는 ID/expectedVersion → 알림·nextCursor |
| `workspace.events.subscribe/unsubscribe` | project filters,afterSequence → main의 검증된 event 채널; raw IPC event 객체 노출 금지 |

읽기 목록 limit 기본 30/최대100, cursor는 createdAt+UUID 기반 opaque 값이며 필터 hash를 포함한다. 다른 필터 cursor는 422 상당 오류다. local notifications는 모든 등록 project를 볼 수 있지만 target resource는 클릭 시 project FK를 다시 검증한다.

### 승인·실행 예시

```json
{"operation":"workspace.validation.plan","projectId":"UUID","payload":{"snapshotId":"UUID","candidateId":"python-unittest"}}
```

결과는 `{planId,planHash,executable,argv,cwdRelative,configHash,timeoutMs,outputLimit,risk}`다. 사용자가 이를 확인하면 approve가 해당 hash의 approvalId를 만든다. run은 `{planId,approvalId}`만 받고 argv/cwd를 재정의할 수 없다. proposal 적용도 patchHash를 고정한다. approval은 생성 후 5분 내 사용·단일 목적·단일 적용이고, 재시작 후 capability는 복원하지 않는다.

## 오류·검증·재시도

local error code: `INVALID_REQUEST`, `UNSUPPORTED_PROTOCOL`, `NOT_A_REPOSITORY`, `GIT_UNAVAILABLE`, `PROJECT_UNAVAILABLE`, `PATH_OUTSIDE_SCOPE`, `ACCESS_DENIED`, `RESOURCE_NOT_FOUND`, `CAPTURE_CHANGED`, `PRECONDITION_FAILED`, `APPROVAL_REQUIRED`, `APPROVAL_EXPIRED`, `CONFLICT`, `BUSY`, `INPUT_EXPIRED`, `INPUT_TOO_LARGE`, `TRANSFER_BLOCKED`, `AUTH_REQUIRED`, `RATE_LIMITED`, `PROVIDER_UNAVAILABLE`, `OUTPUT_INVALID`, `OUTCOME_UNCERTAIN`, `COMMAND_NOT_ALLOWED`, `TIMED_OUT`, `CANCELLED`, `RECOVERY_REQUIRED`, `STORAGE_UNAVAILABLE`.

local RPC에는 HTTP status가 없다. renderer transport가 typed error로 매핑한다. 기존 HTTP의 AI envelope와 non-AI HTTPException을 읽을 때 두 형태를 지원하되 계약을 변경하지 않는다. path·stderr·provider 응답·SQL exception을 사용자 오류에 원문 복사하지 않는다.

read는 제한된 backoff 가능, mutation은 같은 key/body로 접수 상태만 확인한다. apply/run/외부 호출을 timeout만 보고 새 key로 재실행하지 않는다. terminal cancellation과 cancellation_requested를 구분한다. event는 `{sequence,eventId,projectId,type,resourceId,sourceRevision}`이며 sequence gap 시 list를 다시 조회하고 notification DB를 진실 원천으로 삼는다.

## STEP-P01 RPC v1 고정 기준

2026-10-24 P00 문서 반영 기준. 아래 추가 system operation은 신규 예정이며 기존 HTTP 계약과 분리된다. 공통 원본은 예정 `contracts/workspace/v1/*.schema.json`(JSON Schema Draft-07), host DTO는 예정 `desktop/contracts/workspace.ts`다. main과 agent가 같은 정상/거부 fixture를 각각 검증한다. TypeScript 선언만으로 입력을 신뢰하지 않는다.

| 항목 | 고정 기준 |
|---|---|
| 공통 요청 | protocolVersion=1, requestId(UUID), operation(enum), payload(object); operation별 projectId/idempotencyKey 필요 여부 고정 |
| 엄격 검증 | 모든 object에 추가 필드 거부, 타입 강제 변환·기본값 자동 삽입 금지; UUID는 공통 pattern으로 검증 |
| JSON parser | 중복 key·잘못된 UTF-8·NaN/Infinity·빈 프레임 거부. schema 검증 이전 parser도 검사 |
| framing | 개행 제외 UTF-8 최대 262144 bytes, 중첩 object/array 최대16, 분할 수신·여러 프레임 수신 처리 |
| 자원 제한 | agent 미완료 요청 최대16, 초과 BUSY; 읽는 도중 상한 초과 시 무제한 buffer 적재 금지 |
| 시간 | spawn부터 ready까지15초, health5초, 정상 종료5초+강제 정리 포함 총10초 |
| 응답 | requestId·protocolVersion·ok 및 operation 결과 schema 검사; 알 수 없는/중복 완료 응답은 protocol 장애 처리 |
| 초기화 실패 | ready 전 업무 operation 거부, protocol/schema 불일치 시 DB 초기화·쓰기 차단 |
| 장애 | P01 자동 재시작 없음. 상태 표시 후 명시 재시도; 응답 없음은 작업 취소/실패 확정과 다름 |

요청 ID를 안전하게 복구할 수 있으면 정규 오류로 답한다. 잘못된 UTF-8·깨진 JSON·상한 초과 등 상관관계를 신뢰할 수 없으면 원문을 로그에 남기지 않고 채널을 닫아 unavailable로 전환한다. 준비되지 않은 future operation은 capability에 광고하지 않으며 allowlist에서 거부한다. list/cursor·approval·mutation 계약은 기존 규칙을 유지하지만 P01에서는 구현하지 않는다.

| operation | 주체·payload·result |
|---|---|
| workspace.system.handshake | main 전용; payload={appVersion,protocolVersion:1,localSchemaRange:{min,max}} → {agentVersion,protocolVersion:1,localSchemaRange:{min,max},capabilities:string[],state:"initializing"}. version은 길이 제한 문자열, schema 정수>=1; 범위 교집합이 없으면 차단 |
| workspace.system.health | main 경유; payload={} → {state:"ready" 또는 "degraded" 또는 "unavailable",components:{store,secrets,lifecycle},errorCode?:기존 local code}. 각 component는 같은 상태 enum, 경로·key·stderr 제외 |
| workspace.system.shutdown | main 전용; payload={} → {accepted:true}; 이후 정상 pipe 종료와 실제 프로세스 종료를 별도 확인 |

handshake 성공은 ready를 의미하지 않는다. 협상→내부 bootstrap→OS 수명/ACL/SecretStore 확인→DB version/backup/migration 검사→ready 순서다. renderer에는 getHealth()만 노출하며 handshake/shutdown/bootstrap·임의 operation invoke는 노출하지 않는다.

bootstrap은 main→agent 상속 pipe의 단일 내부 초기화 프레임이며 public RPC dispatch와 분리한다. 예정 bootstrap schema는 {type:"bootstrap",protocolVersion:1,keyVersion:양의정수,keyBase64:32byte key의 base64}이고 추가 필드를 거부한다. handshake 이후 한 번만 받고 기동 deadline·프레임 상한을 적용한다. main이 인증된 자식에만 전달하며 renderer 입력은 이 프레임으로 변환될 수 없다. key는 argv·환경변수·일반 로그에 넣지 않고 영속 평문 저장하지 않는다. agent가 필요한 작업용 key를 메모리에서 쓰는 것은 main의 OS key 저장 소유권과 구분한다. 동일 사용자 악성 프로세스 완전 격리를 보장하지 않는다.

향후 chunk payload는 별도 schema·sequence·총량·취소 규칙을 해당 단계에서 고정한다. 256KiB 제한을 늘려 source 전달 계약을 우회하지 않는다. 제한값은 검증할 초기 기준이며 성능 실측 결과가 아니다.

## STEP-P04 고정 입력·탐지 계약 — 2026-10-25

P04 승인 범위에서 local schema3와 다음4개 capability를 추가한다. protocolVersion=1·기존 system/프로젝트 계약은 유지한다. host/agent는 schema3..3을 협상하며 schema1/2 fixture는 backup 뒤 transaction으로 올린다. 실제 사용자 DB migration·freeze·패키징은 별도 실행 승인이다. P03의 실제 서버 OAuth·웹/앱 동시 인수 잔여와 별개다.

| operation | project-scoped payload·결과 |
|---|---|
| `workspace.changes.capture` | `{items:[{facet,path}],includeUntracked}`+공통 idempotencyKey. facet=staged/unstaged/untracked/selected,1..50개. HEAD/index/전체 변경 목록 및 선택 파일 pre/post를 비교하고 snapshotId·inputHash·coverage·facet별 fileId/제외 사유를 반환 |
| `workspace.files.preview` | `{snapshotId,fileId,side,startLine,endLine}`.1회200줄 이하, LEFT/RIGHT의 고정 diff 문맥만 반환. live 경로를 받지 않으며 다른 project/제외/만료 입력 거부 |
| `workspace.validation.detect` | `{cwdRelative,packageManager}`. root는 `.`, 하위 cwd는 엄격 상대경로. manager=null/npm/yarn/pnpm. manifest 근거·framework 선언·lock 충돌·candidateId·도구 유무를 반환. import/실행/설치 없음 |
| `workspace.validation.plan` | `{snapshotId,candidateId}`+공통 idempotencyKey.5분짜리 main/agent 내부 candidate·동일 project/snapshot/config를 검증해 불변 planId/hash·executable/argv/cwd·pre/post scripts·한도·위험을 반환. executionAllowed=false, approve/run capability는 아직 없음 |

한 파일 원문200000bytes, 캡처 보관 전후 합2000000bytes, metadata pin2만 항목, 기존 Git command4초/출력 상한을 유지한다. 캡처는45초 안에 검사를 마치며 host deadline은60초다. system5초·나머지15초는 유지한다. 단일 agent의 동기 캡처 중 다른 요청이 지연될 수 있으며 제품 SLO 측정치는 아니다. 전체 RPC frame256KiB·응답200000bytes 상한을 늘리지 않는다.

원문·diff·코드가 포함된 줄은 project/snapshot/file/schema AAD로 암호화하고 SQL line_map에는 번호/종류만 둔다. 경로/제외/해시는 ACL 보호 metadata다. 정책은 기존 code-transfer-v1을 재사용하되 원문 양쪽 전체를 검사한다. Q08 보존기간은 미정이므로 임의 TTL 삭제를 추가하지 않고 `retention-policy-pending`을 표시한다. 만료값이 지정된 입력은 preview/plan에서 거부한다. local snapshot UUID를 remote PRRevision/Repository ID로 사용하지 않는다.

## STEP-P05 PC 로컬 리뷰 계약 — 2026-10-26

P05 코드는 local schema4(기존 snapshot AAD schema3 유지)를 협상하며 reviews.preview/start/get/list/cancel/feedback의 6개 project-scoped capability를 추가한다. 정확한 필수 필드·추가 필드 거부·상한은 공통 contracts/workspace/v1의 request/response schema를 따른다. 기존 system/project/changes/validation RPC와 protocolVersion=1·256KiB frame·200000byte 결과 한도를 유지한다. 임시 fixture schema migration은 별도 승인 후 통과했고 실제 사용자 DB는 변경하지 않았다.

preview는 snapshotId/kind=review/purpose/profileId/generation으로 실제 system/user와 해시·범위를 반환한다. start는 previewId/previewHash/manifestHash/maxEstimatedCost/acknowledged=true/duplicateRiskAcknowledged와 operation key를 받는다. get/cancel은 runId, cancel은 expectedVersion·operation key가 필요하다. list는 offset/limit(최대50), feedback은 findingId/expectedVersion/verdict/note(최대1000자)·operation key를 받는다. feedback은 원본 결과를 수정하지 않는다. 미리보기/동의 원문과 feedback note를 operation JSON에 평문 저장하지 않는다.

현재 profile은 PC의 고정 Ollama 경로만 활성화한다. 공개 매핑 proof가 없는 cloud는 실행 경계에서 차단한다. 실제 모델 호출·품질·PC 모델 설치를 이번 내부 검증에 포함하지 않는다. provider timeout은90초 절대 deadline과 bounded output, 외부 proxy/redirect/임의 URL·명령/tool 차단을 적용한다. 취소는 이미 전송된 내용 회수를 보장하지 않으며 uncertain 예약은 자동 해제/재전송하지 않는다. 전송 본문 hash와 출력 schema hash를 승인 manifest/profile에 포함한다. 임시 DB·source/frozen agent·독립 검증 패키지의 fake provider 검사는 통과했다. 신규 실패 코드는 공통 response enum과 TS/Python fixture에 고정했고 차단 오류 뒤 다음 요청도 유지한다. 실제 모델 품질·cloud BYOK 활성화는 검증 완료가 아니다.

## STEP-P06 리팩토링·영향·승인 적용 계약

local schema5를 협상하고 protocolVersion=1과 기존 frame/출력 한도를 유지한다. reviews.preview/start는 kind=refactor와 workspace-refactor-v1 출력 검증을 지원하며 기존 review 계약도 유지한다. proposals.create/get/reject/requestRevision/approve/apply/listRecovery/recoveryPlan/approveRecovery/recover와 impact.prepare/read/append/finish/get을 추가한다. 정확한 필드·enum·상한은 공통 request/response JSON schema를 따른다. TypeScript source chunk 및 approve의 opaque token은 main 내부 전용이며 renderer에는 전달하지 않는다. renderer에는 명명된 제안·영향 API만 공개한다.

원본 쓰기는 native 확인 후 main이 발행한 일회 capability·5분 만료·project/proposal/patchHash/preconditionsHash와 묶는다. 적용/복구는 60초 deadline이며 중복 operation key는 기존 결과만 반환한다. 재시작은 미소비 승인을 폐기하고 복구 필요 상태를 표시하며 원본을 쓰지 않는다. 두 단계 게시 예외와 수동 확인 범위는 F03 및 P06 검증 기록을 따른다. 영향 분석은 고정 snapshot의 Python AST와 고정 TypeScript 6.0.3 worker에 한정하고 프로젝트 코드·설정·plugin을 실행하지 않는다. 원격 이력 미연결은 unavailable로 표시하며 AI 예상 효과를 관측 사실로 바꾸지 않는다.


## STEP-P07 실행·에디터·비교 계약

local schema6과 protocolVersion1을 협상하며 총44개 named operation을 제공한다. validation.prepare/approve/start/get/cancel, comparisons.create/get, files.locate, projects.setEditor, impact.release가 추가된다. 승인 capability·worker source chunk·TypeScript measurements는 main 내부 전용이다. renderer의 runValidation/createComparison에는 capability·임의 명령·측정치를 받지 않는다. 정확한 필드·추가 필드 거부·상한은 공통 schema 및 TS/Python fixture가 권위다.

command_plan은 실행파일·argv·cwd·pre/post script·설정 hash·고정 환경·120초·1MiB를 포함하며 plan 자체의 executionAllowed=false는 native 승인 전 권한이 없음을 뜻한다. 기존 environment가 없는 plan은 재탐지/새 계획이 필요하다. main native 취소는 실행하지 않고, trusted=true와 일회 token은 승인 뒤 main에서만 보낸다. project/plan/confirmation hash·5분 만료·단일 소비와 idempotency를 검사한다. stdout/stderr는 순서·원본 bytesBase64와 표시 text를 분리하고 get은 최대10 chunk이다. get/prepare/approve/start와 비교 생성은60초 RPC 상한이며 실제 명령 한도와 다르다.

files.locate는 snapshot/file/line/column을 확인하고 main에만 실제 경로를 반환한다. editor 설정은 native 선택 및 signed publisher/product/hash 확인으로 등록하고 고정 --goto argv로 파일·위치를 연다. renderer에는 범용 파일/프로세스/IPC proxy가 없다. 비교의 TypeScript 기록은 fixed utility worker가 읽은 동일 fileId/facet/path/sourceHash와 묶어 검증한다. 영향 source session은 release로 정리한다. 사용자 실행 도구는 설치된 runtime이며 자동 설치·수정 모드를 생성하지 않는다.

## 실제 계약과의 차이 — 2026-10-28

공통 스키마 `contracts/workspace/v1/*.schema.json`이 local RPC의 권위 원본이다. 현재 operation은 44개다. 위 표와의 차이는 다음과 같다.

- 에디터: `workspace.editors.list/open/configure` 대신 main 프로세스 IPC(`configure-editor`, `open-editor`, `copy-location`)와 agent의 `workspace.files.locate`·`workspace.projects.setEditor`로 구현했다. 실행 파일 경로는 main 전용 설정에만 둔다.
- 미구현: `workspace.projects.connectRemote`, `workspace.notifications.list/markRead`, `workspace.events.subscribe/unsubscribe`. notifications·events는 P08a, connectRemote는 P08b 범위다.
- 오류 코드: 위 목록 외에 스키마에 27개가 추가됐다. `AMBIGUOUS_FACETS`, `CHANGESET_TOO_LARGE`, `CLOUD_MAPPING_REQUIRED`, `CONSENT_EXPIRED`, `CONSENT_REQUIRED`, `CONTENT_EXCLUDED`, `DUPLICATE_RISK_CONFIRMATION_REQUIRED`, `EMPTY_INPUT`, `GIT_NOT_FOUND`, `HISTORY_MAPPING_REQUIRED`, `INDEX_UNAVAILABLE`, `METADATA_GRANT_REQUIRED`, `OUTPUT_LIMIT`, `PATCH_ENCODING_UNSUPPORTED`, `PATCH_INVALID`, `PATCH_SCOPE_UNSUPPORTED`, `PATCH_TOO_LARGE`, `PROJECT_MOVED`, `PROJECT_RECONNECT_REQUIRED`, `RECOVERY_CONFLICT`, `REVIEW_DATA_REQUIRES_EXPLICIT_REMOVAL`, `ROOT_GRANT_REQUIRED`, `SELECTION_EXPIRED`, `UNSUPPORTED_FILENAME`, `UNSUPPORTED_KIND`, `UNSUPPORTED_REPOSITORY`, `WINDOWS_REQUIRED`. 모두 추가만 한 변경이다.
- `CHANGESET_TOO_LARGE`의 details는 `operation`과 `limitBytes/observedBytes` 또는 `limitItems/observedItems`만 담고 경로를 담지 않는다(D-07, [Stage D 기록](validation/stage-d-2026-10-28.md)).
- `workspace:review-start`는 main이 기억한 preview(projectId·previewHash·manifestHash·maxEstimatedCost 일치)에 대해 네이티브 확인을 받은 뒤에만 agent로 전달한다(D-02).
