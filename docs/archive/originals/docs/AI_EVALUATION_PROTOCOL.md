# AI 리뷰 실제 모델 평가 절차

순서: **① 평가 도구 점검(완료) → ② 실제 연동 소량 smoke → ③ 사람 정답·합격 기준 확정 → ④ 모델 평가**. 각 단계의 도구와 지금 상태를 적는다. 수용 보고는 [AI_MVP_ACCEPTANCE.md](AI_MVP_ACCEPTANCE.md).

## ① 평가 도구 (`scripts/evaluate_ai_review.py`) — 점검 완료

채점 규칙(테스트 `scripts/test_ai_evaluation.py`, 통합 `verify_ai_mvp_integration.py`의 EvaluationHarness):

- **위치 일치는 후보일 뿐이다.** 정답과 줄 위치가 겹치는 지적은 *후보*이며, 사람이 `valid`로 판정한 지적만 "찾은 결함"(재현율의 분자)이다. `false_positive`로 판정된 지적은 발견이 아니고, 미판정·불확실은 **확인 대기**로 남는다.
- **비용은 불명 호출을 포함한다.** 결과를 모르는 호출(`OUTCOME_UNCERTAIN`·`PROVIDER_TIMEOUT`·시간 초과)은 정산액이 늘지 않아도 예상 비용 상한을 평가 예산에 계속 반영한다. 기본은 그 시점에서 평가를 중단한다(`--continue-after-uncertain`으로 계속하더라도 상한은 예산에서 빠지지 않는다). 계정의 남은 월 한도(`user_remaining`)도 함께 확인한다.
- **보류가 우선한다.** 실패·미시작 사례(결함 없는 대조 사례 포함), 예산·불명 호출 중단, 사례 누락, 확인 대기·미판정 지적, 분석 범위가 `complete`가 아니거나 확인하지 못한 사례가 하나라도 있으면 판정은 `hold`다. 모든 사례가 끝나고 모든 지적이 판정된 뒤에만 기준과 비교해 `pass`/`fail`을 낸다. 기준이 비어 있으면 `criteria_missing`.
- **분석 범위는 결과 API(`GET /results/{id}`)에서 읽는다.** 작업 응답의 `coverage`는 실행 전 입력의 값(분석 범위 `none`)일 수 있으므로 분석 범위로 쓰지 않는다. 성공하지 못한 사례는 입력 단계 값임을 표시하고 분석 범위로 집계하지 않는다.
- 결함 없는 대조 사례에서 나온 지적 수, 오탐 수, 확인 대기, 미판정, 정산 비용·불명 상한 포함 비용을 따로 보고한다. 구조 검증 통과는 정확도가 아니다.

명령:

```bash
python scripts/evaluate_ai_review.py --validate-manifest          # 평가 세트 형식·세트 분리 검증(호출 없음)
python scripts/evaluate_ai_review.py --check-release              # 출시 전에 사람이 채워야 할 항목
python scripts/evaluate_ai_review.py --live --set tuning --budget 3 --base-url https://… --cookie "gitviz_session=…" --repository-id <GitViz 저장소 id>
python scripts/evaluate_ai_review.py --rescore reports/ai-eval-tuning-….json --base-url https://… --cookie "gitviz_session=…"   # 사람이 판정한 뒤 재채점(호출·비용 없음)
```

실행 → **사람이 화면에서 각 지적을 판정(유효/오탐/불확실)** → `--rescore` 순서다.

