# AI MVP 수용 보고 (S10)

작성 기준: 2026-10-07, 커밋 후 상태. 이 문서는 **무엇을 실행했고 무엇을 아직 하지 않았는지**를 구분해서 적는다. 구조 검증·fake provider 통과는 모델 정확도의 근거가 아니다.

## 1. 결론

| 구분 | 상태 |
|---|---|
| 보안·구조·작업 일관성 (fake provider, 실제 PostgreSQL·Redis·별도 프로세스) | **충족** — 아래 §2·§3 |
| 운영자 수행 가능(설정 점검·상태·취소·불명 호출 정리·기한 삭제) | **충족** — `scripts/ai_ops.py`, [AI_OPERATIONS.md](AI_OPERATIONS.md) |
| 실제 모델 정확도 평가 | **미실행 — 출시 전 필수** (§5) |
| 실제 GitHub·제공자 연동, 운영 스냅샷 검증 | **미확인 — 출시 전 필수** (§6) |

따라서 현재 상태는 "기능·안전 측면 수용 가능, 모델 품질은 근거 없음"이다. 실제 모델 평가가 끝나고 합의한 기준을 넘기 전에는 사용자에게 품질을 보장하는 표현을 쓰지 않는다(화면도 "사람 검증 전, 참고용"으로 표시한다).

## 2. 실행한 검증과 결과

모든 실행은 일회용 PostgreSQL 16·Redis 7 컨테이너와 로컬 가짜 제공자로 했다. 외부 AI 제공자·GitHub·운영 DB는 쓰지 않았다.

| 검증 | 파일 | 결과 |
|---|---|---|
| **통합**(실제 PG·Redis + app 서버·dispatcher·Celery worker·가짜 제공자 프로세스 + HTTP) | `scripts/verify_ai_mvp_integration.py` | 16개 통과 |
| 평가 도구(채점·세트 분리·예산 보호·보류 판정·재채점·실행 기록·중단 복구) | `scripts/test_ai_evaluation.py` | 41개 통과 |
| smoke 도구(로컬 가짜 제공자로 검증, 실제 호출 없음) | `scripts/test_ai_smoke.py` | 9개 통과 |
| 운영 도구·기한 삭제 | `scripts/test_ai_ops.py` | 7개 통과 |
| 생성 실행(S7) | `scripts/test_ai_generation.py` | 40개 통과 |
| 생성·결과·피드백 API(S8) | `scripts/test_ai_api.py` | 34개 통과 |
| GitViz 이력 근거(S11) | `scripts/test_ai_history_context.py` | 36개 통과 |
| 인수인계(S12) | `scripts/test_ai_handover.py`, `scripts/test_ai_handover_frontend.cjs` | 32 + 9개 통과 |
| 키 관리·동의·작업 고정(BYOK) | `scripts/test_ai_credentials.py`, `test_ai_credential_api.py`, `test_ai_pulls_api.py` | 30 + 8 + 5개 통과 |
| 작업 수명주기·입력 준비·큐(S4·S5) | `test_ai_job_lifecycle.py`, `test_ai_inputs.py`, `test_ai_input_api.py`, `test_ai_queue_integration.py` | 55 + 28 + 17 + 7개 통과 |
| 계약·보안·스키마·수집·제공자·출력 검증 | `test_ai_contracts/security/schema/pr_collection/provider/output_validation.py` | 28 + 32 + 27 + 43 + 38 + 24개 통과 |
| 프런트 순수 모듈(주소·멱등 key·문구·API·폴링) | `scripts/test_ai_frontend.cjs` | 29개 통과 |
| 기존 analytics·인증 회귀 | `test_backend_improvements.py`, `test_analytics_calculations.py`, `test_metric_policy.py`, `test_repository_snapshots.py`, `test_lightweight_score.py`, `test_metric_display.cjs`, `test_viz_modules.cjs` | 모두 통과 |
| 브라우저(실제 app·worker·dispatcher·가짜 제공자) | 수동: 키 등록 → PR 선택 → 파일 선택 → 미리보기 → 동의 → 리뷰 실행 → 결과 → 근거 코드 → 피드백 | 통과(스택은 `scripts/ai_integration_stack.py`) |
| 브라우저(모의 서버) | S9: 401/404/429/503, 만료, 취소, 결과 불명, 지적 0건, 이전 SHA, HTML·script 출력 안전성, 모바일 폭 | 통과 |

