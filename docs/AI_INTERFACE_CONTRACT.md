# AI 인터페이스 계약 (S0)

- 계약 버전: `1.7` (`app/schemas/ai.py`의 `CONTRACT_VERSION`)
- 설계 근거: [요구사항 및 시스템 설계 v0.4](GitViz_AI_요구사항_및_시스템설계_v0.4.md), [구현 계획 v0.5](GitViz_AI_의존성기반_구현계획_v0.5.md) S0
- 수집·백엔드·시각화는 모두 `app/schemas/ai.py`의 DTO를 기준으로 합니다. 이 문서와 코드가 다르면 코드가 우선이며 즉시 문서를 고칩니다.
- 모든 DTO는 알 수 없는 필드를 거부합니다(`extra="forbid"`). 클라이언트가 `owner_user_id` 같은 값을 보내도 받지 않습니다.
- 기본 scope는 본인만 접근하는 개인 scope입니다. 팀 scope는 S13에서 명시 필드로 추가하며, 그 전에는 DTO에 scope 필드를 두지 않습니다.

## 1. 식별자와 코드 위치

| 값 | 규칙 |
|---|---|
| Git SHA / blob OID | 소문자 hex 40자(sha1) 또는 64자(sha256). 대문자·짧은 SHA 거부 |
| 해시(`content_hash`, `manifest_hash`) | sha256 소문자 hex 64자 |
| `repo_id` | 정수, 1 이상 (기존 `repositories.id`) |
| AI 리소스 ID | UUID |
| 경로 | 저장소 상대 경로, `/` 구분. 빈 구간·`.`·`..`·`\`·NUL·절대 경로 거부 |

`PRRevision = {repo_id, pr_number, base_sha, head_sha, merge_base_sha}` — 모두 필수입니다. merge-base를 확정할 수 없으면 revision을 만들지 않습니다.

`CodeLocation = {path, side, start_line, end_line, revision_id}`

- `side`는 `LEFT`(변경 전) / `RIGHT`(변경 후).
- 줄 번호는 실제 commit의 1부터 시작하는 번호입니다. `start_line >= 1`, `end_line >= start_line`.
- 삭제 파일은 `LEFT`만, 새 파일은 `RIGHT`만 줄을 가집니다(`ManifestFile.sides`). 모델이 만든 번호나 diff 화면 순번을 쓰지 않습니다.

## 2. Coverage

세 값은 서로 다른 분모를 가리키며 `job.status`와 별개입니다.

| 필드 | 값 | 의미 |
|---|---|---|
| `inventory_coverage` | complete / partial / unknown | PR 전체 파일 목록을 확보한 정도 |
| `selected_content_coverage` | complete / partial / none | 선택한 파일의 코드를 확보한 정도 |
| `analysis_coverage` | complete / partial / none | 해당 기능이 실제 다룬 선택 범위 |
| `selected_files` | 정수 ≥ 0 | 선택 파일 수 |
| `pr_total_files` | 정수 ≥ 0 또는 null | PR 전체 파일 수. `unknown`이면 반드시 null, `complete`이면 필수 |
| `limitations` | `Limitation[]` | 중복 없는 제외·누락 사유 |

일관성 규칙: `selected_files <= pr_total_files`, 코드를 확보하지 못한(`none`) 범위는 분석 범위가 될 수 없고, 일부만 확보(`partial`)한 코드를 `complete`로 분석할 수 없습니다.

화면 표기 예: 선택 3개 · PR 전체 8개 → "선택 범위 3/3, PR 전체 3/8". 전체 목록이 `unknown`이면 분모는 "알 수 없음"입니다.

`Limitation`: `not_selected`, `unsupported_language`, `too_large`, `truncated`, `generated_file`, `binary`, `link_or_submodule`, `secret_suspected`, `fetch_failed`, `analysis_failed`. 파일별 `exclusion_reason`과 coverage가 같은 enum을 씁니다.

## 3. InputManifest

사용자에게 보이는 전송 미리보기이며 실제 AI 전송 후보와 같아야 합니다.

`{contract_version, revision_id, revision, policy_version, pr_metadata_hash?, files[]}`

파일 항목: `path, old_path, change_type(added|modified|deleted|renamed), old_blob_oid, new_blob_oid, content_hash, language, content_state(read|excluded|failed), exclusion_reason, hunks[{old_start,old_lines,new_start,new_lines}]`

- 모든 파일은 `read` / `excluded` / `failed` 중 하나입니다. 숨겨진 누락이 없습니다.
- `read`는 `content_hash`가 필수이고 `exclusion_reason`이 없습니다. `excluded`/`failed`는 `exclusion_reason`이 필수이고 `content_hash`가 없습니다.
- `added`는 `old_path`/`old_blob_oid` 없음, `deleted`는 `new_blob_oid` 없음, `renamed`는 `path`와 다른 `old_path` 필수. 그 외 `old_path`는 사용하지 않습니다.
- 같은 `path`가 두 번 나올 수 없습니다.
- `canonical_hash()`: 키를 정렬한 공백 없는 JSON(UTF-8)의 sha256. 필드 입력 순서와 무관하지만 **배열 순서는 의미가 있습니다**(파일 순서가 바뀌면 해시가 바뀜).

## 4. 요청

`PrepareInputRequest` (POST `/inputs`): `repository_id, pr_number, expected_base_sha, expected_head_sha, selected_paths`. `selected_paths`는 1개 이상(최대 1000), 중복 불가. 선택 0개는 `INVALID_SELECTION`으로 매핑합니다.

## 5. 작업 상태

- `status`: `queued → running → succeeded | failed | cancelled`, 일시 오류 `retry_wait`, 취소 요청 `cancel_requested`. 종료 상태는 되돌리지 않습니다.
- `stage`: `input_preparation / queued / provider_call / output_validation / result_storage`. 종료된 작업은 stage가 없습니다. 모델 진행률을 백분율로 만들지 않습니다.
- `JobState`: 생성 작업(`summary`/`review`)은 `succeeded`이면 `result_id` 필수, 아니면 금지. `prepare`는 AI 결과가 없으므로 항상 `result_id`가 없다(성공해도 입력 준비만 완료). `failed`이면 `error_code` 필수, `succeeded`이면 금지. `retry_after_seconds >= 0`.
- 입력 상태 `InputStatus`: `building → ready | failed`, 만료 시 `expired`. `ready`는 입력이 고정됐다는 뜻이지 coverage가 완전하다는 뜻이 아닙니다.

### 작업 `error_code` (S4, `failed` 작업의 값)

| 값 | 의미 |
|---|---|
| `NO_HANDLER` | 해당 종류를 처리할 handler가 worker에 없다(재시도 안 함) |
| `INTERNAL_ERROR` | handler의 예상 못 한 예외(재시도 안 함. 원문 예외는 저장하지 않는다) |
| `RESULT_STORAGE_FAILED` | 결과 저장 transaction 실패(재시도 가능) |
| `LEASE_EXPIRED` | 처리자가 사라져 처리권이 만료됨(attempt 기록에만 남고 재시도 대기로 전환) |
| `ATTEMPTS_EXHAUSTED` | 최대 시도 횟수(`AI_MAX_ATTEMPTS`) 소진 |
| `QUEUE_TIMEOUT` | 대기열에서 `AI_QUEUE_DEADLINE_SECONDS`를 넘김 |
| `RESULT_LOST` | AI 호출은 처리·정산됐지만 결과를 저장하기 전에 처리자가 사라짐. 같은 작업으로 다시 호출하지 않으며 사용자가 명시적으로 재시도(새 generation)해야 한다 |
| `OUTCOME_UNCERTAIN` | AI 호출 중 처리자가 사라져 결과를 알 수 없음. 새 호출로 이어가지 않으며 사용자가 명시적으로 재시도(새 generation)해야 한다 |

생성 작업(S7)의 코드는 §13을 따른다.

그 외 handler가 정하는 코드(`TEMPORARY` 등)는 해당 단계의 문서에서 정의한다. 재시도·취소 규칙은 다음과 같다.

- 일시 오류는 `running → retry_wait → queued`이며 대기 시간은 `AI_RETRY_DELAY_SECONDS`부터 2배씩(최대 600초) 늘어난다.
- 취소가 접수된 작업(`cancel_requested`)은 처리자가 성공을 시도해도 `cancelled`로 확정하고 결과를 저장하지 않는다.
- 같은 `Idempotency-Key`+같은 요청은 같은 작업을, 다른 요청은 `IDEMPOTENCY_CONFLICT`를 낸다. 새 key라도 같은 fingerprint의 진행 중·성공 작업은 재사용하고, 실패·취소·강제 재생성은 새 `generation`이다.

## 6. Finding

`ordinal(≥1), title, category, priority(정수 ≥1, 1이 먼저 검토), condition, impact, evidence_ids(1개 이상), verification, suggestion?, limitations[], location, human_validation`

- `priority`는 검토 순서이며 장애 등급이 아닙니다.
- `human_validation` 기본값은 `unverified`입니다. 구조·위치 검증 통과는 내용이 맞다는 뜻이 아니며 자동으로 `valid`로 바꾸지 않습니다. 사람의 판정은 `Verdict`(valid/false_positive/uncertain) 피드백으로만 들어옵니다.

## 7. 오류

```json
{"error": {"code": "PR_CHANGED", "message": "PR이 변경됐습니다.", "retryable": true, "request_id": "...", "details": {}}}
```

비밀·다른 이용자의 존재·제공자 원문 오류를 `details`에 넣지 않습니다. code→HTTP status는 `ERROR_HTTP_STATUS`가 단일 출처입니다.

| HTTP | code |
|---|---|
| 401 | AUTH_REQUIRED, REAUTH_REQUIRED |
| 403 | TRANSFER_NOT_ALLOWED, CSRF_FAILED |
| 404 | RESOURCE_NOT_FOUND (권한 밖 자료도 동일) |
| 409 | PR_CHANGED, PREVIEW_CHANGED, VERSION_CONFLICT, IDEMPOTENCY_CONFLICT |
| 410 | INPUT_EXPIRED, RESULT_EXPIRED |
| 413 | INPUT_TOO_LARGE |
| 422 | INVALID_SELECTION, INVALID_STATE, CREDENTIAL_REQUIRED, CREDENTIAL_INVALID |
| 429 | USER_LIMIT, BUDGET_LIMIT, UPSTREAM_RATE_LIMIT |
| 503 | ACCESS_CHECK_UNAVAILABLE, UPSTREAM_UNAVAILABLE |

설계 v0.4 표의 `502/503` 중 이번 계약은 503만 사용합니다. 502가 필요하면 다음 minor 버전에서 추가합니다.

## 8. 버전 규칙

- 필드·enum 값 **추가**는 minor 증가, 의미 변경·삭제·이름 변경은 major 증가이며 소비자 합의가 필요합니다.
- 1.1(S5): `InputManifest.pr_metadata`(AI로 보내는 PR 제목·설명) 추가. 기존 필드는 그대로이며 manifest_hash에 PR 설명이 반영된다.
- 1.2(BYOK): 사용자 key 관리 DTO(`CredentialView` 등)·`TransferConsent`·`InputPreview.transfer` 추가, ErrorCode `CREDENTIAL_REQUIRED`·`CREDENTIAL_INVALID`(422) 추가.
- 1.3(S8): 생성·작업·결과·지적·피드백·사용량 API DTO(`CreateJobRequest`, `RetryJobRequest`, `JobView`, `JobList`, `ResultView`, `FindingView`, `FindingList`, `FeedbackRequest`, `FeedbackView`, `UsageView`) 추가. 기존 필드·enum은 그대로다.
- 1.4(S9): PR·파일 선택 조회 DTO(`PullSummary`, `PullList`, `PullFile`, `PullFilesView`)와 `UpdateCredentialRequest` 추가. 기존 필드·enum은 그대로다.
- 1.5: OpenAI·Gemini BYOK, 같은 PC의 Ollama keyless 실행. 모델·transfer의 `requires_api_key`/`execution_location`, transfer의 `model_digest`/`profile_fingerprint`, 동의의 선택 `profile_fingerprint`(로컬 필수), 로컬의 null `credential_version`, 미리보기 `profile_id` 선택을 추가한다. 클라우드 key 버전 검사는 유지한다. 자세한 설정·비용·주소·복구 규칙은 [제공자 지원](AI_PROVIDER_SUPPORT.md)에 정의한다.
- 1.7: 인수인계(S12). 인계 대상·준비 체크폼·초안 생성·편집 문서·Markdown 내보내기 API를 추가한다(17절). 기존 요청은 영향이 없다.
- 1.6: GitViz 이력 근거(S11). 작업 생성의 선택 `history_snapshot_id`와 `/history/snapshots`, `/results/{id}/history` 조회·생성을 추가한다. 기존 요청과 멱등 key는 그대로 유효하다. 상세는 16절.
- 이후 단계에서 임의로 enum/필드 의미를 바꾸지 않습니다. 변경은 `CONTRACT_VERSION`, 이 문서, `scripts/test_ai_contracts.py`를 같은 PR에서 고칩니다.
- 기능별 출력 schema 버전(S6)과 API 응답 DTO(S5·S8)는 이 계약 위에 추가하며 위 정의를 재정의하지 않습니다.

## 9. 테스트와 fixture

- `python scripts/test_ai_contracts.py` — DB·네트워크 없이 실행. DTO 검증, 줄 맵, canonical hash, 테스트 DB 가드.
- `scripts/fixtures/ai/sample.diff` + `line_maps.json`: add / delete / rename+수정 줄 맵. `valid_findings.json`, `invalid_findings.json`: 정상·비정상 Finding.
- `scripts/ai_test_support.py`의 `require_test_database()`는 `gitviz_test`로 시작하는 DB만 허용하고, 앱 import **전에** `DATABASE_URL` 등을 설정합니다. 오류 메시지에 URL(비밀번호)을 넣지 않습니다.
- `parse_line_maps()`는 fixture 검증용입니다. 실제 수집기의 줄 맵(S3)은 같은 fixture를 통과해야 합니다.

## 10. 입력 준비 API (S5)

모든 응답은 `Cache-Control: private, no-store`이며 오류는 `ErrorEnvelope`입니다(요청 형식 오류도 `INVALID_SELECTION`, 문제 필드 이름만 `details.fields`에 담고 입력값은 되돌려 주지 않음). 변경 요청(POST/DELETE)은 CSRF와 Origin 검사를 거치고, POST는 `Idempotency-Key`가 필수입니다. AI 제공자는 호출하지 않으며 `AI_ALLOW_EXTERNAL_TRANSFER=0`이어도 동작합니다. 비공개 저장소도 준비할 수 있습니다(외부 전송만 막힘).

| Method / path | 설명 |
|---|---|
| POST `/api/ai/v1/inputs` | 본문은 `PrepareInputRequest`. PR 리비전을 고정하고 `building` 입력과 prepare 작업을 접수한다. 새로 만들었거나 진행 중이면 202, 이미 준비된 입력을 재사용하면 200. 응답은 `InputPreview` |
| GET `/api/ai/v1/inputs/{id}` | 상태·전송 후보(manifest)·제외 사유·기한·토큰 예상·profile·policy. 작업이나 코드 취득을 시작하지 않는다 |
| GET `/api/ai/v1/inputs/{id}/files/{file_id}?side=&start_line=&end_line=` | 보관된 고정 diff(맥락 25줄 포함)를 줄 번호와 함께. `side`(LEFT/RIGHT)와 범위는 함께 지정(범위는 side 필수). 삭제 파일은 LEFT만, 새 파일은 RIGHT만. `InputFileContent` |
| DELETE `/api/ai/v1/inputs/{id}` | 202. 연결된 진행 중 작업을 취소하고 원문을 삭제한다. 입력 행·manifest·이미 만든 결과는 남는다. 본인 자료 삭제라 현재 저장소 접근 권한과 무관 |

- 오류: 로그인 없음 401, 기능 꺼짐·다른 작성자·권한 상실 404(`RESOURCE_NOT_FOUND`), CSRF 403, 같은 key의 다른 요청 409(`IDEMPOTENCY_CONFLICT`), 미리보기 이후 PR 변경 409(`PR_CHANGED`), 선택 파일 수·요청 크기 초과 413, 원문 만료·삭제 410(`INPUT_EXPIRED`, files 엔드포인트), 권한 확인 장애 503. 코드를 읽는 GET은 매번 GitHub 접근 권한을 다시 확인한다.
- `InputPreview.status`: `building → ready | failed`, 원문 보관 기한이 지나면(또는 삭제하면) `expired`. `ready`여도 `execution_valid=false`일 수 있다(실행 유효기간 `AI_INPUT_VALID_HOURS`=24시간 경과, 원문은 `expires_at`=`AI_INPUT_RETENTION_DAYS`=7일까지 보관). 준비 제한은 `AI_PREPARE_TIME_LIMIT_SECONDS`=120초(`PREPARE_TIMEOUT`).
- `manifest_hash`가 생성 요청의 `consent_manifest_hash`로 되돌아오는 값이다. 미리보기 manifest는 실제 AI 전송 후보와 같다(파일별 `content_hash`·제외 사유·언어, PR 제목·설명 포함). `token_estimate`는 mock 추정(bytes/4+overhead)이며 모델 profile별 확인은 실행 직전에 다시 한다.
- `coverage`의 세 분모: `inventory_coverage`/`pr_total_files`(PR 전체), `selected_files`·`selected_content_coverage`(선택과 확보), `analysis_coverage`(항상 `none`, 분석 전). 읽을 수 있는 파일이 없으면 `reviewable=false`(NO_REVIEWABLE_CONTENT, 생성 작업을 만들 수 없다).
- prepare 작업 `error_code`(S5): `PR_CHANGED`(준비 중 PR 변경, 재시도 안 함), `INVALID_SELECTION`, `INPUT_TOO_LARGE`, `MERGE_BASE_UNAVAILABLE`, `RESOURCE_NOT_FOUND`/`REAUTH_REQUIRED`(권한 상실), `PREPARE_TIMEOUT`. 입력이 `failed`가 된다. 일시 오류(`UPSTREAM_UNAVAILABLE` 등)는 입력을 `building`으로 둔 채 재시도한다.
- 보관: 코드 원문은 AES-GCM으로 암호화되어 `ai_input_files`에만 있고(AAD=`input_id:file_id`) 로그·큐·audit에는 없다. 확정된 입력은 파일을 바꾸거나 더할 수 없다(새 입력을 준비). 기한이 지난 원문은 참조하는 결과가 있어도 삭제된다(`run_input_maintenance`).

## 11. Provider adapter·프롬프트·출력 검증 (S6)

외부 송신은 이 단계에서도 일어나지 않는다(테스트는 HTTP 계층을 mock). 실제 작업 실행 연결은 S7이다.

**Profile** (`app/services/ai/providers/registry.py`, 사용자가 지원 목록에서 선택, 기본값은 `AI_PROVIDER_PROFILE`): 버전이 고정된 불변 값이다. 이용자가 모델·URL을 지정할 수 없고, 값을 바꾸려면 새 id를 만든다. 작업 fingerprint에는 `profile.fingerprint()`(모델·가격·한도·timeout·프롬프트·schema 전체)를 넣는다.

| 항목 | `claude-opus-5-5-v1` (sonnet 변형은 §12) |
|---|---|
| 제공자·모델 | anthropic / `claude-opus-5-5` (공식 SDK, `max_retries=0`: 호출 1번 = HTTP 1번) |
| 가격 | 입력 $4.00 / 출력 $20.00 per 1M 토큰 (`price_version` 2026-09-25, 제공자 가격표와 대조 후 새 profile로 갱신) |
| 한도 | 입력 24,000 토큰, 출력 4,000 토큰(생각 토큰 포함), 컨텍스트 1,000,000, 모델 호출 timeout 90초 |
| effort | `low` (thinking은 끌 수 없는 모델이라 effort로만 조절) |
| 프롬프트·schema | `summary-v1`/`review-v1`, `summary-output-v1`/`review-output-v1` |

- 요청에는 `tools`·`tool_choice`·`fallbacks`·beta·sampling 옵션이 없다. 모델이 도구를 실행하거나 다른 모델·제공자로 바뀔 수 없다(자동 fallback 없음). 안전 분류기가 거절하면 `finish=refused`로 끝나며 사람이 다른 profile을 명시적으로 선택해야 한다.
- 프롬프트 파일은 `PROMPT_DIGESTS`로 고정된다. 내용을 바꾸면 테스트가 실패하므로 새 버전 파일과 profile을 만든다.
- 신뢰할 수 없는 자료(PR 설명·코드)는 요청마다 새로 만든 무작위 경계 토큰 블록 안에만 들어가고 system 프롬프트에는 코드가 없다. 모든 diff 줄은 실제 줄 번호(`= L8 R8 | …`)와 함께 보낸다.

**토큰·비용** (`token_budget.py`): 미리보기(`InputPreview.token_estimate`)와 호출 전 검사는 외부로 보내지 않는 로컬 보수적 추정(`bytes_per_token`)이다. 입력 토큰이 `limit`를 넘으면(`within_limit=false`) 호출 전에 거부한다(`input_too_large`, 과금 없음). 정확한 값은 사용자 동의 이후 실행 직전 `count_tokens`(과금되지 않는 별도 호출, 실제 호출과 같은 출력 schema 포함)로 확인한다. `count_tokens`도 코드를 외부로 보내므로 동의·권한·키 확인 이후에만 호출한다. 비용은 `Decimal`이며 올림한다. 예약액 `estimated_max_cost`는 추정 입력 + 최대 출력 기준 상한, 정산액은 응답 usage 기준이다. usage가 없거나 올바르지 않으면 비용은 `null`(알 수 없음, 0이 아님)이며 호출자는 예약액으로 추정 정산한다.

**실패 분류** (`ProviderFailure`): `outcome_uncertain=false`이면 요청이 처리되지 않았음이 확실하다(예약 해제). `retryable`이면 backoff 후 자동으로 다시 호출해도 안전하다. `outcome_uncertain=true`이면 처리·과금 여부를 모르므로 예약을 `uncertain`으로 남기고 **자동 재호출하지 않는다**.

| 분류 `kind` | 상황 | retryable | outcome_uncertain | 작업 error_code |
|---|---|---|---|---|
| `rate_limited` | 429 (`retry_after`는 1~3600초) | 예 | 아니오 | `PROVIDER_RATE_LIMITED` |
| `server_error` | 500·502·503·529 중 제공자가 낸 응답(`request-id` 헤더 있음) | 예 | 아니오 | `PROVIDER_UNAVAILABLE` |
| `response_lost` | `request-id` 없는 5xx(프록시·게이트웨이: 처리됐을 수 있음) | 아니오 | **예** | `PROVIDER_TIMEOUT` |
| `connection_failed` | 연결·연결 timeout·pool timeout(서버에 닿기 전) | 예 | 아니오 | `PROVIDER_UNAVAILABLE` |
| `timeout` | 응답 대기·전송 중 timeout | 아니오 | **예** | `PROVIDER_TIMEOUT` |
| `response_lost` | 408·504, 응답 중 연결 끊김, 형식이 깨진 200 응답 | 아니오 | **예** | `PROVIDER_TIMEOUT` |
| `auth_failed` / `billing_error` | 401·403 / 402 | 아니오 | 아니오 | `PROVIDER_AUTH` |
| `model_unavailable` / `request_rejected` | 404 / 그 밖의 4xx | 아니오 | 아니오 | `PROVIDER_REJECTED` |
| `input_too_large` | 로컬 한도 초과 또는 413 | 아니오 | 아니오 | `INPUT_TOO_LARGE` |
| `unknown_error` | 분류할 수 없는 예외 | 아니오 | **예** | `PROVIDER_ERROR` |

호출이 끝까지 처리된 응답은 예외가 아니라 `ProviderResponse`이며 과금된다(비용 기록 대상). `finish`: `complete`(출력 JSON 사용 가능 여부는 `usable`), `truncated`(출력 한도), `refused`(안전 거절, `refusal_category`), `empty`, `incomplete`(예상 밖 stop reason·도구 블록). 출력은 JSON 객체 하나여야 하며 코드 펜스·앞뒤 설명·NaN·64KB 초과는 `parse_error`(`invalid_json`/`not_json_object`/`output_too_large`)이다. 출력 검증 실패도 호출이 일어났으므로 비용을 기록한다.

**출력 검증** (`validators.validate_output(kind, output, sent, revision_id=…)`): 구조(`strict`, 알 수 없는 필드·형 변환 금지)와 함께 *실제로 보낸* 자료(`SentInput`)에 대해 다음을 검사한다. 하나라도 어긋나면 결과 전체를 거부한다(부분 채택 없음).

- 근거(`evidence_ids`)는 보낸 파일의 handle(F1…)이며 중복·미지의 handle·위치한 파일과 다른 근거는 거부.
- 위치는 보낸 파일(제외된 파일 불가)의 `side`(삭제=LEFT만, 신규=RIGHT만)와 줄 범위 안이어야 하고 범위의 **모든 줄**이 실제로 보낸 번호여야 한다(hunk 사이 빈 구간을 가로지르면 거부). `quote`는 그 줄 범위의 코드에 실제로 있어야 한다(공백 차이 무시).
- 이름이 바뀐 파일의 LEFT는 옛 경로로 부를 수 있고 결과 위치는 옛 경로로 기록된다. 요약은 PR 설명이 없으면 `author_intent`를 쓸 수 없다.
- 통과한 결과의 `human_validation`은 항상 `unverified`다. **구조·참조 검증은 내용의 정확도를 보장하지 않는다**(틀린 주장도 형식이 맞으면 통과). 정확도는 사람의 피드백과 S10 평가에서 다룬다.

**미해결(S7 이후)**: 실제 API schema 수용 여부와 모델 출력 품질은 실제 키로만 확인할 수 있다(S10). `reviewable=false` 입력으로 생성 작업을 만드는 것은 S8에서 차단한다.

## 12. 사용자 API key(BYOK)

서버 공용 제공자 key는 없다. 사용자가 자기 key만 등록하고, 제공자·모델은 **지원 목록**(`GET /api/ai/v1/providers`)에서 고른다. 이 단계도 외부로 코드를 보내지 않는다(key 확인은 `models.retrieve`, 코드 미포함·무과금).

**API** (모두 로그인·AI 기능 켜짐·변경 요청은 CSRF, 응답 `Cache-Control: private, no-store`):

| 메서드·경로 | 설명 |
|---|---|
| `GET /api/ai/v1/providers` | 지원 제공자·모델(`profile_id`·가격·한도·`allowed`)과 `external_transfer_allowed` |
| `GET /api/ai/v1/credentials` | 내 key 목록: 마스킹 힌트(`sk-ant-…1111`)·버전·기본 모델·확인 시각·진행 중 작업 수. key 원문은 어떤 응답에도 없다 |
| `PUT /api/ai/v1/credentials/{provider}` | 등록·교체 `{api_key, profile_id?, verify=true}`. 같은 key 재전송은 `changed=false`. `AI_ALLOW_EXTERNAL_TRANSFER=1`이어야 한다(아니면 403 TRANSFER_NOT_ALLOWED) |
| `DELETE /api/ai/v1/credentials/{provider}` | 암호문 즉시 삭제, 이 key에 고정된 진행 중 작업 취소(`jobs_cancelled`) |

key 형식 오류·제공자 거절(401·403·402·모델 없음)은 422 `CREDENTIAL_INVALID`(`details.reason`), 제공자 제한·장애는 429/503. 확인 호출은 사용자당 시간당 `AI_CREDENTIAL_VERIFY_LIMIT_PER_HOUR`회(초과 시 429 `USER_LIMIT`). 확인에 실패하면 아무것도 저장하지 않는다.

**저장·접근**: 사용자별 AES-GCM 암호화(`AI_CREDENTIAL_ENCRYPTION_KEYS`, 입력 코드 키와 분리, AAD=`credential:{owner}:{version_id}`라 다른 사용자·버전으로 옮기면 복호화 실패). 교체하면 새 버전이 active, 옛 버전은 retired(되살릴 수 없음)이며 암호문은 **그 버전에 고정된 비종료 작업이 없을 때** 삭제된다. DB 트리거가 암호문·소유자·상태 변경을 막고, key 원문은 로그·큐·작업 옵션·audit·idempotency에 들어가지 않는다(작업에는 `credential_version_id`만 있다).

**작업 고정**: 생성 작업(`summary`/`review`)은 만들 때 `provider`·`model`·`provider_profile`·`profile_fingerprint`·`credential_version_id`·`consent_manifest_hash`를 기록하고 바꿀 수 없다(DB 트리거). 이후 key·모델을 바꿔도 진행 중 작업은 고정된 설정으로 실행된다. 고정한 profile 값이 바뀌었거나 key가 삭제·파기됐으면 실행하지 않는다(`CredentialUnavailable`). 설정이 없는 생성 작업은 422 `CREDENTIAL_REQUIRED`, key 버전이 바뀌어 있으면 409 `PREVIEW_CHANGED`. `prepare` 작업은 key가 필요 없다. 작업 fingerprint는 key 버전을 넣지 않는다. 재사용: 끝난(succeeded) 결과는 key가 달라도 재사용하지만, **진행 중 작업은 동의한 key 버전과 같은 버전에 고정된 경우에만** 재사용한다. 다르면 새 generation을 만들고 기존 작업의 고정 설정은 바꾸지 않는다.

**전송 동의** `TransferConsent{input_id, manifest_hash, provider, profile_id, credential_version, policy_version}`: 미리보기(`InputPreview.transfer`)에 보이는 제공자·모델·마스킹 key·보낼 코드 범위(manifest_hash)를 사용자가 확인한 값이다. 서버는 전송 허용·정책 버전·허용 모델·입력 소유자/준비 완료/유효기간·manifest 일치·읽을 파일 존재(`reviewable`)·모델 토큰 한도·key 존재와 버전 일치를 모두 확인하고, 하나라도 다르면 거부한다(`PREVIEW_CHANGED` 등). `reviewable=false` 입력 차단은 S8 완료 조건으로 유지한다.

**사용량**: 사용자 key를 써도 요청 한도(`AI_DAILY_JOB_LIMIT`)와 비용·토큰 기록(attempt, 예약·정산)은 그대로다. 사용자별 월 지출 상한(`AI_USER_MONTHLY_BUDGET`, 기본 20)은 항상 적용한다. 서버 전체 합계 상한(`AI_SERVER_MONTHLY_BUDGET`)은 0이면 끄고(기본) 0보다 클 때만 적용한다.

**지원 모델**: `claude-opus-5-5-v1`(입력 $4 / 출력 $20 per 1M), `claude-sonnet-5-5-v1`($2 / $10). 둘 다 입력 24,000·출력 4,000 토큰, timeout 90초, effort low. 관리자는 `AI_ALLOWED_PROFILES`로 선택지를 줄인다. 무단 fallback은 없다: 다른 모델·제공자로 자동 전환하지 않는다.

## 13. 설명·리뷰 생성 실행 (S7)

`summary`/`review` handler(`generation_service.generate_handler`, `app/tasks/ai_generate.py`가 등록)는 고정된 입력·제공자·모델·사용자 key로 실제 호출을 수행한다. summary와 review는 독립된 호출이며 review 프롬프트에 summary 결과를 넣지 않는다.

**순서**: ① 재검증 → 입력 복호화(무결성 해시 확인)·프롬프트·로컬 한도 → ② 재검증 → **`count_tokens`**(코드가 처음 외부로 나감) → 한도 확인 → ③ 재검증 → 비용 예약 → 생성 호출 → 정산 → 출력 검증 → 접근 재확인(`read`) → 결과·상태 commit.

**재검증**(코드를 외부로 보내는 호출마다, 느린 확인을 먼저 하고 마지막에 빠른 확인): 저장소 접근·전송 허용·정책(`authorize_ai(generate)`, 비공개 저장소 차단 포함) → 처리권·취소 → 입력 `ready`·유효기간 → 동의한 `manifest_hash` 일치 → 모델 허용 → 고정된 key가 삭제·파기되지 않음. 하나라도 어긋나면 보내지 않는다. 호출 전의 접근 확인 장애(`ACCESS_CHECK_UNAVAILABLE`·`UPSTREAM_RATE_LIMIT`)만 재시도한다. 예약은 key 행(`FOR SHARE`)을 먼저, 작업 행을 나중에 잠가 key 삭제와 직렬화한다.

**비용**: `count_tokens`는 과금되지 않으므로 예약·불명 처리가 없다. 예약액은 `count_tokens` 입력 + 최대 출력 기준 상한이다. 호출이 처리됐다면 결과가 쓸모없어도(검증 실패·잘림·거절·취소) 응답의 usage로 정산하고, usage가 없으면 예약액으로 정산한다. 처리되지 않았음이 확실한 실패(429·`request-id`가 있는 5xx·연결 전 실패·거절)는 예약을 해제한다. 결과를 모르면(`outcome_uncertain`) 예약을 `uncertain`으로 남겨 예산을 점유하고 작업은 `OUTCOME_UNCERTAIN`으로 실패하며 자동으로 다시 호출하지 않는다. 호출 시간 동안 DB transaction은 열려 있지 않다.

**복구**: 처리권이 만료된 작업은 현재 attempt의 호출 기록(미정산·불명·정산 완료 예약)으로 호출 여부를 판단한다. 호출 전이면 재시도하고, 결과를 모르면 `OUTCOME_UNCERTAIN`, 정산까지 끝났다면 `RESULT_LOST`로 실패시키며 어느 쪽도 자동으로 다시 호출하지 않는다. `fail_job`도 이미 처리된 호출이 있는 attempt는 재시도 대기로 돌리지 않는다.

**결과**(`result_store.save_validated_result`): `finish_job`의 `work`로 같은 transaction에서 `ai_results`·`ai_findings`를 만들어 결과와 상태가 함께 커밋된다. 취소가 접수됐거나 처리권·접근 권한을 잃었다면 결과를 만들지 않는다(호출 비용은 기록). `coverage`는 입력의 값을 유지하고 `analysis_coverage`만 실제로 모델에 보낸 범위로 채운다(선택한 파일을 모두 보내고 선택 코드를 모두 확보했으면 `complete`, 일부면 `partial`, 없으면 `none`). `human_validation`은 `unverified`, 보관 기간은 `AI_RESULT_RETENTION_DAYS`(기본 30일). 결과 저장이 실패해도 이미 비용이 든 호출을 자동으로 되풀이하지 않는다(`RESULT_STORAGE_FAILED`, 사용자가 명시적으로 다시 시작).

| 작업 `error_code` | 의미 |
|---|---|
| `REAUTH_REQUIRED`·`ACCESS_REVOKED`·`TRANSFER_NOT_ALLOWED` | 접근 권한·전송 허용·정책을 잃었다 |
| `INPUT_EXPIRED`·`PREVIEW_CHANGED`·`INPUT_INTEGRITY`·`NO_REVIEWABLE_CONTENT` | 입력이 만료됐거나 동의한 범위와 다르거나 손상됐거나 보낼 파일이 없다 |
| `PROFILE_NOT_ALLOWED`·`PROFILE_CHANGED`·`CREDENTIAL_UNAVAILABLE` | 모델이 허용되지 않거나 고정한 profile이 바뀌었거나 key가 삭제·파기됐다 |
| `INPUT_TOO_LARGE`·`BUDGET_LIMIT` | 모델 입력 한도·사용 예산 초과(호출 전) |
| `PROVIDER_RATE_LIMITED`·`PROVIDER_UNAVAILABLE`(재시도), `PROVIDER_AUTH`·`PROVIDER_REJECTED`·`PROVIDER_ERROR` | 제공자 오류(S6 분류) |
| `PROVIDER_TIMEOUT`·`OUTCOME_UNCERTAIN` | 호출 결과를 알 수 없음(자동 재호출 없음) |
| `OUTPUT_TRUNCATED`·`PROVIDER_REFUSED`·`OUTPUT_INVALID`·`OUTPUT_VALIDATION_FAILED` | 출력이 잘렸거나 거절됐거나 쓸 수 없거나 검증에 실패(부분 채택 없음, 호출 비용은 기록) |

**미해결**: 실제 API와의 연동은 mock(`httpx2.MockTransport`)으로만 검증했다. `reviewable=false` 입력 차단은 S8의 API에서도 유지한다(동의 확인에서 이미 막고, 실행 시에도 `NO_REVIEWABLE_CONTENT`로 막는다).

## 14. 생성·결과·피드백 API (S8)

새 실행 로직은 없다. 접수·조회·상태 전이는 S4~S7의 서비스(`consent.resolve_consent`, `job_service`, `result_store`)를 그대로 쓴다. 모든 경로는 로그인·AI 기능 켜짐·(변경 요청은) CSRF를 요구하고 응답은 `Cache-Control: private, no-store`다. GET은 작업·코드 취득·외부 호출을 시작하지 않는다.

| 메서드·경로 | 설명 |
|---|---|
| `POST /jobs` | `{kind: summary\|review, consent: TransferConsent, force_regenerate}` + `Idempotency-Key`. 새로 접수하면 **202**, 기존 작업(같은 key 재전송, 같은 fingerprint의 진행 중 작업, 이미 성공한 결과)을 돌려주면 **200** |
| `GET /jobs?repository_id=` | 내 작업 목록(필수 `repository_id`, 선택 `kind`·`status`·`input_id`·`limit`≤50·`cursor`). 최신순 keyset `(created_at, id)`라 같은 시각이어도 중복·누락이 없다 |
| `GET /jobs/{id}` | `JobView`: 상태·`stage`(실행 중일 때만)·coverage·`result_id`·`error_code`·`outcome_uncertain`·`retry_after_seconds` |
| `POST /jobs/{id}/cancel` | 대기 중이면 즉시 `cancelled`(200), 실행 중이면 `cancel_requested`(202), 종료된 작업은 변화 없음. 저장소 권한과 무관(본인 작업) |
| `POST /jobs/{id}/retry` | 실패·취소된 생성 작업을 새 generation으로 다시 시작. `{consent}`와 `Idempotency-Key` 필요 |
| `DELETE /jobs/{id}` | 끝난 작업과 결과·지적·피드백 삭제(진행 중이면 422, 비용 기록은 남음) |
| `GET /results/{id}` | 불변 결과(`payload`·`payload_hash`·`coverage`·`human_validation=unverified`) |
| `GET /results/{id}/findings` | 지적 목록(ordinal keyset, `limit`·`cursor`). 내 피드백이 `finding.human_validation`·`feedback`에 덧입혀진다 |
| `PUT /findings/{id}/feedback` | `{verdict, note?, expected_version}`. 처음이면 0, 아니면 마지막으로 본 version |
| `GET /usage` | 이번 달 개인 지출·예약·남은 한도, 불명 예약 수, 일일 요청 사용량 |

**접수 검증**(`POST /jobs`, `/retry`): 입력 소유자(남의 입력은 404) → 같은 key의 재전송이면 처음 작업 반환(그 사이 key가 바뀌었어도) → 현재 저장소 접근·전송 허용(`generate`, 비공개 저장소 차단) → `resolve_consent`: 입력 `ready`·만료(410 `INPUT_EXPIRED`; 삭제된 입력 포함)·동의한 `manifest_hash`·정책 버전·허용 모델·**읽을 파일 존재(`reviewable=false`이면 422 `INVALID_STATE`, `details.reason=NO_REVIEWABLE_CONTENT`)**·모델 토큰 한도·key 존재와 버전 → 일일 한도(429 `USER_LIMIT`). 같은 key·다른 요청은 409 `IDEMPOTENCY_CONFLICT`, Idempotency-Key가 없으면 422 `INVALID_STATE`.

**조회 권한**: 본인 자료만(남의 것은 404로 존재를 숨김)이고 저장소 접근 권한은 매 요청 다시 확인한다(잃으면 404). 삭제된 작업·결과는 404, 보관 기간이 지난 결과는 410 `RESULT_EXPIRED`.

**재시도 규칙**: 자동 재시도는 worker가 호출 전 실패에 대해서만 한다. `OUTCOME_UNCERTAIN`·`RESULT_LOST` 등 이미 처리됐을 수 있는 호출은 사용자가 `/retry`(또는 `force_regenerate`)로 명시적으로 요청해야 새 generation이 만들어지고, 그때 동의·key·예산을 다시 확인한다. 불명 호출의 예약은 정리될 때까지 예산을 점유한다(`GET /usage`의 `uncertain_reservations`).

**삭제와 Idempotency-Key**: 작업을 삭제해도 그 작업에 묶인 key는 TTL(24시간) 동안 유지된다. 같은 key·같은 요청을 다시 보내면 새 작업(재과금)을 만들지 않고 404 `RESOURCE_NOT_FOUND`(`details.reason=JOB_DELETED`)로 끝난다. 다시 요청하려면 새 key를 쓴다. 공통 `create_job` 경로(입력 준비 포함)에도 같은 규칙이 적용된다.

**피드백**: 결과 작성자만 쓸 수 있고(남의 지적은 404) `expected_version`이 다르면 409 `VERSION_CONFLICT`(`details.current_version`). 지적 행을 잠근 채 존재·작성자·만료를 다시 확인하므로(그 사이 결과가 삭제됐다면 404) 동시 요청 중 하나만 성공한다. 원본 결과·지적은 바뀌지 않으며(DB trigger) 피드백은 별도 행이다. 감사 기록에는 메모 본문이 없다.

**미해결**: `reviewable=false` 차단은 접수 경로(동의 확인)와 실행 경로(`NO_REVIEWABLE_CONTENT`) 모두에서 유지한다. 프런트 연결은 S9다.

## 15. PR·파일 선택 조회와 기본 모델 변경 (S9)

AI 제공자를 호출하지 않고 DB에 아무것도 저장하지 않는다. 로그인·AI 기능 켜짐을 요구하고 응답은 `Cache-Control: private, no-store`다. 저장소 접근은 `prepare` 권한(외부 AI 전송이 아님)으로 매번 확인한다.

| 메서드·경로 | 설명 |
|---|---|
| `GET /pulls?repository_id=&state=open\|closed\|merged&limit≤50&cursor=` | 현재 GitHub PR 목록(`PullList`). cursor는 서명된 값이며 저장소·state·limit에 묶인다 |
| `GET /pulls/{n}/files?repository_id=&limit≤100&cursor=` | PR 전체 파일 목록 한 페이지(`PullFilesView`): 현재 `base_sha`·`head_sha`, `pr_total_files`, `inventory_coverage`(complete/partial/unknown). **고정 입력이 아니다** |
| `PATCH /credentials/{provider}` | `{profile_id}`: key·버전은 그대로 두고 기본 모델만 바꾼다(허용되지 않은 모델은 422). 전송 동의는 별도로 모델·key 버전을 확인한다 |

GitHub 오류는 `ErrorEnvelope`로 매핑한다(404 `RESOURCE_NOT_FOUND`, 429 `UPSTREAM_RATE_LIMIT`+`Retry-After`, 503 `UPSTREAM_UNAVAILABLE`). 화면은 목록에서 본 SHA를 `POST /inputs`의 `expected_base_sha`·`expected_head_sha`로 보내며, 서버가 다시 확인해 다르면 409 `PR_CHANGED`다.

## 16. GitViz 이력 근거 (S11)

코드 지적과 **별개**인 불변 근거다. 분석 snapshot(`analysis_snapshots.payload`)에 이미 저장된 값만 옮기며 재계산·모델 호출·외부 전송을 하지 않는다(이력 값은 모델로 보내지 않는다). 결과 저장은 이력 근거 실패와 무관하게 성공한다(savepoint 격리).

- `GET /api/ai/v1/history/snapshots?repository_id=` — 고를 수 있는 snapshot과 `eligible`, `reasons`, `reason_texts`.
- `GET /api/ai/v1/results/{id}/history` — 결과에 붙은 근거 목록. `POST` 같은 경로 `{snapshot_id, as_of?}` — 만들거나(201) 이미 있으면 그대로(200) 돌려준다. 리뷰 결과만 가능하다. 작성자만, 저장소 접근 권한을 매번 확인한다.
- 작업 생성의 `history_snapshot_id`(선택, 리뷰 전용): 접수 때 같은 저장소의 snapshot인지만 확인하고 결과 저장 시 근거를 붙인다.
- 같은 리뷰를 재사용할 때(지문 일치): 끝난 작업이면 이번에 지정한 snapshot의 근거를 그 결과에 붙이고(이미 있으면 그대로) 200을 돌려준다. 진행 중인 작업은 고정 설정을 바꾸지 않으므로, 다른 snapshot을 지정하면 조용히 무시하지 않고 409 `VERSION_CONFLICT`(`details.reason=HISTORY_SNAPSHOT_DIFFERS`)로 알린다(완료 후 `POST /results/{id}/history`).
- 재사용 요청의 멱등 key는 첨부보다 먼저 확정된다. 첨부가 실패한 뒤 같은 key와 본문으로 재전송하면 미완료 첨부를 이어서 만든다(이미 있으면 그대로).
- 동시 요청이 먼저 근거를 저장해도(UNIQUE 충돌), 나중 요청은 자기 `as_of`의 판정과 다시 비교한다.
- 근거는 (결과, snapshot)당 하나다. 이미 있는 근거에 다른 `as_of`를 주면, 그 시점의 판정(상태·이유)이 기존과 같을 때만 기존 근거를 돌려주고 다르면 409 `VERSION_CONFLICT`(`details.reason=HISTORY_AS_OF_CONFLICT`)다.
- 다른 저장소의 snapshot은 존재를 숨긴다(404). DB도 (snapshot, 저장소) 복합 FK로 막는다. 근거 행은 수정·삭제 불가이며 결과 삭제와 함께 사라진다.
- **검증하지 못한 snapshot은 근거에서 제외한다**: `status=excluded`와 이유 코드만 기록하고 값은 싣지 않는다. 이유 — `integrity_failed`, `legacy_format`, `formula_unsupported`, `target_sha_missing`, `repository_identity_missing`, `repository_identity_mismatch`, `payload_incomplete`, `future_data`(리뷰 결과 시각 또는 `as_of` 이후의 자료).
- `evidence`: `basis`(snapshot·SHA·PR base/head 비교 `relation`), `paths`(파일별 관측 횟수·소유 분포·co-change 상위·한계), `data_quality`(미식별 분모·수집 품질), `notes`. co-change 파일은 `changed_in_pr`만 알려 주는 **검토 후보**이며 누락·오류로 단정하지 않는다. 신규·이름 변경·snapshot에 없는 파일은 각각 한계 문구와 함께 표시한다.
- 문구는 `prompts/history_v1.txt`(버전 `history-v1`, 다이제스트 고정)의 고정 템플릿이며 모델 프롬프트가 아니다.
- 운영 snapshot 검증은 출시 전 필수 확인으로 남는다. 인증 사용자로 접근하는 실제 GitViz 경로의 확인은 별도다.

## 17. 인수인계 (S12)

계획(§15)과 다른 점: **초안은 모델을 호출하지 않는다.** `POST /jobs kind=handover`와 `prompts/handover_v1.txt`를 모델 프롬프트로 만들지 않고, 서버가 저장된 자료(대상·준비 정보·AI 결과·피드백·분석 snapshot)를 고정 문구(`handover_v1.txt`, 버전 `handover-v1`, 다이제스트 고정)로 옮겨 적는다. 그래서 코드 전송·동의·비용이 없고, 미검증 AI 지적과 미확인 항목이 문장 생성 과정에서 확정 사실로 바뀔 수 없다. 모델이 쓰는 서술은 이후 단계에서 같은 저장 구조(원본/편집본 분리) 위에 추가할 수 있다.

모든 경로는 `/api/ai/v1` 아래이고 작성자 본인의 자료만 다룬다(남의 것은 404). 저장소 접근 권한을 매번 다시 확인하며 응답은 `Cache-Control: private, no-store`다. 감사 기록(`handover.*`)에는 본문·메모를 넣지 않는다.

| 경로 | 설명 |
|---|---|
| `POST/GET /handover-targets`, `GET/PATCH/DELETE /handover-targets/{id}` | 인계 대상(이름, 파일 1~50개, 선택 snapshot, 목적·인계자·인수자 메모). `PATCH`는 `expected_version`이 필요하다. 이름은 메모일 뿐 역할·권한을 주지 않는다. |
| `GET/PUT /handover-targets/{id}/preparation`, `GET .../preparation/versions` | 준비 체크폼. 항목 6개(`assignee`, `description`, `run_method`, `verify_method`, `joint_review`, `receiver_run`) 상태 `unknown`/`needed`/`ready`. |
| `POST/GET /handover-targets/{id}/generations`, `GET /handover-generations/{id}` | 초안(생성 원본) 만들기·조회. |
| `GET /handover-targets/{id}/document`, `PUT /handover-documents/{id}`, `POST /handover-documents/{id}/adopt`, `GET /handover-documents/{id}/export?format=markdown` | 편집 문서 조회·저장·새 원본 채택·내보내기. |

**준비 체크폼**
- 미입력·누락·`null` 항목은 항상 `unknown`(미확인)이다. `needed`로 바꾸지 않는다. 요청은 전체 교체이며 보내지 않은 항목은 `unknown`이다. 매 저장은 새 버전을 추가하고(수정 불가), 항목마다 바뀐 버전·시각(`updated_version`, `updated_at`)을 남긴다.
- `unknown` 항목에는 내용·링크를 둘 수 없다(422). `ready`는 완료 조건이 있어야 한다: `assignee`·`joint_review`·`receiver_run`은 내용, `description`·`run_method`·`verify_method`는 내용 또는 링크. `ready`의 표시 문구는 항상 "준비 완료 (작성자 기록)"이며 시스템이 내용이나 실행 성공을 검증했다는 뜻이 아니다.
- 링크는 `http(s)`만 저장·표시하고 서버가 열거나 읽지 않는다.
- `expected_version`(처음이면 0)이 다르면 409 `VERSION_CONFLICT`(`details.reason=PREPARATION_VERSION`, `current_version`). `confirmed_target_version`이 현재 대상 버전과 다르면 409(`TARGET_CHANGED`).
- 대상의 파일 또는 snapshot이 바뀌면 `scope_version`이 올라가고, 그 전에 저장한 준비 정보는 보존되지만 `needs_reconfirm=true`가 된다(새 대상에 대한 확인으로 취급하지 않는다). 현재 대상 버전을 확인해 다시 저장하면 해제된다. 이름·메모만 바꾸면 재확인이 필요 없다.

**초안과 편집본**
- 생성 요청은 `expected_target_version`, `expected_preparation_version`과 선택 `summary_result_id`/`review_result_id`를 받는다. 결과는 같은 작성자·같은 저장소·만료 전·종류가 맞아야 한다. 다르면 409(`TARGET_CHANGED`/`PREPARATION_CHANGED`).
- 생성 원본(`sources`)이 고정하는 기준: 대상·준비 정보 버전, 사용한 결과의 `payload_hash`와 입력·PR 기준(번호·base/head SHA·manifest hash), 리뷰 결과는 이 사용자의 피드백 기준(`feedback_digest`), snapshot은 id·상태·제외 사유. 같은 기준이면 기존 원본을 그대로 돌려주고(200, `reused`), `force=true`일 때만 새 원본(번호 증가)을 만든다. 원본은 수정할 수 없다.
- 이 기준이 나중에 달라지면 원본·문서는 그대로 두고 `stale_reasons`(`target_changed`, `preparation_changed`, `feedback_changed`, `source_missing`)로 알린다.
- 편집 문서(`handover_documents`)는 원본과 분리돼 `expected_version`으로만 바뀐다(409 `DOCUMENT_VERSION`). 새 원본이 생겨도 **편집한 문서는 덮어쓰지 않는다**(`newer_generation_*`로 알리고 `POST /adopt`로 사용자가 명시할 때만 바꾸며 이전 내용은 편집 이력에 남는다). 편집하지 않은 문서는 새 원본을 따라간다. 편집 이력은 문서마다 최근 100개를 둔다.
- 대상 파일 밖의 AI 지적·변경 설명은 초안에 넣지 않고 제외한 건수를 적는다. 코드 원문·인용은 넣지 않는다.

**확정 사실로 바꾸지 않는 규칙(초안 문구)**
- 미검증 AI 지적은 "사람이 아직 검증하지 않은 지적"에, `valid`는 "사용자가 유효로 표시한 지적(사용자의 판단)"에, `false_positive`는 참고용으로만 표시한다. 미검증 지적은 질문과 체크리스트로만 나가고 결론이 되지 않는다.
- 선택하지 않은 자료는 "확인되지 않음"으로 적는다("문제 없음"류 문구를 쓰지 않는다). 이력 수치는 검증을 통과한 snapshot의 저장값만 옮기며, 통과하지 못한 snapshot은 사유만 적고 수치를 싣지 않는다. 공동 변경은 검토 후보일 뿐 의존 관계가 아니다.

**Markdown 안전**
- 사용자·AI가 쓴 모든 문장은 이스케이프해 문서 구조(제목·링크·HTML·이미지)로 해석되지 않게 넣고, 링크·경로는 코드 span으로만 쓴다. 제어·방향 제어 문자는 입력 단계에서 거절한다.
- 문서 저장과 내보내기 직전에 `validate_markdown`을 적용한다(코드 블록·코드 span·이스케이프를 렌더러와 같은 순서로 제외하고 검사): 원시 HTML, 이미지, `http`/`https`/`mailto`·같은 문서·상대 경로가 아닌 링크(문자 참조·`//` 포함), 제어·보이지 않는 문자, 4,000자 넘는 줄, 100,000자 넘는 문서를 거절한다(422 `INVALID_SELECTION`, `details.issues`). 내보내기에서 걸리면 422 `INVALID_STATE`다.
- 내보내기는 `text/markdown; charset=utf-8`, `Content-Disposition: attachment`(파일 이름은 문서 id·버전으로만 만든다), `X-Content-Type-Options: nosniff`, `no-store`이며, 본문 뒤에 기준 정보와 "AI가 만든 내용과 미확인 항목은 확정 사실이 아니다" 주의 문구를 서버가 덧붙인다.

한도: 대상 100개/사용자, 대상당 초안 50개·준비 정보 500버전, 대상 파일 50개.
