# 현재 AI HTTP 계약 안내

검토일: 2026-10-23 · 코드 기준: `a8351f0` · DTO 계약 값: `1.9` · [문서 안내](../index.md)

확인: [DTO](../../app/schemas/ai.py)와 실제 router·서비스를 대조한 현재 endpoint 안내다. [과거 상세 계약](../archive/originals/docs/AI_INTERFACE_CONTRACT.md)은 변경 계보를 확인하는 자료다. [D01~D04](../decisions/index.md)의 정책 충돌은 문서 정리로 승인·해소된 것으로 처리하지 않는다.

모든 아래 경로는 `/api/ai/v1`을 앞에 붙인다. 로그인·AI 기능 활성화가 필요하고 변경 요청에는 CSRF·Origin 검사가 적용된다. 응답은 private/no-store를 사용한다. 생성 요청과 입력 준비·retry는 Idempotency-Key를 요구한다.

## endpoint와 접근 범위

| 경로 | 동작·범위 | 소스 |
|---|---|---|
| `GET /providers` | 지원 profile·한도·허용 여부 | [credentials router](../../app/api/ai/credentials.py) |
| `GET /credentials`, `PUT/PATCH/DELETE /credentials/{provider}` | 본인 클라우드 key 관리. 로컬은 key 없음 | [credentials](../../app/api/ai/credentials.py) |
| `GET /pulls`, `GET /pulls/{n}/files` | 저장소·상태·cursor의 현재 PR 목록·파일 | [pulls](../../app/api/ai/pulls.py) |
| `GET /commits`, `GET /commits/{sha}/files` | 기본 브랜치 목록·40자리 SHA 변경 파일 | [commits](../../app/api/ai/commits.py) |
| `POST /inputs`, `GET/DELETE /inputs/{id}` | 본인 입력 준비·조회·원문 삭제 | [inputs](../../app/api/ai/inputs.py) |
| `GET /inputs/{id}/files/{file_id}` | 본인 고정 코드. side·줄 범위 | [inputs](../../app/api/ai/inputs.py) |
| `POST/GET /jobs`, `GET /jobs/{id}` | 본인 생성·작업 조회 | [jobs](../../app/api/ai/jobs.py) |
| `POST /jobs/{id}/cancel`, `/retry` | 본인 취소·새 generation 재실행 | [jobs](../../app/api/ai/jobs.py) |
| `DELETE /jobs/{id}` | 종료된 본인 작업·결과 삭제. 현재 구현은 200 | [jobs](../../app/api/ai/jobs.py) |
| `GET /results/{id}`, `/results/{id}/findings` | 본인 결과·피드백 overlay | [results](../../app/api/ai/results.py) |
| `PUT /findings/{id}/feedback` | 결과 owner만 판정·메모 저장, expected_version 충돌 검사 | [feedback](../../app/api/ai/feedback.py) |
| `GET /usage` | 본인 비용·예약·횟수 | [usage](../../app/api/ai/usage.py) |
| `GET /history/snapshots`, `GET/POST /results/{id}/history` | 본인 리뷰에 같은 저장소의 고정 이력 근거 연결 | [history](../../app/api/ai/history.py) |
| `GET /shared/results`, `/shared/results/{id}`, `/shared/results/{id}/findings` | 다른 owner의 결과·판단·메모를 현재 저장소 접근 권한으로 읽음. 별도 opt-in 검사 없음 | [shared](../../app/api/ai/shared.py) |