통합 검증이 직접 확인하는 것: 정상 흐름과 호출 횟수(설명·리뷰 각 1회, 동의 전 0회), 사용자 key만 제공자에 전달(서버 공용 key·남은 환경변수 주소 무시), 요청에 도구·fallback 없음, **큐 발행 실패**(닫힌 Redis에서도 작업 유실 없음, 복구 후 1회만 처리), **worker 강제 종료**(호출 도중 → `OUTCOME_UNCERTAIN`, 새 worker가 다시 호출하지 않음, 불명 예약이 예산을 점유, 명시적 재실행은 새 generation), **응답 유실**(자동 재호출 없음), **제공자 5xx**(request-id가 있는 처리 안 된 오류만 자동 재시도), 출력 검증 실패(결과 저장 없음·재호출 없음·비용 기록), **취소 경쟁**(결과 비공개·비용 정산), **원문 삭제·기한 삭제**(작업 취소·암호문 삭제·결과 만료 후 삭제), **키 삭제**(대기 작업 취소·호출 없음), 권한 경계(남의 자료 404·비로그인 401·CSRF/Origin 403), 로그·DB 평문에 key·코드 없음, 평가 도구가 실제 HTTP 흐름을 지나 채점하고 예산 부족 시 전송 전에 멈춤.

## 3. 통합 검증에서 드러난 결함과 수정

| 결함 | 영향 | 수정 |
|---|---|---|
| 환경변수 `ANTHROPIC_BASE_URL`이 남아 있으면 SDK가 사용자 API key를 그 주소로 보낸다(확인: 다른 주소로 바뀜) | 사용자 key 유출 경로 | 어댑터가 `base_url`을 항상 명시(공식 주소). 통합 시험 전용 `AI_TEST_PROVIDER_BASE_URL`은 로컬 주소만 허용(설정 검증). 회귀 테스트·통합 검증에 반대 환경변수를 심어 둠 |
| 닫힌 Redis로 발행하면 **64초** 동안 막힘 | dispatcher의 다른 발행·유지관리 지연(복구 지연) | 발행 연결을 짧게 한 번만 시도(약 4초에 실패 → outbox backoff 재시도). 통합 검증이 25초 안 기록을 요구 |
| 결과 보관 기간이 지나도 결과 행이 지워지지 않음 | 보관 기한 미이행 | 유지관리가 만료 결과를 지적·피드백과 함께 삭제하고 작업을 삭제 표시(비용 기록 유지). 통합·단위 검증 |

## 4. 수용 기준 판정

| 기준 | 판정 | 근거 |
|---|---|---|
| 모델 호출 전 동의·권한·키·취소 재검증, 동의 없이 전송 없음 | 충족 | S7 순서 검증, 통합 검증(동의 전 호출 0) |
| 사용자 key 암호화·마스킹·삭제 시 즉시 파기, 로그·큐·옵션에 key 없음 | 충족 | 자격 증명 테스트, 통합 검증(로그·DB 평문 검사) |
| 처리된 호출의 자동 재호출 금지(불명·정산 후 중단) | 충족 | S7 복구 테스트, 통합 검증(worker 종료·응답 유실) |
| 결과 불변·원본 비변경 피드백, 결과·상태 원자성 | 충족 | S8·S7 테스트, DB trigger |
| 비공개 저장소 코드 외부 전송 금지 | 충족(MVP 정책) | `authorize_ai`(generate) |
| 보관 기간 후 삭제(원문·결과·key) | 충족 | 통합 검증, 운영 도구 |
| 운영자가 설정·확인·취소·복구·기한 삭제 수행 | 충족 | `ai_ops.py check/status/uncertain/resolve/cancel/maintenance` + 문서 |
| 실제 모델의 지적 품질(유효 지적·오탐·보류), 합의한 기준 | **미충족(미실행)** | §5 |

## 5. 실제 모델 평가 — 미실행

**하지 않은 이유**: 평가에는 평가자 본인의 API key·별도 예산·**사람이 붙인 정답이 있는 공개 자료**가 필요하다. 이 환경에는 key가 없고, 정답이 있는 실제 평가 세트는 사람이 마련해야 한다(임의로 만들어 채우지 않았다).

**절차**: [AI_EVALUATION_PROTOCOL.md](AI_EVALUATION_PROTOCOL.md) — ① 평가 도구 점검(완료) → ② 실제 연동 소량 smoke(`scripts/smoke_ai_live.py`, 평가자가 실행) → ③ 사람 정답·합격 기준 확정 → ④ 모델 평가.

**준비된 것**: `scripts/evaluate_ai_review.py`(채점·세트 분리·예산 보호·보고서·`--rescore`). 위치 일치는 후보일 뿐 사람이 valid로 판정한 지적만 발견으로 세고, 결과를 모르는 호출의 상한은 예산에 계속 반영하며, 미완료·중단·미판정이 있으면 판정을 보류한다. 분석 범위는 결과 API에서 읽는다, `scripts/fixtures/ai/evaluation_manifest.json`(구조 + 하네스 점검용 합성 조정 사례 2개). 합성 사례는 정확도 평가에 쓰지 않으며 `final` 세트는 비어 있다.

