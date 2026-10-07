# AI 리뷰 화면 설계 (S9)

대상: `frontend/pages/ai-review.html` 한 화면. 백엔드 계약은 `docs/AI_INTERFACE_CONTRACT.md`(v1.4)를 따른다. 이 화면은 서비스 내부 함수가 아니라 HTTP API만 쓴다.

## 1. 원칙

1. **주소가 유일한 기준이다.** `?repo=<id>&pr=<번호>&input=<uuid>&job=<uuid>`. localStorage·기본 repo=1·마지막 작업 기억을 쓰지 않는다. `repo`가 없으면 "저장소를 먼저 선택하세요" 화면만 보인다.
2. **저장소·작업이 섞이지 않는다.** 화면 이동마다 세대 번호(navigation guard)를 올리고, 이전 세대의 응답·폴링·타이머는 버린다(요청은 abort). 응답의 `repository_id`가 주소의 `repo`와 다르면 그리지 않는다. 저장소가 바뀌는 이동은 전체 새로고침이라 메모리 상태가 남지 않는다.
3. **화면은 서버 상태를 대신 판단하지 않는다.** 허용·만료·권한은 서버 응답(오류 코드)으로만 안내한다. 버튼 비활성은 안내일 뿐이고 서버가 다시 막는다.
4. **모든 동적 문자열은 텍스트로만 넣는다.** DOM은 `createElement` + `textContent`로 만들고 `innerHTML`에 데이터를 넣지 않는다. 외부 URL·이미지·링크를 데이터에서 만들지 않는다(내부 `?repo=` 링크만).
5. **"문제 없음"이라고 말하지 않는다.** 지적 0건·테스트 미실행·사람 미검증은 오류 없음/통과로 표시하지 않는다. 결과에는 항상 분석 범위(선택 M/PR 전체 N, 분석 K)와 사람 검증 수를 같이 보인다.
6. **비밀은 화면에 남기지 않는다.** API key 입력칸은 제출 즉시 비우고, 응답은 마스킹 힌트만 쓴다. key는 URL·저장소·콘솔 로그에 두지 않는다.

## 2. 화면 구성과 흐름

```
[A 키·모델] ─(설정됨)→ [B PR 선택] → [C 파일 선택] → [D 미리보기·전송 동의] → [E 실행] → [F 결과·피드백]
        ↑__________________________(CREDENTIAL_* / 키 삭제)_______________________________|
```

상단에는 항상 **저장소 이름·고정 범위 요약**(저장소 `owner/name`, PR 번호, base/head 짧은 SHA)과 단계 표시가 있다. 단계는 주소에서 결정한다.

| 주소 | 보이는 단계 |
|---|---|
| `repo` 없음 | 저장소 선택 안내 |
| `repo` | A(키 상태 요약) + B(PR 목록) + 최근 작업 목록 |
| `repo&pr` | C(파일 선택) |
| `repo&input` | D(미리보기·동의). `&retry=<job>`이면 재실행 동의 |
| `repo&job` | E(진행) 또는 F(결과). 종료 상태에 따라 결정 |

### A. BYOK 키 관리·제공자·모델 선택 (접이식 패널, 접수 불가 시 자동 펼침)
- 제공자 목록(`providers`)에서 **제공자·모델을 고른다**: 모델마다 가격(입력/출력 $/1M 토큰), 입력·출력 한도, 서버 허용 여부(`allowed=false`는 선택 불가, 이유 표시).
- key 등록·교체: 입력칸(`type=password`, `autocomplete=off`)에 key만 입력, 모델 선택, "확인 후 저장"(제공자 확인은 코드를 보내지 않음). 같은 key 재입력은 "변경 없음".
- 등록된 key: 마스킹 힌트·버전·확인 시각·**진행 중 작업 수**. 기본 모델 변경은 key 재입력 없이(`PATCH`). 삭제는 확인창에서 "진행 중 N개 작업이 취소됩니다"를 먼저 보여 준다.
- 서버가 외부 전송을 끄면(`external_transfer_allowed=false`) 등록·실행 버튼을 막고 이유를 보인다.
- 사용량: 이번 달 지출/한도, 불명 예약 수, 일일 요청 사용량.

### B. PR 선택
- 상태 탭(열림/닫힘/병합) + 목록(번호·제목·작성자·base←head·초안·포크·갱신 시각). "더 보기"는 서버 cursor.
- PR을 고르면 `?repo&pr=`로 이동. 목록은 현재 GitHub 상태이므로 새로고침하면 다시 읽는다.