개인 API의 owner 제한과 공유 GET 예외를 구분한다. 공유 GET은 입력 원문 조회 권한을 주지 않지만 결과 payload·인용·판단·메모를 반환한다. 공유 endpoint에는 쓰기 경로가 없고, 다른 사용자가 기존 feedback PUT으로 남의 결과에 판정을 쓰는 것도 허용되지 않는다. 공개 저장소의 GitHub read는 조직 구성원 검증과 다르다. 구체 정책은 [D01](../decisions/index.md#d01)에서 미확정으로 관리한다.

## 입력·실행·결과

- 입력은 `pr_number`와 `commit_sha` 중 정확히 하나를 사용한다. PR은 merge-base와 head, 커밋은 첫 부모와 head, 최초 커밋은 빈 트리를 비교한다.
- `inventory_coverage`, `selected_content_coverage`, `analysis_coverage`는 분모가 다르다. 입력 준비에서 analysis none은 정상이다.
- `changed_files`는 전체 변경 목록이며 AI에 전송한 선택 코드와 다르다. S11은 전체 목록 또는 구형 입력의 선택 범위 한계를 명시한다.
- summary와 review는 독립 호출이다. AI 요약을 review의 코드 사실로 사용하지 않는다.
- 신규 생성은 202, 기존 작업 재사용은 200이다. 같은 key·다른 본문은 409다. 삭제된 작업의 key는 TTL 동안 새 과금을 만들지 않도록 유지한다.
- 실행 상태는 queued/running/retry_wait/cancel_requested/succeeded/failed/cancelled다. 성공 prepare에는 결과 ID가 없고 성공 생성에는 결과 ID가 있다.
- 취소·본인 자료 삭제는 owner·CSRF를 요구하되 현재 저장소 접근과 무관한 정리 경로다. 진행 중 job 삭제는 거부되므로 먼저 취소 상태를 확인한다. 입력 삭제는 202, 종료 job 삭제는 200이며 비용 기록은 남는다. 모든 작업을 동일한 read 권한 규칙으로 단순화하지 않는다.
- 전송 확인은 manifest·정책·profile·key 버전을 고정한다. 로컬은 key 버전 null과 profile fingerprint를 사용한다.
- cloud generate는 외부 전송 허용과 공개 저장소를 요구한다. local_generate는 외부 전송 설정과 별개이며 현재 저장소 접근 검사는 유지한다.

## 식별자·동의·검증

AI 리소스 ID는 UUID, repository ID는 양의 정수, 코드 위치는 `path + side(LEFT/RIGHT) + start_line + end_line + revision_id`다. SHA·경로·coverage·요청의 추가 필드 거부 등 정확한 제약은 [DTO](../../app/schemas/ai.py)에 정의한다. 커밋 파일 endpoint는 소문자 전체 40자리 SHA를 받으므로 일반 DTO의 SHA 지원 범위와 혼동하지 않는다.

`POST /jobs`는 `{kind, consent, force_regenerate, history_snapshot_id?}`를 사용한다. consent는 `input_id`, `manifest_hash`, `provider`, `profile_id`, `credential_version`, `policy_version`, 선택 `profile_fingerprint`를 고정한다. 로컬은 null credential_version과 profile_fingerprint가 필요하다. 정확한 필수·선택 필드는 DTO를 따른다. 코드 준비만으로 AI 호출 동의를 얻었다고 간주하지 않는다.

호출 전에 owner·현재 GitHub 접근·전송/로컬 정책·입력 기한·hash·profile·key·취소·lease·예산을 재검사한다. provider 호출 중 DB transaction을 유지하지 않고 결과와 job 완료를 함께 확정한다. [생성 서비스](../../app/services/ai/generation_service.py)와 [동의 서비스](../../app/services/ai/consent.py)가 실행 근거다.

출력은 실제 보낸 파일·side·모든 줄·인용·근거 handle에 대해 검증한다. 결과 일부를 임의 채택하지 않고 구조·위치 실패를 실패로 처리한다. 사람 판정은 별도 feedback이며 결과 원본은 unverified로 시작한다. scope·위치 검증은 의미 정확도 보장이 아니다.

## 오류·기한·재사용

오류는 `{"error":{"code":"…","message":"…","retryable":false,"request_id":"…","details":{}}}`이며 code→HTTP mapping은 `ERROR_HTTP_STATUS`가 정의한다. 대표적으로 로그인 401, 전송/CSRF 403, 권한 밖/삭제 404, 입력·버전·멱등 충돌 409, 만료 410, 크기 413, 형식·상태 422, 한도 429, 외부 접근 확인 장애 503이다. 다른 작성자의 개인 리소스 존재·원문 오류·비밀을 노출하지 않는다.

멱등 key와 본문 hash 재전송, fingerprint 재사용, 실패·취소 후 새 generation은 다른 규칙이다. 현재 key가 바뀌어도 동일 key의 최초 접수 복구와 진행 중 작업의 고정 자격 증명은 구분한다. 결과 불명 호출은 자동 재호출하지 않고 예약을 유지한다. [운영 안내](../operations/ai.md)를 따른다.

기본 신규 실행 유효기간은 24시간, 원문 7일, 결과·피드백 30일이다. 설정으로 바뀔 수 있으며 원문 만료와 결과 만료는 다르다. idempotency key는 삭제된 작업에서도 TTL 동안 중복 접수를 막는다. 한도는 [profile별 안내](../operations/providers.md)를 따른다.

S11은 같은 저장소의 유효한 불변 snapshot만 사용한다. 구형·해시 손상·SHA/식별 누락·미래 자료·미지원 계산은 제외 이유로 남긴다. 같은 결과/snapshot의 다른 as_of 판정이 달라지면 충돌로 알리고 원본 근거를 다시 쓰지 않는다. [이력 서비스](../../app/services/ai/history_context.py)와 [API](../../app/api/ai/history.py)가 상세 판정 근거다.

## 버전 변경

현재 `CONTRACT_VERSION=1.9`는 관찰값이다. 과거의 major/minor 규칙과 폐기 이력은 [D02](../decisions/index.md#d02)에서 미확정으로 관리한다. 정책 확정 전 변경은 DTO·영향받는 router·소비자·테스트·이 문서와 결정 기록을 함께 검토하며 임의 호환성 보장을 추가하지 않는다.

추정: 상세 계약을 재작성할 때 endpoint별 권한·예외·반환 형식으로 나누는 것이 적절하다.

미확정: DTO 1.9의 폐기·호환성 정책은 [D02](../decisions/index.md#d02), 구현된 공유 정책의 승인 여부는 D01에 남아 있다. 이 문서는 코드의 현재 동작을 기록하며 충돌의 정답을 정하지 않는다.