**출시 전에 해야 할 일** (`python scripts/evaluate_ai_review.py --check-release`가 현재 남은 항목을 출력한다):
1. 공개 저장소(license 확인)의 고정 base/head SHA와 사람 정답을 모아 `tuning`·`final`을 만든다. 결함이 있는 사례와 결함이 없는 사례(negative_control)를 섞고 언어·변경 크기를 다양하게 한다.
2. 합격 기준(`criteria`: 재현율·판정된 지적의 유효 비율·사례당 비용)을 **실행 전에** 합의한다. 비어 있으면 도구가 합격/불합격을 계산하지 않는다.
3. `tuning`으로 프롬프트·검증기를 조정한다. 조정이 끝나면 `final`을 **한 번만** 실행한다(`--live --set final --budget <USD>`).
4. 지적 중 정답과 겹치지 않은 것은 사람이 화면 피드백(유효/오탐/불확실)으로 판정한다. 판정되지 않은 지적은 정확도 수치에서 따로 센다.
5. 보고서에 결과·비용·coverage·유효 지적·오탐·보류·언어·범위 한계를 적는다.

## 6. 출시 전 필수 확인(남은 항목)

- **운영 스냅샷 검증** `scripts/verify_snapshot_integration.py`: 운영 DB(repo 9 스냅샷·clone)가 필요해 이 환경에서 확인하지 못했다. 시험 DB에서는 migration·trigger까지만 통과하고 `test_backend_improvements.py`가 일부러 남긴 손상 스냅샷 때문에 이어지는 검사가 실패한다(이번 변경과 무관).
- **실제 GitHub 연동**: OAuth 로그인, 실제 PR 목록·파일·고정 SHA 수집, 비공개 저장소 차단, 권한 상실은 대역으로만 확인했다.
- **실제 제공자 smoke**: key 확인(`models` 조회), `count_tokens`, 구조화 출력 schema 수용, 응답 형식은 mock·가짜 제공자로만 확인했다. 실제 key로 설명·리뷰 각 1건을 먼저 소량 실행해 schema 거부·가격·한도가 profile 값과 맞는지 확인한다.
- **운영 환경**: HTTPS·`SESSION_COOKIE_SECURE`, 원격 DB CA, worker·dispatcher 상시 실행과 재시작 정책, 로그 보관·접근, 백업, 암호화 키 보관·교체 절차 점검.

## 7. 알려진 한계(사용자·운영자에게 알려야 하는 것)

- **제공자·모델**: Anthropic의 등록된 profile(Opus·Sonnet 5.5)만 쓸 수 있고 자동 fallback은 없다. 가격·한도는 profile에 고정된 값이며 제공자 가격 변경은 새 profile로 반영해야 한다.
- **범위**: 입력은 선택한 파일의 diff(+주변 25줄)와 PR 제목·설명이다. 파일 수(기본 50)·크기·토큰 한도(24,000)를 넘으면 접수할 수 없다. 비밀 의심·바이너리·생성 파일·링크/서브모듈·너무 큰 파일은 제외되며 coverage·제외 사유로 표시된다. PR 전체가 아니라 **선택한 파일만** 분석한다. 모델 프롬프트에는 저장소의 다른 코드가 없으므로 호출부·테스트 결과를 알 수 없다(출력의 `unknowns`).
- **언어**: 언어별 지원 여부를 평가하지 않았다. 텍스트로 해석되는 소스만 다루며 언어별 정확도는 §5의 평가로 확인해야 한다.
- **정확도**: 구조·참조 검증(파일·줄·인용 일치)은 지어낸 위치를 걸러낼 뿐 주장의 옳고 그름을 보증하지 않는다. 지적 0건은 문제가 없다는 뜻이 아니다. 모든 결과는 사람 검증 전 상태로 시작한다.
- **비용**: 비용은 사용자의 API key 계정에 청구된다. 화면의 예상 비용은 로컬 보수적 상한이며, 실제 비용은 제공자 usage로 정산한다. 결과를 알 수 없는 호출은 비용이 발생했을 수 있고 운영자가 확인해 정리할 때까지 예산을 점유한다.
- **개인정보·전송**: 코드는 사용자가 동의한 때에만 선택한 제공자로 전송된다. 제공자 쪽 보관·학습 정책은 사용자 key 계정의 약관을 따른다.
- **PR 변경**: 결과는 고정된 SHA 기준이다. 이후 PR이 바뀌면 화면이 이전 SHA 결과라고 표시한다.

## 8. 재현 명령

```bash
# 일회용 PostgreSQL·Redis (테스트 전용 DB 이름은 gitviz_test로 시작해야 한다)
export GITVIZ_TEST_DATABASE_URL=postgresql://gitviz_test:validation@localhost:5432/gitviz_test
export GITVIZ_TEST_REDIS_URL=redis://localhost:6379/0      # 시작 시 이 Redis DB를 비운다
python scripts/verify_ai_mvp_integration.py
python scripts/test_ai_evaluation.py
python scripts/test_ai_ops.py
node scripts/test_ai_frontend.cjs
python scripts/evaluate_ai_review.py --check-release       # 남은 평가 항목
```
