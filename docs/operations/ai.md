# 현재 AI 운영 안내

검토일: 2026-11-04 · 코드 기준: `feat/ai-review-byok` · [설치](setup.md) · [제공자](providers.md) · 결정: [AD-33](../architecture/ARCHITECTURE_DECISIONS.md)

확인: [기존 운영 원본](../archive/originals/docs/AI_OPERATIONS.md)은 Celery worker·dispatcher·서버 key 저장소가 있던 시절의 절차다. AD-33 이후에는 아래 절차만 쓴다.

## 구조 요약 (AD-33)

- AI 리뷰와 AI 분석은 **앱 전용 BYOK**다. 사용자의 API key는 앱이 Windows DPAPI로 보관하고 실행 요청(작업 생성·재시도)마다 `X-GitViz-AI-Key` 헤더로 보낸다. 서버는 key를 DB·로그·큐에 저장하지 않고 그 실행의 메모리에서만 쓴다.
- 실행은 Celery가 아니라 **요청을 받은 API 프로세스의 스레드**(프로세스당 동시 2개)에서 한다. 재시도도 같은 스레드가 같은 key로 한다. 별도 AI worker·dispatcher·Redis 큐가 없다.
- 서버는 **코드 원문을 저장하지 않는다.** 입력 준비 때 파일별 해시만 확정하고, 파일 보기·생성 때 고정 SHA에서 다시 가져와 해시를 대조한다. 다르면 `PREVIEW_CHANGED`로 막고 `input.integrity_failure` 감사 기록을 남긴다.
- 서버 AI 스위치(`AI_ENABLED`)·외부 전송 허용(`AI_ALLOW_EXTERNAL_TRANSFER`)·암호화 키·서버 월 예산은 없다. 비공개 저장소의 클라우드 리뷰는 사용자가 실행마다 보낼 코드를 확인하고 동의하면 허용한다. AI 분석은 비공개 저장소를 클라우드로 보내지 않는다(AD-32 ⑤).
- 서버가 재시작되면 실행 중·대기 중 작업은 이어받지 않고 `INTERRUPTED`로 끝난다(key가 사라진 스레드의 메모리에만 있었다). 사용자가 앱에서 다시 실행한다.

## 설정·기본값

실제 설정은 [.env.example](../../.env.example)과 [AISettings](../../app/core/ai_settings.py)를 함께 확인한다. 아래는 소스 기본값이며 배포 실측이 아니다.

| 설정 | 기본/의미 |
|---|---|
| `AI_INSIGHTS_ENABLED` | 1, AI 분석 켜짐. AI 리뷰에는 서버 스위치가 없다 |
| `AI_POLICY_VERSIONS` | `code-transfer-v1`, 허용하는 전송 정책 버전 |
| `AI_MAX_FILES` / `AI_MAX_INPUT_BYTES` | 50 / 2,000,000 bytes |
| `AI_DAILY_JOB_LIMIT` | 0, 하루 실행 수 제한 없음 |
| `AI_LEASE_SECONDS` / `AI_MAX_ATTEMPTS` / `AI_RETRY_DELAY_SECONDS` | 120 / 3 / 30 |
| `AI_MAINTENANCE_INTERVAL_SECONDS` | 60, API 프로세스의 유지관리 주기. 0이면 끈다 |
| `AI_INPUT_VALID_HOURS` | 24, 새 실행 가능 기한 |
| `AI_INPUT_RETENTION_DAYS` / `AI_RESULT_RETENTION_DAYS` | 7 / 30 (입력 기록·결과 보관) |

비용 상한은 사용자의 제공자 계정에서 정한다. 서버는 실행당 호출 수·입력 크기 한도와 사용 기록(예약·정산)만 둔다. `AI_TEST_PROVIDER_BASE_URL`은 시험용이며 운영에서 비운다. key·주소·원문을 감사 산출물에 노출하지 않는다.

## 운영 명령과 효과

아래 명령은 프로젝트 루트에서 운영자가 수행하는 절차다. 이번 문서화에서는 실행하지 않았다.

| 명령 | 효과 |
|---|---|
| `python tools/ops/ai_ops.py check` | 설정·migration·제공자 주소 점검 |
| `python tools/ops/ai_ops.py status` | 작업 상태·만료 lease·사용 기록·보관 정리 상태. `input_files_with_payload`·`stored_api_keys`는 늘 0이어야 한다 |
| `python tools/ops/ai_ops.py uncertain` | 불명 예약 조회 |
| `python tools/ops/ai_ops.py resolve <예약UUID>` | 미처리 확인 후 예약 해제, DB 변경 |
| `python tools/ops/ai_ops.py resolve <예약UUID> --settle 0.12` | 확인한 비용으로 정산, DB 변경 |
| `python tools/ops/ai_ops.py cancel <작업UUID>` | 본인 대신 운영자 취소 기록, DB 변경 |
| `python tools/ops/ai_ops.py maintenance` | 실행자를 잃은 작업 정리·기한 삭제를 한 번 실행, DB 변경 |

도구 근거: [ai_ops](../../tools/ops/ai_ops.py). 조회와 변경 명령을 구분한다. 불명 예약을 자동으로 해제해 미과금으로 단정하지 않는다.

## 멈춤·불명·삭제

1. API 프로세스가 살아 있는지와 `status`의 대기 시간·만료 lease를 확인한다. AI 작업은 API 프로세스 안에서만 돈다.
2. 유지관리는 API 프로세스가 시작할 때와 주기마다 실행한다: 만료된 처리권, 실행 스레드 없이 남은 대기 작업, 기한이 지난 입력·결과를 정리한다.
3. 실행이 중단되면 자동으로 다시 실행하지 않는다. 호출 전이면 `INTERRUPTED`, 호출 중이면 `OUTCOME_UNCERTAIN`, 정산 뒤 결과 저장 전이면 `RESULT_LOST`다. 사용자가 앱에서 새 동의와 key로 다시 실행한다.
4. 제공자에서 처리 여부·비용을 확인한 뒤 불명 예약을 정리한다.
5. 입력 기록 기한·결과 기한은 다르다. 입력이 만료돼도 이미 생성된 결과·인용은 결과 보관 정책을 따른다.

현재 결과 공유는 원래 결과·작업 삭제·만료에 종속된다. 별도 팀 보관·팀 삭제 API가 있다고 안내하지 않는다. 공유에서 보이는 메모의 범위는 [AI API](../contracts/ai-api.md)와 [D01](../decisions/index.md#d01)을 따른다.

추정: 운영 기록에는 request/job/attempt/profile ID, 명령의 효과·실행 결과·환경을 남기되 코드·key·메모 본문을 덤프하지 않는 방식이 적절하다.

미확정: 실제 모델 호출·요금·설치 모델 성능·운영 복구는 이번 검증에 포함되지 않았다. [현재 검증 상태](../validation/current-status.md)를 확인한다.
