# 현재 AI 운영 안내

검토일: 2026-10-23 · 코드 기준: `a8351f0` · [설치](setup.md) · [제공자](providers.md)

확인: [기존 운영 원본](../archive/originals/docs/AI_OPERATIONS.md)의 도구·복구 절차를 현재 실행 범위와 연결했다. 원본의 팀·보고서 보관 행과 `DELETE /teams/{id}`는 폐기 기능의 과거 설명이므로 현재 절차로 사용하지 않는다.

## 설정·기본값

실제 설정은 [.env.example](../../.env.example)과 [AISettings](../../app/core/ai_settings.py)를 함께 확인한다. 아래는 소스 기본값이며 배포 실측이 아니다.

| 설정 | 기본/의미 |
|---|---|
| `AI_ENABLED` | 0, AI 기본 비활성 |
| `AI_ALLOW_EXTERNAL_TRANSFER` | 0, 클라우드 코드 전송 비활성. 로컬 생성은 별도 허용 경로 |
| `AI_MAX_FILES` / `AI_MAX_INPUT_BYTES` | 50 / 2,000,000 bytes |
| `AI_DAILY_JOB_LIMIT` | 30, 0은 횟수 무제한 |
| `AI_USER_MONTHLY_BUDGET` | USD 20, 0은 개인 금액 상한 없음 |
| `AI_SERVER_MONTHLY_BUDGET` | 0은 서버 합계 상한 비활성 |
| `AI_LEASE_SECONDS` / `AI_MAX_ATTEMPTS` | 120 / 3 |
| `AI_QUEUE_DEADLINE_SECONDS` | 1,800 |
| `AI_INPUT_VALID_HOURS` | 24, 새 실행 가능 기한 |
| `AI_INPUT_RETENTION_DAYS` / `AI_RESULT_RETENTION_DAYS` | 7 / 30 |

AI를 켜려면 원문·자격 증명용 서로 다른 암호화 키와 버전, 허용 Origin, 세션·DB 보안 설정이 필요하다. 원격 DB CA·HTTPS Secure 요구는 설정 검증을 따른다. `AI_TEST_PROVIDER_BASE_URL`은 시험용이며 운영에서 비운다. key·주소·원문을 감사 산출물에 노출하지 않는다.

클라우드는 사용자별 BYOK를 쓰고 공개 저장소만 코드 전송할 수 있다. 같은 PC 로컬은 key 없이 실행하며 현재 권한이 확인된 private도 허용한다. 외부 전송 비활성화와 AI 전체 비활성화는 다르다.

## 운영 명령과 효과

아래 명령은 프로젝트 루트에서 운영자가 수행하는 절차다. 이번 문서화에서는 실행하지 않았다.

| 명령 | 효과 |
|---|---|
| `python tools/ops/ai_ops.py check` | 설정·migration·Redis 점검 |
| `python tools/ops/ai_ops.py status` | 대기·만료 lease·outbox·보관 정리 상태 조회 |
| `python tools/ops/ai_ops.py uncertain` | 불명 예약 조회 |
| `python tools/ops/ai_ops.py resolve <예약UUID>` | 미처리 확인 후 예약 해제, DB 변경 |
| `python tools/ops/ai_ops.py resolve <예약UUID> --settle 0.12` | 확인한 비용으로 정산, DB 변경 |
| `python tools/ops/ai_ops.py cancel <작업UUID>` | 본인 대신 운영자 취소 기록, DB 변경 |
| `python tools/ops/ai_ops.py maintenance` | 복구·만료 정리, DB 변경 |

도구 근거: [ai_ops](../../tools/ops/ai_ops.py). 조회와 변경 명령을 구분한다. 불명 예약을 자동으로 해제해 미과금으로 단정하지 않는다.

## 멈춤·불명·삭제

1. dispatcher·Redis·worker와 대기 시간·outbox·lease를 확인한다.
2. dispatcher는 발행과 주기 유지관리를 수행한다. worker만 실행하면 충분하지 않다.
3. worker 중단 후 호출 전이면 복구가 재시도할 수 있지만, 호출 중이면 불명, 정산 뒤 결과 저장 전이면 RESULT_LOST로 처리한다. 자동으로 다시 과금 호출을 하지 않는다.
4. 제공자에서 처리 여부·비용을 확인한 뒤 불명 예약을 정리한다. 명시적 새 generation 실행은 별도 동의·한도 검사를 받는다.
5. 입력 원문 기한·결과 기한은 다르다. 원문 삭제 뒤에도 이미 생성된 결과·인용은 결과 보관 정책을 따른다.

현재 결과 공유는 원래 결과·작업 삭제·만료에 종속된다. 현재 별도 팀 보관·팀 삭제 API가 있다고 안내하지 않는다. 공유에서 보이는 메모의 범위는 [AI API](../contracts/ai-api.md)와 [D01](../decisions/index.md#d01)을 따른다.

추정: 운영 기록에는 request/job/attempt/profile ID, 명령의 효과·실행 결과·환경을 남기되 코드·key·메모 본문을 덤프하지 않는 방식이 적절하다.

미확정: 실제 모델 호출·요금·설치 모델 성능·운영 복구는 이번 검증에 포함되지 않았다. [현재 검증 상태](../validation/current-status.md)를 확인한다.
