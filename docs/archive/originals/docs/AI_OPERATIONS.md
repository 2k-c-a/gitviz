# AI 기능 운영 안내

대상: AI 리뷰 기능(BYOK)을 켜고 운영하는 사람. 사용자 API key는 사용자가 등록하며 서버 공용 제공자 key는 없다. 설계·계약은 [AI_INTERFACE_CONTRACT.md](AI_INTERFACE_CONTRACT.md), 수용 상태는 [AI_MVP_ACCEPTANCE.md](AI_MVP_ACCEPTANCE.md).

## 1. 구성

| 프로세스 | 하는 일 | 실행 |
|---|---|---|
| API 서버 | `/api/ai/v1/*`, 화면 서빙 | `python -m uvicorn app.main:app` |
| dispatcher | outbox → Redis 발행, **주기 유지관리**(만료 처리권 복구, 대기 기한, 만료 입력·결과·key 정리) | `python -m app.tasks.ai_dispatch` (compose: `ai-dispatcher`) |
| worker | 입력 준비·설명·리뷰 실행 | `celery -A app.core.celery_app.celery worker -Q ai_prepare,ai_generate,ai_maintenance` (compose: `ai-worker`) |
| PostgreSQL / Redis | 상태·결과 / 작업 큐 | 기존 `docker compose up -d db redis` |

`docker compose --profile ai up`으로 worker와 dispatcher를 함께 띄운다. dispatcher는 **반드시 하나 이상** 떠 있어야 한다(없으면 접수된 작업이 대기열로 나가지 않고 `AI_QUEUE_DEADLINE_SECONDS` 뒤에 `QUEUE_TIMEOUT`으로 실패한다). 여러 개를 띄워도 안전하다(`SKIP LOCKED`).

## 2. 켜기 전 설정

`.env.example`의 AI 항목을 따른다. 필수: `AI_ENABLED=1`, `AI_INPUT_ENCRYPTION_KEYS`/`AI_INPUT_KEY_VERSION`(원문 코드), `AI_CREDENTIAL_ENCRYPTION_KEYS`/`AI_CREDENTIAL_KEY_VERSION`(사용자 key, **입력 키와 다른 값**), `AI_ALLOWED_ORIGINS`(또는 `FRONTEND_URL`), HTTPS면 `SESSION_COOKIE_SECURE=1`, 원격 DB면 `DATABASE_SSL_CA`. 코드를 외부 제공자로 보내려면 `AI_ALLOW_EXTERNAL_TRANSFER=1`(꺼 두면 입력 준비·조회만 된다).

```bash
python scripts/ai_ops.py check
```

설정·migration·Redis를 점검하고 문제가 있으면 종료 코드 1이다. 잘못된 설정이면 앱이 시작되지 않는다(`require_valid_ai_settings`).

- **운영에서 비워야 하는 값**: `AI_TEST_PROVIDER_BASE_URL`(통합 시험 전용, 로컬 주소만 허용 — 앱 시작 검증과 provider 어댑터 생성 때 모두 확인한다). 사용자 key는 이 값이 없으면 항상 공식 주소로만 간다. `ANTHROPIC_BASE_URL`·`ANTHROPIC_API_KEY` 환경변수는 AI 기능이 **무시**한다(`check`가 남아 있으면 알려 준다).
- **키 교체(암호화 키)**: 새 키를 `AI_*_ENCRYPTION_KEYS`에 `버전:hex`로 **추가**하고 `*_KEY_VERSION`을 새 버전으로 바꾼다. 옛 버전 키는 기존 암호문을 읽는 동안 남겨 둔다. 원문 코드는 보관 기간이 지나면 지워지므로 옛 키는 그 뒤에 제거할 수 있다. 사용자 key는 사용자가 key를 교체하면 새 암호화 키로 다시 저장된다.
- **예산·한도**: `AI_USER_MONTHLY_BUDGET`(개인 지출 상한, 0이면 상한 없음), `AI_SERVER_MONTHLY_BUDGET`(0이면 끔, 서버 합계 상한), `AI_DAILY_JOB_LIMIT`(0이면 횟수 상한 없음), `AI_CREDENTIAL_VERIFY_LIMIT_PER_HOUR`(0이면 key 확인 횟수 상한 없음), `AI_ALLOWED_PROFILES`(사용자가 고를 수 있는 모델). 상한을 해제해도 사용량·예약·정산 기록은 유지하고 비용은 사용자 key의 계정에 청구된다.
- **확장 토큰 profile**: 클라우드 모델의 v4는 입력 128,000·출력 16,384토큰이다. 기존 profile은 기존 작업의 동의·fingerprint를 위해 유지한다. 로컬 모델은 `AI_<런타임>_MAX_INPUT_TOKENS`와 `MAX_OUTPUT_TOKENS`로 설정하며 합계는 실제 context 한도 이하여야 한다.