### C. 파일 선택
- PR 전체 파일을 페이지 단위로 불러와 체크박스로 선택(경로 검색, 현재 목록 전체 선택/해제).
- **고정될 SHA 표시**: base/head 짧은 SHA(전체는 툴팁). 목록의 SHA가 페이지마다 달라지면 "PR이 바뀌었습니다"로 중단하고 새로고침을 안내한다.
- **범위 표시**: "선택 M개 / PR 전체 N개". 목록이 부분적(`partial`)이거나 알 수 없으면(`unknown`) 분모를 "알 수 없음"으로 쓰고 이유를 보인다.
- "미리보기 만들기" → `POST /inputs`(화면이 본 SHA를 `expected_*`로, 같은 의도는 같은 Idempotency-Key). `PR_CHANGED`는 새 SHA로 다시 선택하게 안내한다.

### D. 미리보기·전송 동의
- 준비 중이면 `GET /inputs/{id}`를 폴링(단계 표시만, 백분율 없음).
- **고정 SHA**(base·head·merge-base 전체, 복사 가능)·PR 상태·제목·본문(AI로 가는 PR 설명 포함 여부).
- **전송 범위 표**: 파일별 상태(`read`/`excluded`/`failed`), **제외 사유**(비밀 의심·바이너리·생성 파일·너무 큼·링크/서브모듈·가져오기 실패 등 한국어 라벨), 바이트, 변경 위치(hunk). 파일을 열면 실제 전송될 diff(줄 번호·LEFT/RIGHT)를 본다.
- **coverage 3분모**: 목록(PR 전체) / 선택 코드 확보 / 분석(실행 전에는 "아직 분석 전").
- **예상 비용**: 입력 토큰 추정, 모델, 한도 대비, **예상 비용 상한**(입력 추정+최대 출력). 추정은 로컬 보수적 값이며 실행 직전 제공자 토큰 수로 다시 확인한다고 명시. 한도 초과(`within_limit=false`)면 실행 불가.
- **전송 동의**: "다음 N개 파일의 코드가 {제공자}/{모델}로 전송됩니다 · key {힌트} v{버전} · 정책 {버전}" 체크박스에 동의해야 "요약 생성"/"리뷰 생성"(독립 실행) 버튼이 켜진다. 모델 변경은 이 화면에서 `PATCH` 후 미리보기를 다시 읽는다.
- `reviewable=false`(읽은 파일 0개)면 버튼을 켜지 않고 이유를 보인다. 유효기간(`valid_until`)이 지나면 "새 입력 필요".
- 동의 본문은 미리보기가 준 값(`manifest_hash`·`profile`·`credential_version`·`policy_version`)을 그대로 보낸다. `PREVIEW_CHANGED`면 미리보기를 다시 읽고 다시 동의를 요구한다.
- 입력 삭제(원문 삭제·연결 작업 취소)는 확인 후.

### E. 실행(진행)
- 상태·단계(`queued`/`input_preparation`/`provider_call`/`output_validation`)와 경과 시간. 모델 진행률은 만들지 않는다. `retry_wait`는 "다음 시도까지 N초".
- 취소 버튼: 대기 중이면 즉시, 실행 중이면 "취소 접수됨(진행 중인 호출은 끝날 수 있고 비용이 발생할 수 있음)".
- 폴링: 직렬 요청, 숨긴 탭 감속, 화면 이탈 시 abort, 429 `Retry-After`·5xx 지수 backoff, 종료 상태에서 멈춤.

### F. 결과·피드백
- 머리: 종류(요약/리뷰), 모델, 생성 시각, **분석 기준 SHA**(결과가 만들어진 입력의 SHA). 현재 PR head가 다르면 **"이전 SHA 결과"** 경고(현재 head 병기).
- **분석 범위 패널**: "PR 전체 N개 · 선택 M개 · 분석 K개", 제외·누락 사유 목록.
- **검증 배지**: "구조 검증 통과(내용의 정확성을 뜻하지 않음)", "사람 검증 n/전체: 유효·오탐·불확실·미검증 개수".
- 리뷰: 지적 카드(제목·분류·검토 순서·위치 `경로:줄 (LEFT/RIGHT)`·조건·영향·확인 방법·제안·한계·인용). **근거 코드 보기**는 입력의 파일 줄을 불러와 해당 줄 범위를 강조(원문 보관 기간이 지났으면 저장된 인용만 보이고 이유를 설명). 피드백(유효/오탐/불확실 + 메모 ≤2000자)은 `expected_version`으로 저장하고 충돌이면 최신 내용을 다시 읽어 알린다.
- **지적 0건**: "분석한 범위(선택 M/…)에서 모델이 지적을 보고하지 않았습니다. 오류가 없다는 뜻이 아닙니다. 모델 결과는 사람이 검증하기 전입니다." 문구에 "오류 없음/문제 없음"을 쓰지 않는다.
- 요약: 개요(코드에서 관찰), 작성자 의도(PR 설명 기반이라고 구분), 파일별 변경, 알 수 없는 것. 근거 파일 링크.
- 결과 삭제(확인), 다시 실행(동의 단계로 이동, 새 generation).

