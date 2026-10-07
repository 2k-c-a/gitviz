# AI 제공자 지원 (2026-10-07)

## 지원 범위

| 제공자 | profile | 모델 | 인증 | API 요금 / 100만 토큰 |
|---|---|---|---|---|
| Anthropic | 기존 Claude profiles | 기존 Opus / Sonnet | 사용자 BYOK | 기존 단가 유지 |
| OpenAI | `gpt-5.4-v1` | `gpt-5.4-2026-03-05` | 사용자 BYOK | 입력 $2.50 / 출력 $15 |
| Gemini | `gemini-3.5-flash-lite-v1` | `gemini-3.5-flash-lite` | Google AI Studio 사용자 key | 입력 $0.30 / 출력 $2.50 (유료 표준 등급) |
| Ollama | `ollama-local-v1` | 관리자가 지정한 설치 모델 | key 없음 | 제공자 API 요금 $0, 전력·하드웨어 비용 별도 |

클라우드 단가는 등록된 profile의 추정치이며 할인·무료 quota를 반영하지 않는다. Gemini는 생각 토큰도 출력 비용에 포함한다. 무료 등급의 입력·출력은 Google 제품 개선에 사용될 수 있다. 실제 계정의 데이터 처리 정책을 확인한다.

모든 제공자가 기존 summary/review 프롬프트, JSON·파일·side·줄·인용 검증, 사람 미검증 상태, 암호화 보관, 취소·멱등·호출 불명 복구를 사용한다. 제공자와 모델 사이의 자동 fallback은 없다. OpenAI는 Responses API를 사용하며 `store=false`다. 키는 헤더에만 전달하고 공식 주소를 고정한다. SDK를 추가하지 않고 이미 있는 `httpx2`로 단일 REST 요청을 보낸다. redirect·환경 proxy·SDK 자동 재시도는 사용하지 않는다.

## 화면과 API

AI 리뷰 화면의 제공자 선택에서 키를 등록하고 모델을 선택한다. 로컬 선택에는 key 입력이 없다. 미리보기에서 다른 제공자·모델을 고르면 다시 불러오고 동의를 해제한다.

- 기존 credentials API의 `{provider}`가 `anthropic`, `openai`, `gemini`를 지원한다. 각 사용자·제공자별로 키를 암호화하고 버전을 고정한다. `ollama`에 key 등록은 거부한다.
- `GET /api/ai/v1/inputs/{id}?profile_id=...`로 선택 모델의 한도·요금·키 버전을 확인한다. 생략하면 기존 기본 모델 선택 규칙을 유지한다. 지원·허용 목록 밖의 값은 거부한다.
- 모델 목록과 transfer에 `requires_api_key`, `execution_location`을 추가했다.
- transfer에 `profile_fingerprint`, `model_digest`를 추가했다. 동의의 `profile_fingerprint`는 클라우드의 기존 클라이언트에는 선택 항목이며, 로컬 실행에는 필수다.
- 로컬 동의의 `credential_version`은 null이다. 클라우드는 여전히 현재 key 버전 일치가 필수다.
- 기존 nullable `credential_version_id`와 불변 provider/model/profile fingerprint 컬럼을 사용하므로 DB migration은 없다. 기존 Anthropic fingerprint는 그대로 유지한다.

클라우드의 외부 전송 허용·공개 저장소 정책은 유지한다. 로컬은 `AI_ALLOW_EXTERNAL_TRANSFER=0`에서도 실행할 수 있고 접근 권한이 확인된 비공개 저장소도 허용한다. 로그인·CSRF·소유자·매번 저장소 접근 확인·입력 만료·정책 검사는 동일하다.

로컬도 $0 예약·정산 기록을 남긴다. worker가 결과 저장 전에 중단됐을 때 이미 수행한 연산을 자동으로 다시 호출하지 않기 위해서다. 일일 요청 한도와 큐 제한은 유지한다. Ollama에는 사전 토큰 계산 API가 없으므로 UTF-8 바이트 기반 보수적 상한을 사용한다. 실제 usage가 없더라도 로컬 제공자 API 요금은 $0이다.

클라우드 월 예산을 이미 소진했어도 $0 로컬 실행은 허용한다. 모델 metadata 조회가 느려져도 완료 후 저장소 접근·입력 삭제·취소를 다시 확인하고 그 뒤에 코드를 보낸다. 새 선택 필드가 없는 기존 클라이언트의 멱등 키 해시는 유지한다.

## 같은 PC의 Ollama 설정

GitViz API·dispatcher·worker·Ollama를 같은 PC에서 호스트 프로세스로 실행한다. worker의 loopback 주소가 기준이다. 웹 GitViz에서 다른 사용자 PC를 연결하는 것은 후속 과제다.