## 3. 평소 확인

```bash
python scripts/ai_ops.py status
```

| 항목 | 정상 | 이상일 때 |
|---|---|---|
| `jobs` | 대기(`queued`)·실행(`running`)이 오래 쌓이지 않는다 | worker가 없거나 느리다 → §5 |
| `oldest_waiting_seconds` | 수십 초 이내 | 크면 dispatcher·worker 확인 |
| `running_with_expired_lease` | 0 (잠깐 생겼다가 유지관리가 정리) | 계속 남으면 dispatcher 유지관리가 멈춘 것 |
| `outbox_unpublished` / `outbox_failing` | 0 또는 일시적 | `outbox_failing>0`이 계속되면 Redis 연결 확인(발행은 backoff 재시도, 작업은 유실되지 않는다) |
| `uncertain_reservations` | 0 | §4 |
| `inputs_expired_with_payload`, `results_expired` | 0 (유지관리가 삭제) | 계속 남으면 dispatcher 유지관리 확인 |
| `retired_credentials_with_secret` | 진행 중 작업이 쓰는 동안만 | 계속 남으면 해당 작업 확인 |

로그에는 식별자·분류 코드만 남는다(코드·모델 출력·API key 없음). 사고 조사 시 `audit_events`(키 등록·교체·삭제, 입력·작업 삭제, 운영자 조치)를 본다.

## 4. 결과를 알 수 없는 호출(불명)

제공자가 요청을 처리했는지 알 수 없을 때(응답 대기 중 timeout, worker 중단, 연결 끊김, 출처 불명의 5xx) 작업은 `failed`(`OUTCOME_UNCERTAIN`·`PROVIDER_TIMEOUT`)로 끝나고 **자동으로 다시 호출하지 않는다**. 예약은 `uncertain`으로 남아 사용자의 월 예산을 점유한다.

```bash
python scripts/ai_ops.py uncertain                      # 목록(예약 id·작업·금액)
python scripts/ai_ops.py resolve <예약id>               # 호출이 처리되지 않았음을 확인했을 때: 해제
python scripts/ai_ops.py resolve <예약id> --settle 0.12 # 제공자 콘솔에서 실제 비용을 확인했을 때: 그 금액으로 정산
```

사용자는 화면의 "다시 실행"(동의를 다시 받음)으로 새 generation을 시작할 수 있다. 사용자 계정의 제공자 콘솔(사용 내역)에서 해당 시각의 호출 여부를 확인한 뒤 정리하는 것이 정확하다. 확인할 수 없으면 해제보다 정산이 보수적이다. `RESULT_LOST`(호출은 정산됐지만 결과를 저장하지 못함)는 비용이 이미 기록돼 있어 정리할 것이 없다.

## 5. 작업이 멈췄을 때

1. `status`로 대기·실행 작업과 `oldest_waiting_seconds`를 본다.
2. dispatcher가 떠 있는지 확인한다. 없으면 띄운다 — outbox에 남은 작업이 곧 발행된다(작업 유실 없음).
3. worker가 없으면 띄운다. worker가 **중단**됐다면 처리권(`AI_LEASE_SECONDS`) 만료 뒤 dispatcher 유지관리가 정리한다: 호출 전이었으면 재시도, 호출 중이었으면 `OUTCOME_UNCERTAIN`(§4), 정산 뒤 저장 전이었으면 `RESULT_LOST`. 지금 바로 정리하려면 `python scripts/ai_ops.py maintenance`.
4. Redis가 죽었다면 복구한다. 발행은 짧게(약 4초) 실패하고 outbox가 backoff로 재시도하므로 복구 후 자동으로 이어진다.
5. 특정 작업을 멈추려면 `python scripts/ai_ops.py cancel <작업id>`(대기 중이면 즉시 취소, 실행 중이면 취소 접수 — 이미 시작된 제공자 호출은 끝나고 그 비용은 정산되지만 결과는 공개되지 않는다).

## 6. 보관 기간과 삭제