## 3. 상태·오류·재실행 안내

| 상황 | 신호 | 화면 |
|---|---|---|
| 로그인 필요 | 401 `AUTH_REQUIRED`/`REAUTH_REQUIRED` | 로그인·GitHub 재인증 안내(진행 중 입력은 유지되지 않음을 알림) |
| 기능 꺼짐 | 404 (AI 비활성) | "이 서버에서는 AI 기능을 쓸 수 없습니다" |
| 권한 상실·남의 자료·삭제됨 | 404 | "열 수 없습니다: 접근 권한이 없거나 삭제됐습니다"(구분하지 않음), 작업 목록으로 |
| 입력 만료/삭제 | 410 `INPUT_EXPIRED` | "실행 유효기간이 지났거나 원문이 삭제됐습니다", 새 입력 만들기 |
| 결과 만료 | 410 `RESULT_EXPIRED` | "결과 보관 기간이 지났습니다" |
| 동의 불일치 | 409 `PREVIEW_CHANGED` | 미리보기 새로고침 + 재동의 |
| PR 변경 | 409 `PR_CHANGED` | 새 SHA로 파일 다시 선택 |
| key 없음/무효 | 422 `CREDENTIAL_REQUIRED`/`CREDENTIAL_INVALID` | A 패널로 이동 |
| 한도 | 429 `USER_LIMIT`/`BUDGET_LIMIT`/`UPSTREAM_RATE_LIMIT` | 사용량 링크, `Retry-After` 안내 |
| 제공자 장애 | 503, 작업 `PROVIDER_UNAVAILABLE` | 일시 장애 안내(자동 재시도 상태 표시) |
| 취소됨 | `cancelled` | "취소됨", 비용은 호출이 시작됐다면 발생했을 수 있음 |
| **결과 불명** | `failed` + `OUTCOME_UNCERTAIN`/`PROVIDER_TIMEOUT`, `outcome_uncertain=true` | "제공자가 요청을 처리했는지 알 수 없습니다. **비용이 이미 발생했을 수 있습니다.** 자동으로 다시 실행하지 않습니다." + [다시 실행(동의 다시 받음)] |
| **결과 저장 실패** | `RESULT_LOST`/`RESULT_STORAGE_FAILED` | "호출은 처리·정산됐지만 결과를 저장하지 못했습니다. 다시 실행하면 비용이 다시 듭니다." + 재실행 |
| key 삭제됨 | `CREDENTIAL_UNAVAILABLE` | key 재등록 안내 |
| 출력 검증 실패 | `OUTPUT_VALIDATION_FAILED` 등 | "모델 출력이 검증을 통과하지 못해 저장하지 않았습니다(호출 비용은 발생)" |

재실행은 항상 사용자 클릭이며 `POST /jobs/{id}/retry`(동의 재확인)다. 화면이 자동으로 재요청하지 않는다. 같은 클릭의 중복(더블클릭·재전송)은 같은 `Idempotency-Key`로 한 번만 접수된다.

## 4. 화면별 API 대응