**실행 기록과 중단 복구**
- 실행을 시작하기 **전에** `reports/ai-eval-<세트>-state.json`을 원자적으로(`O_EXCL`) 만든다. 정상 종료 뒤에 남기는 표시가 아니므로, 접수 뒤 연결이 끊겨 비정상 종료해도 기록이 남고, 같은 명령은 다시 실행되지 않는다(두 번째 실행은 거부). 최종 세트는 `--fresh`로도 새로 시작할 수 없다.
- 비용 합계(정산·보류 상한)는 따로 저장하지 않고 **사례 기록에서 매번 다시 계산**한다. 사례 완료와 그 정산액·상한이 한 번의 원자적 저장이므로, 완료를 저장한 직후 프로세스가 종료돼도 합계가 이전 값으로 남지 않는다(복구 시 완료 사례는 건너뛰지만 그 비용은 합계에 반영돼 있다).
- 사례마다 진행 단계(started → submitting → accepted → done/interrupted)와 예상 상한·정산 기준을 접수 **전에** 기록하고, 접수 요청의 Idempotency-Key는 (실행 id, 사례, 단계)로 정한 결정적 값이다.
- 중단되면 접수됐을 수 있는 작업의 예상 상한을 예산에 잡아 두고 종료 코드 3으로 끝난다. 복구 중 다시 끊겨도 잡아 둔 상한과 접수 상태는 결과를 확인할 때까지 그대로 유지한다(중복해서 더하지도, 풀지도 않는다). **`--resume`**으로 이어가면 저장된 `job_id`가 있으면 그 작업을 **조회만** 한다(새 미리보기·동의를 쓰지 않는다: 중단 사이 key·모델이 바뀌어도 409가 나지 않는다). 접수 여부가 불명확할 때(`job_id` 없음)만 접수 전에 저장해 둔 **최초 요청 본문 그대로** 같은 key로 다시 보내 서버가 이미 접수한 작업을 돌려받는다. 그 재전송까지 거절되거나 작업이 조회되지 않으면 완료로 기록하지 않고 상한을 유지한 채 멈추므로, 제공자 콘솔과 GitViz에서 작업 여부를 확인한 뒤 판단한다. 결과가 확인되면 잡아 둔 상한이 풀리고 실제 정산 비용으로 바뀐다. 끝난 사례는 다시 실행하지 않는다.
- 최종 세트가 끝까지 실행되면 `reports/FINAL_RUN_DONE.json`이 만들어지고 이후에는 `--resume`도 거부된다(재판정은 `--rescore`). 조정(tuning) 세트는 `--fresh`로 이전 기록을 보관하고 새로 시작할 수 있다.

## ② 실제 연동 소량 smoke (`scripts/smoke_ai_live.py`) — 평가자가 실행

도구와 로컬 검증(`scripts/test_ai_smoke.py`)은 준비돼 있고, 실제 key·GitHub 호출은 하지 않았다. 평가 전에 한 번 실행하고 결과를 보고서에 붙인다.

```bash
GITVIZ_SMOKE_API_KEY=sk-ant-… python scripts/smoke_ai_live.py provider --live --budget 0.50     # key는 환경변수로만
GITHUB_TOKEN=… python scripts/smoke_ai_live.py github --live --repo owner/name --pr 123 --path src/app.py   # 공개 저장소, 읽기 전용
```

**주소와 key**: 실제 smoke는 **공식 주소만** 쓴다. `AI_TEST_PROVIDER_BASE_URL`이 설정돼 있으면 key를 보내기 전에 거부한다(환경변수를 비우고 실행). 어댑터도 생성할 때 이 값을 로컬 주소(localhost·127.0.0.1)로만 허용하므로 앱 시작 검증을 거치지 않는 독립 실행에서도 key가 외부 주소로 가지 않는다. `ANTHROPIC_BASE_URL` 환경변수는 무시한다.

확인 항목: key 확인(models 조회) → `count_tokens`가 실제 출력 schema를 받아 토큰 수를 돌려주는가(로컬 추정이 더 큰가) → 설명·리뷰 각 1회 생성에서 **구조화 출력 schema를 제공자가 수용하는가**(거부·잘림·거절 여부) → usage와 profile 가격으로 계산한 비용이 제공자 콘솔 청구와 맞는가 → 우리 검증기 통과 → 지연·request-id. GitHub 단계: 공개 저장소 확인, PR 목록·파일 목록(목록 품질), 고정 SHA 캡처, 고정 코드 수집(blobless fetch).