| 대상 | 기간 | 삭제 방식 |
|---|---|---|
| 새 실행 시작 가능 | `AI_INPUT_VALID_HOURS`(24h) | 이후 접수는 `INPUT_EXPIRED`(410) |
| 원문 코드(암호화) | `AI_INPUT_RETENTION_DAYS`(7일) | 유지관리가 암호문을 지우고 입력을 `expired`로 닫고 연결 작업을 취소 |
| 결과·지적·피드백 | `AI_RESULT_RETENTION_DAYS`(30일) | 유지관리가 결과를 삭제하고 작업을 삭제 표시(비용 기록은 남김) |
| Idempotency 키 | 24시간 | 만료 후 정리. 삭제된 작업의 키도 이 기간 동안 유지돼 재전송이 새 작업·재과금을 만들지 않는다 |
| 사용자 key 암호문 | 교체 후 그 key에 고정된 작업이 끝나면 / 삭제 시 즉시 | 삭제는 암호문을 즉시 지우고 고정된 진행 중 작업을 취소 |
| 팀·구성원·연결 저장소·공유·보고서 | 기한 없음(관리자가 삭제할 때까지). 공유는 원본(결과·인계 대상)이 사라지거나 저장소 연결을 끊으면 함께 삭제 | `DELETE /teams/{id}`로 팀과 하위 자료를 함께 삭제(개인 자료는 그대로). 보고서는 지적의 제목·분류·위치만 담고 코드 원문·인용·메모는 담지 않으며, 권한이 줄면 차단된다 |

사용자는 화면·API로 입력(원문)·작업(결과)·key를 언제든 삭제할 수 있다. 감사 기록·비용 기록(예약·예산)에는 코드·메모·key가 없고 삭제하지 않는다.

## 7. 사고 대응 체크리스트

- **사용자 key 유출 의심**: 해당 사용자가 제공자 콘솔에서 key를 폐기하고 화면에서 key를 삭제·재등록하게 한다(삭제하면 암호문이 즉시 지워지고 고정된 진행 중 작업이 취소된다). 서버 로그에는 key가 남지 않는다.
- **입력 암호화 키 유출 의심**: 새 키를 추가·전환하고, 보관 중인 원문이 필요 없다면 `AI_INPUT_RETENTION_DAYS`를 줄여 기한 삭제를 앞당기거나 사용자에게 입력 삭제를 안내한다.
- **외부 전송을 즉시 막기**: `AI_ALLOW_EXTERNAL_TRANSFER=0`으로 API 서버·worker를 재시작한다. 이후 접수·실행이 `TRANSFER_NOT_ALLOWED`로 거부되고 이미 접수된 작업도 호출 직전 재검증에서 막힌다.
- **기능 전체 끄기**: `AI_ENABLED=0`. 모든 AI 경로가 404를 돌려준다.
- **제공자 장애**: 429·출처가 확인된 5xx는 자동 재시도(호출이 처리되지 않았음이 확실한 경우만), 그 밖에는 `failed`로 끝나 사용자가 다시 실행한다.

## 8. 검증 명령

```bash
python scripts/verify_ai_mvp_integration.py     # 실제 PG·Redis + 별도 프로세스 + 가짜 제공자(운영 데이터·실제 제공자 없음)
python scripts/test_ai_ops.py                   # 운영 도구·기한 삭제
python scripts/evaluate_ai_review.py --check-release   # 출시 전 사람이 채워야 할 평가 항목
```

실제 모델 평가(`evaluate_ai_review.py --live`)는 일반 CI와 분리돼 있고 평가자의 key·별도 예산이 필요하다. 절차와 현재 상태는 [AI_MVP_ACCEPTANCE.md](AI_MVP_ACCEPTANCE.md).

## PR·커밋 리뷰 및 폐기 기능 (2026-10-11)

`alembic upgrade head`로 커밋 리뷰의 `source_type` 및 nullable PR 식별자 마이그레이션을 적용한 뒤 uvicorn·Celery·dispatcher를 재시작합니다. S12·S13 API·화면은 제거됐으며 기존 테이블은 과거 자료 보존을 위해 유지합니다. 커밋 행이 있는 DB의 해당 마이그레이션 downgrade는 자료 유실을 막기 위해 거부합니다. 기존 불변 이력 근거는 유지하며 전체 변경 후보가 필요하면 입력을 새로 준비합니다. S14·S15는 보류합니다.