| 화면·동작 | API |
|---|---|
| 로그인 확인·CSRF | `GET /api/auth/me`, `GET /api/auth/csrf` |
| 저장소 이름 | `GET /repositories/{id}` |
| A 제공자·모델 목록 | `GET /ai/v1/providers` |
| A key 목록·등록·교체 | `GET /ai/v1/credentials`, `PUT /ai/v1/credentials/{provider}` |
| A 기본 모델 변경 | `PATCH /ai/v1/credentials/{provider}` |
| A key 삭제 | `DELETE /ai/v1/credentials/{provider}` |
| A 사용량 | `GET /ai/v1/usage` |
| B PR 목록 | `GET /ai/v1/pulls?repository_id&state&cursor` |
| C 파일 목록·현재 head | `GET /ai/v1/pulls/{n}/files?repository_id&cursor` |
| C 입력 만들기 | `POST /ai/v1/inputs` (+Idempotency-Key) |
| D 미리보기·폴링 | `GET /ai/v1/inputs/{id}` |
| D 전송될 diff | `GET /ai/v1/inputs/{id}/files/{file_id}` |
| D 입력 삭제 | `DELETE /ai/v1/inputs/{id}` |
| D 생성 접수 | `POST /ai/v1/jobs` (+Idempotency-Key) |
| D 재실행 | `POST /ai/v1/jobs/{id}/retry` (+Idempotency-Key) |
| E 진행·취소 | `GET /ai/v1/jobs/{id}`, `POST /ai/v1/jobs/{id}/cancel` |
| 작업 목록 | `GET /ai/v1/jobs?repository_id&cursor` |
| F 결과 | `GET /ai/v1/results/{id}` |
| F 지적 | `GET /ai/v1/results/{id}/findings?cursor` |
| F 근거 코드 | `GET /ai/v1/inputs/{input_id}/files/{source_file_id}?side&start_line&end_line` |
| F 피드백 | `PUT /ai/v1/findings/{id}/feedback` |
| F 삭제 | `DELETE /ai/v1/jobs/{id}` |

## 5. 파일

수정: `frontend/pages/components/navbar.html`(AI 리뷰 메뉴), `frontend/js/navbar-loader.js`(`goAiReview`: 주소의 repo가 있을 때만 그 저장소로), `docs/VISUALIZATION_HANDOFF.md`(AI 상태·coverage·오류·원본 코드 위치 계약), `.github/workflows/backend-validation.yml`(새 테스트), 백엔드 `app/api/ai/pulls.py`(신규)·`credentials.py`·`schemas/ai.py`(계약 1.4).

신규: `frontend/pages/ai-review.html`, `frontend/js/ai-state.js`(순수: 주소·상태·문구·오류 매핑), `ai-api.js`(CSRF·멱등 key·오류 정규화·abort), `ai-poll.js`(폴링), `ai-review.js`(DOM), `frontend/css/ai-review.css`, `scripts/test_ai_frontend.cjs`, `scripts/test_ai_pulls_api.py`, `scripts/ai_ui_mock_server.py`(브라우저 검증용 모의 서버).

## 6. 테스트

- **순수·node(`test_ai_frontend.cjs`)**: 주소 파싱/생성(repo 없음·잘못된 값·repo 전환 시 job/input 폐기), 오류 코드→안내·재실행 가능 여부, 상태→단계 표시, coverage·분모 문구, 지적 0건·미검증 문구(금지어 검사), 비용·SHA 포맷, 동의 본문 구성, 멱등 key 재사용/갱신, 세대 guard(늦은 응답 폐기), ai-api(CSRF 재발급·오류 정규화·HTML/깨진 JSON·네트워크 오류·abort), ai-poll(직렬·backoff·429 Retry-After·숨긴 탭·stop·종료 상태).
- **백엔드(`test_ai_pulls_api.py`)**: PR·파일 목록, 권한, GitHub 오류 매핑, 기본 모델 변경.
- **브라우저(모의 서버)**: 미리보기→동의→생성→근거 코드→피드백→재조회, 401/404/429/503, 만료·취소·결과 불명·재실행, 지적 0건 문구, HTML/script·외부 이미지가 든 제목·본문·지적이 텍스트로만 보임, 이전 SHA 경고, 새로고침·저장소 전환 시 혼입 없음, 좁은 화면.

## 7. 완료 조건

1. 다른 저장소·이전 작업의 데이터가 새로고침·주소 변경·늦은 응답으로 섞이지 않는다.
2. 선택 범위와 PR 전체 범위, 고정 SHA, 제외 사유, 예상 비용, 전송 대상(제공자·모델·key 힌트)이 동의 전에 보인다.
3. 동의 없이 접수되지 않고, 재실행·불명 호출은 사용자 클릭으로만 시작된다.
4. 지적 0건·사람 미검증·테스트 미실행이 "오류 없음"으로 표시되지 않는다.
5. 데이터에 든 HTML·script·외부 이미지가 실행·로드되지 않는다.
6. 운영 DB 스냅샷 검증(`verify_snapshot_integration.py`)은 **출시 전 필수 확인**으로 남긴다(S9에서는 미확인).
