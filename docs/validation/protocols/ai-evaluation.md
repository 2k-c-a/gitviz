# AI 실제 연동·모델 평가 절차

검토일: 2026-10-23 · 코드 기준: `a8351f0` · [현재 검증 상태](../current-status.md)

확인: [평가 도구](../../../scripts/evaluate_ai_review.py), [smoke 도구](../../../scripts/smoke_ai_live.py), [manifest](../../../scripts/fixtures/ai/evaluation_manifest.json)를 기준으로 한다. 현재 final 사례·합격 기준·최종 모델 평가는 미완료다. [보존 원문](../../archive/originals/docs/AI_EVALUATION_PROTOCOL.md)의 하네스 검증 완료는 모델 품질 평가 완료가 아니다.

## 1. 호출 없는 준비 점검

```powershell
python scripts/evaluate_ai_review.py --validate-manifest
python scripts/evaluate_ai_review.py --check-release
```

`--check-release`는 최종 평가 미실행도 보고하므로 평가 전 모든 항목이 비어 있어야만 시작할 수 있다는 조건으로 쓰지 않는다. 미완료를 기록하고 아래 자료·예산·동의·smoke 조건을 준비한다. 종료 코드 1을 무조건 도구 오류나 제품 회귀로 취급하지 않는다.

평가 도구 자체의 테스트는 `scripts/test_ai_evaluation.py`, smoke 도구는 `scripts/test_ai_smoke.py`, HTTP 흐름은 `scripts/verify_ai_mvp_integration.py`로 구분한다. 실제 제공자 대신 대역을 사용한 결과를 live 품질 근거로 쓰지 않는다.

## 2. 제공자·GitHub 소량 smoke

key는 해당 제공자의 `GITVIZ_SMOKE_API_KEY` 환경변수로만 준비한다. 시험 주소 `AI_TEST_PROVIDER_BASE_URL`은 비우고 명시적으로 실행한다. 다음은 예시이며 이번 정리에서 호출하지 않았다.

```powershell
python scripts/smoke_ai_live.py provider --profile gpt-5.4-v4 --live --budget 0.50
python scripts/smoke_ai_live.py provider --profile ollama-local-v1 --live --budget 0
python scripts/smoke_ai_live.py github --live --repo owner/name --pr 123 --path src/app.py
```

GitHub 단계의 인증은 `GITHUB_TOKEN`으로 준비한다. 선택한 profile·모델·prompt/schema·한도·가격 버전을 기록한다. smoke의 기존 어댑터 지원 여부와 계정·설치 모델을 먼저 확인한다. [제공자 안내](../../operations/providers.md)는 등록값이며 실제 요금·가용성 보증이 아니다.

확인 항목은 연결·key/모델 확인, token 추정과 제공자 계산/usage 비교, summary/review의 schema 수용·잘림·거절·구조/줄/인용 검사, 지연·request ID·비용이다. 로컬은 digest 또는 모델 ID 검증의 차이를 남기고 클라우드 실제 청구와 구분한다. 로컬 연산을 외부 API 과금 0으로 기록하는 것과 실행 비용·품질은 별개다.

클라우드 usage가 없으면 비용을 0으로 확정하지 않는다. 로컬 추정이 실제 입력 토큰보다 작은 경우 한도·예약의 보수성을 확인하고 평가를 진행하기 전에 해소한다. schema 거부·비용 불명·접근 실패를 숨긴 채 다음 단계로 넘어가지 않는다.

## 3. 사람 정답·기준 확정

- 공개 자료의 license 또는 사용 허락, 저장소 ID·고정 base/head SHA·선택 경로를 기록한다.
- tuning과 final은 저장소·SHA가 겹치지 않게 분리한다. 합성 tuning 두 사례는 하네스 점검용이며 모델 정확도 자료가 아니다.
- 결함 사례와 negative_control을 포함하고 언어·변경 규모·유형을 기록한다. 파일·바이트 제한과 선택 profile별 token 제한을 적용한다. 과거 24K를 모든 profile의 상한으로 쓰지 않는다.
- 사람이 모델에 제공하지 않을 외부 증거로 정답을 확인하고 path·side·줄·분류·사유·판정자를 남긴다. 가능하면 두 판정자의 불일치를 합의한다.
- `min_recall`, `min_precision`, `max_cost_per_case_usd` 기준을 final을 보기 전에 확정한다. 공란을 임의 수치로 채워 통과시키지 않는다. 지원 언어·모델별 일반화를 하지 않는다.

## 4. 실행·판정·재채점

```text
python scripts/evaluate_ai_review.py --live --set tuning --budget <USD> --base-url <URL> --cookie <본인세션> --repository-id <ID>
python scripts/evaluate_ai_review.py --rescore reports/ai-eval-tuning-<실행>.json --base-url <URL> --cookie <본인세션>
```

실행 → 각 지적을 사람이 valid/false_positive/uncertain으로 판정 → `--rescore` 순서다. 세션·key를 공유 보고서에 기록하지 않는다. tuning으로 조정한 뒤 final을 한 번 실행한다. prompt/schema/profile이 바뀌면 적용 버전과 평가 대상을 구분한다.

위치가 정답과 겹치면 후보일 뿐이다. 사람 valid만 발견으로 세고 오탐·미판정·불확실을 별도 보고한다. 실패·누락·미판정·불완전 coverage·예산 중단이 있으면 hold를 우선한다. 분석 범위는 완료 결과 API에서 읽고 실행 전 job coverage를 대신 쓰지 않는다.

## 5. 중단·불명 비용 복구

도구는 실행 전 `reports/ai-eval-<세트>-state.json`을 배타적으로 만들고 사례별 제출 전 본문·멱등 key·예상 상한·단계를 원자적으로 저장한다. 비용은 사례 기록에서 다시 계산한다. 불명 호출은 예상 상한을 예산에 유지하며 기본적으로 중단한다.

`--resume`은 job ID가 있으면 조회만 하고, ID가 없을 때만 최초 본문·동일 key로 접수 여부를 복구한다. 이미 끝난 사례를 재호출하지 않는다. 정산 여부가 불명이면 상한을 풀지 않고 운영자가 제공자와 GitViz 기록을 확인한다. `--continue-after-uncertain`도 불명 상한을 예산에서 빼지 않는다.

final 완료 뒤 `reports/FINAL_RUN_DONE.json`이 생기면 새 final·resume을 거부한다. 재판정은 `--rescore`로 하고 tuning만 `--fresh`로 이전 실행을 보관하고 새로 시작할 수 있다.

## 보고·완료 조건

보고서에 commit·실행 환경·profile/prompt/schema·자료 SHA·사람 정답·기준·coverage·유효/오탐/불확실/미판정·비용/불명 상한·지연·실패·한계를 남긴다. 모든 확인이 끝나면 `--check-release`를 다시 실행하고 [현재 검증 상태](../current-status.md)에 새 실행 링크를 추가한다. 과거 수용 보고 원문을 새 결과로 덮어쓰지 않는다.

추정: 소규모 평가의 품질·생산성 결과는 과제·언어·모델·자료 범위에 한정한다. 미확정: 사람 자료·기준·실제 계정·설치 모델 검증은 별도 실행이 필요하다.
