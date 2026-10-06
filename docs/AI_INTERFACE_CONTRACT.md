# AI 인터페이스 계약 (S0)

- 계약 버전: `1.1` (`app/schemas/ai.py`의 `CONTRACT_VERSION`)
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
| `OUTCOME_UNCERTAIN` | AI 호출 중 처리자가 사라져 결과를 알 수 없음. 새 호출로 이어가지 않으며 사용자가 명시적으로 재시도(새 generation)해야 한다 |

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
| 422 | INVALID_SELECTION, INVALID_STATE |
| 429 | USER_LIMIT, BUDGET_LIMIT, UPSTREAM_RATE_LIMIT |
| 503 | ACCESS_CHECK_UNAVAILABLE, UPSTREAM_UNAVAILABLE |

설계 v0.4 표의 `502/503` 중 이번 계약은 503만 사용합니다. 502가 필요하면 다음 minor 버전에서 추가합니다.

## 8. 버전 규칙

- 필드·enum 값 **추가**는 minor 증가, 의미 변경·삭제·이름 변경은 major 증가이며 소비자 합의가 필요합니다.
- 1.1(S5): `InputManifest.pr_metadata`(AI로 보내는 PR 제목·설명) 추가. 기존 필드는 그대로이며 manifest_hash에 PR 설명이 반영된다.
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

**Profile** (`app/services/ai/providers/registry.py`, `AI_PROVIDER_PROFILE`로 선택): 버전이 고정된 불변 값이다. 이용자가 모델·URL을 지정할 수 없고, 값을 바꾸려면 새 id를 만든다. 작업 fingerprint에는 `profile.fingerprint()`(모델·가격·한도·timeout·프롬프트·schema 전체)를 넣는다.

| 항목 | `claude-review-v1` |
|---|---|
| 제공자·모델 | anthropic / `claude-opus-5-5` (공식 SDK, `max_retries=0`: 호출 1번 = HTTP 1번) |
| 가격 | 입력 $4.00 / 출력 $20.00 per 1M 토큰 (`price_version` 2026-09-25, 제공자 가격표와 대조 후 새 profile로 갱신) |
| 한도 | 입력 24,000 토큰, 출력 4,000 토큰(생각 토큰 포함), 컨텍스트 1,000,000, 모델 호출 timeout 90초 |
| effort | `low` (thinking은 끌 수 없는 모델이라 effort로만 조절) |
| 프롬프트·schema | `summary-v1`/`review-v1`, `summary-output-v1`/`review-output-v1` |

- 요청에는 `tools`·`tool_choice`·`fallbacks`·beta·sampling 옵션이 없다. 모델이 도구를 실행하거나 다른 모델·제공자로 바뀔 수 없다(자동 fallback 없음). 안전 분류기가 거절하면 `finish=refused`로 끝나며 사람이 다른 profile을 명시적으로 선택해야 한다.
- 프롬프트 파일은 `PROMPT_DIGESTS`로 고정된다. 내용을 바꾸면 테스트가 실패하므로 새 버전 파일과 profile을 만든다.
- 신뢰할 수 없는 자료(PR 설명·코드)는 요청마다 새로 만든 무작위 경계 토큰 블록 안에만 들어가고 system 프롬프트에는 코드가 없다. 모든 diff 줄은 실제 줄 번호(`= L8 R8 | …`)와 함께 보낸다.

**토큰·비용** (`token_budget.py`): 미리보기(`InputPreview.token_estimate`)와 호출 전 검사는 외부로 보내지 않는 로컬 보수적 추정(`bytes_per_token`)이다. 입력 토큰이 `limit`를 넘으면(`within_limit=false`) 호출 전에 거부한다(`input_too_large`, 과금 없음). 정확한 값은 사용자 동의 이후 실행 직전 `count_tokens`(과금되지 않는 별도 호출)로 확인한다. 비용은 `Decimal`이며 올림한다. 예약액 `estimated_max_cost`는 추정 입력 + 최대 출력 기준 상한, 정산액은 응답 usage 기준이다. usage가 없거나 올바르지 않으면 비용은 `null`(알 수 없음, 0이 아님)이며 호출자는 예약액으로 추정 정산한다.

**실패 분류** (`ProviderFailure`): `outcome_uncertain=false`이면 요청이 처리되지 않았음이 확실하다(예약 해제). `retryable`이면 backoff 후 자동으로 다시 호출해도 안전하다. `outcome_uncertain=true`이면 처리·과금 여부를 모르므로 예약을 `uncertain`으로 남기고 **자동 재호출하지 않는다**.

| 분류 `kind` | 상황 | retryable | outcome_uncertain | 작업 error_code |
|---|---|---|---|---|
| `rate_limited` | 429 (`retry_after`는 1~3600초) | 예 | 아니오 | `PROVIDER_RATE_LIMITED` |
| `server_error` | 500·502·503·529 | 예 | 아니오 | `PROVIDER_UNAVAILABLE` |
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