1. Ollama를 설치하고 사용할 모델을 **직접** 내려받는다. 앱은 모델 다운로드나 외부 fallback을 수행하지 않는다.
2. Ollama를 `OLLAMA_NO_CLOUD=1`로 실행한다. `/api/tags`의 모델 이름과 전체 digest를 복사한다. 예:

   ```powershell
   $env:OLLAMA_NO_CLOUD='1'
   ollama serve
   # 다른 터미널에서 설치된 모델 확인
   Invoke-RestMethod http://127.0.0.1:11434/api/tags
   ```

3. 기존 AI·DB·Redis·암호화 설정에 다음 값을 추가한다. API와 worker에 같은 값을 적용한다.

   ```dotenv
   AI_OLLAMA_ENABLED=1
   AI_OLLAMA_URL=http://127.0.0.1:11434
   AI_OLLAMA_MODEL=qwen2.5-coder:7b
   AI_OLLAMA_MODEL_DIGEST=<api/tags에 표시된 전체 digest>
   # 선택: 로컬을 서버 기본 모델로 사용
   AI_PROVIDER_PROFILE=ollama-local-v1
   # 선택: 로컬만 허용
   AI_ALLOWED_PROFILES=ollama-local-v1
   ```

4. 기존 실행 명령으로 API·dispatcher·worker를 시작한다. Windows Celery는 `--pool=solo`를 사용한다. 모델은 context 32,768, 입력 상한 24,000, 출력 4,000, timeout 90초를 지원해야 한다. 작은 모델의 구조화 출력 품질과 PC 메모리·속도는 실제 smoke로 확인한다.

운영 Ollama 주소는 HTTP loopback과 명시적 포트만 허용한다. 사용자 URL, 원격 IP, 사용자 정보·경로·query·fragment·redirect는 거부한다. `/api/tags` digest 및 `/api/show`의 remote 모델 여부를 코드 전송 전에 확인한다. 모델 이름이 같아도 digest·주소 설정이 바뀌면 기존 동의·작업으로 호출하지 않는다.

기존 Docker compose worker 안의 `127.0.0.1`은 호스트 PC가 아니다. 이번 버전은 Windows 호스트 Ollama와 컨테이너 worker 연결을 지원하지 않으며 `host.docker.internal`도 허용하지 않는다. 이 경우 worker를 호스트에서 실행한다. 컨테이너 연결은 별도 네트워크·주소 검증 후 확장한다.

## 실제 smoke

기존 smoke에 `--profile`을 지정한다. 실제 호출은 아래 명령을 운영자가 명시적으로 실행할 때만 발생한다.

```powershell
# cloud: GITVIZ_SMOKE_API_KEY에 해당 제공자의 본인 key 설정
python scripts/smoke_ai_live.py provider --profile gpt-5.4-v1 --live --budget 0.50
python scripts/smoke_ai_live.py provider --profile gemini-3.5-flash-lite-v1 --live --budget 0.50
# local: 위 Ollama 설정 필요, key 불필요
python scripts/smoke_ai_live.py provider --profile ollama-local-v1 --live --budget 0
```

이번 변경의 자동 검증은 HTTP 대역과 격리 PostgreSQL을 사용한다. 실제 OpenAI·Gemini 계정 호출과 설치 Ollama 모델의 생성 품질·속도는 미확인이다. 기존 모델 평가 정답·합격 기준과 운영 스냅샷 검증의 미확인 상태도 유지한다.

## 자동 검증 기록

- 새 HTTP adapter 테스트 14개, PostgreSQL 기반 제공자·로컬 작업 테스트 8개 통과. 키 없는 실행, provider 별 BYOK, digest 변경, 취소 경쟁, $0 정산, 소진된 클라우드 예산과 로컬 실행 분리를 확인했다.
- 기존 계약·보안·스키마·Anthropic·키 관리·입력·생성·API·평가·smoke·운영·백엔드 회귀를 포함해 Python 21개 묶음 484개 테스트 통과.
- 실제 PostgreSQL·Redis·별도 Celery worker의 큐 테스트 7개와 HTTP 통합 수용 테스트 16개가 위 집계에 포함된다. 실제 외부 제공자는 대역이다.
- Node 화면 모듈 테스트 32개 통과. 모의 서버의 headless 브라우저에서 네 제공자 선택, OpenAI·Gemini key 등록·입력 비우기, 로컬 동의→결과, 375px 가로 넘침·JS 오류를 확인했다.

공식 계약·단가 근거: [OpenAI GPT-5.4](https://developers.openai.com/api/docs/models/gpt-5.4), [Responses 토큰 계산](https://developers.openai.com/api/reference/resources/responses/subresources/input_tokens/methods/count), [Gemini 구조화 출력](https://ai.google.dev/gemini-api/docs/structured-output), [Gemini 가격·데이터 처리](https://ai.google.dev/gemini-api/docs/pricing), [Ollama chat](https://docs.ollama.com/api/chat), [Ollama FAQ](https://docs.ollama.com/faq).