통과 기준: 모든 단계 OK, 로컬 추정이 실제 토큰 수 이상, 계산 비용이 콘솔 값과 근사. **로컬 토큰 추정이 제공자 `count_tokens`(또는 응답 usage의 입력 토큰)보다 작으면 실패**한다(예약·한도 검사가 보수적이지 않다). **응답에 usage가 없으면 비용을 0으로 보고하지 않고 "미확인"으로 실패 처리**한다(제공자 콘솔에서 직접 확인). 하나라도 실패하면 원인(schema 거부·가격·한도·형식)을 고친 뒤 다시 실행하고 ③으로 가지 않는다. 구조화 출력 schema가 거부되면 `prompts`의 schema를 고치고 프롬프트 digest·profile 버전을 올린다.

## ③ 사람 정답·합격 기준 확정 — 사람이 해야 함

`scripts/fixtures/ai/evaluation_manifest.json`의 `final` 세트는 비어 있고 `tuning`에는 하네스 점검용 합성 사례만 있다. 임의로 만들어 채우지 않는다.

**자료**
- 공개 저장소(license 확인)의 PR 또는 사용 허락을 기록한 자료만 쓴다. 고정 `base_sha`/`head_sha`(40자)와 `selected_paths`를 적는다.
- `tuning`(프롬프트·검증기를 조정하며 쓴다)과 `final`(조정이 끝난 뒤 한 번만)을 **저장소·SHA가 겹치지 않게** 나눈다(`--validate-manifest`가 검사).
- 결함이 있는 사례와 결함이 없는 대조 사례(`negative_control: true`)를 섞는다. 언어·변경 크기·변경 유형(버그 수정·리팩터·기능 추가)을 다양하게 한다. 한 사례의 선택 파일은 입력 한도(24,000토큰·파일 50개) 안이어야 한다.

**정답 붙이기**
- 사례마다 `truths`에 `{path, side, start_line, end_line, category, note}`를 적는다. 결함의 근거는 PR 이후의 수정 커밋·이슈·리뷰 코멘트처럼 **모델이 보지 못하는 외부 증거**로 확인한다. 가능하면 두 사람이 따로 붙이고 불일치는 합의한다(`labeled_by`에 기록).
- 위치는 diff의 실제 줄 번호(LEFT=변경 전, RIGHT=변경 후)다. 같은 결함은 한 항목으로 적는다.

**합격 기준(`criteria`) — 실행 전에 합의**: 평가자가 숫자를 정한다. 정할 때 고려할 값은 다음과 같다(제안이 아니라 목록).
- `min_recall`: 알려진 결함 중 사람이 확인한 발견 비율의 하한.
- `min_precision`: 사람이 판정한 지적 중 유효 비율의 하한.
- `max_cost_per_case_usd`: 사례당 비용 상한(불명 호출 상한 포함).
- 별도로 결함 없는 대조 사례에서 나온 지적 수와 오탐이 허용 범위인지 사람이 판단한다(도구는 수치만 보고).
- 기준은 `tuning` 결과를 보고 정하지 말고 **`final`을 보기 전에** 확정해 manifest에 기록한다.

## ④ 모델 평가

1. `--check-release`가 비어 있고 ②가 통과했는지 확인한다.
2. `tuning`을 실행하고 지적을 판정하고 `--rescore`로 결과를 본 뒤 프롬프트·검증기를 조정한다(변경하면 프롬프트 digest·profile 버전이 바뀐다). 반복 가능.
3. 조정이 끝나면 `final`을 **한 번** 실행한다. 비용 예산을 사례 수 × 예상 상한보다 넉넉하게 별도로 잡는다.
4. 지적을 모두 판정하고 `--rescore`로 최종 채점한다. 판정이 `hold`면 보류 사유를 해소한다(미판정 판정, 불명 호출 비용 확인 등).
5. 보고서에 결과·비용·coverage·유효 지적·오탐·보류·언어·범위 한계를 적고 `AI_MVP_ACCEPTANCE.md`의 §5를 갱신한다.

비용은 평가자의 API key 계정에 청구된다. 제공자 콘솔의 사용 내역과 보고서의 정산 비용이 다르면(특히 불명 호출) 원인을 확인한 뒤 판정한다.
