# 현재 제공자·profile·로컬 실행 안내

검토일: 2026-10-23 · 코드 기준: `a8351f0` · [문서 안내](../index.md)

확인: 지원 구현은 [registry](../../app/services/ai/providers/registry.py)와 [adapter 공통 계약](../../app/services/ai/providers/base.py)을 기준으로 읽었다. 모델명·가격은 Repository에 등록된 값이다. 제공자의 현재 실제 판매·가격·계정 정책을 외부 검증한 표가 아니다.

## 현재 노출되는 선택지

| 제공자 | profile | 인증·실행 위치 | 입력/출력 상한 | timeout |
|---|---|---|---|---|
| Anthropic | `claude-opus-5-5-v4`, `claude-sonnet-5-5-v4` | 사용자 BYOK·클라우드 | 128,000 / 16,384 tokens | 180초 |
| OpenAI | `gpt-5.4-v4` | 사용자 BYOK·클라우드 | 128,000 / 16,384 | 180초 |
| Gemini | `gemini-3.5-flash-lite-v4` | 사용자 BYOK·클라우드 | 128,000 / 16,384 | 180초 |
| Ollama | `ollama-local-v1` | key 없음·같은 PC | 24,000 / 4,000, context 32,768 | 90초 |
| LM Studio | `lmstudio-local-v1` | key 없음·같은 PC | 기본 4,000 / 4,000, context 8,192 | 기본 180초 |
| KoboldCpp | `koboldcpp-local-v1` | key 없음·같은 PC | 기본 4,000 / 4,000, context 8,192 | 기본 180초 |

`AI_ALLOWED_PROFILES`와 런타임 enabled 설정이 실제 선택 허용을 제한한다. 클라우드 v1~v3는 기존 동의·fingerprint를 위한 내부 profile로 남지만 `supported_options()`는 v4를 노출한다. 미선택 서버 기본값 `AI_PROVIDER_PROFILE=claude-opus-5-5-v1`과 화면 노출 최신 profile은 다르다. 한도가 낮다고 진행 중 작업의 profile을 임의로 치환하지 않는다.

클라우드 v1은 24,000/4,000, v2는 64,000/4,000, v3는 64,000/4,000과 확장 출력 schema를 사용한다. v4는 v3에서 token·timeout을 확장한다. 모든 입력에 파일 50개·2,000,000 bytes 기본 제한도 별도로 적용한다.

원시 응답 파싱 상한은 `MAX_RAW_OUTPUT_BYTES=262_144`(256KiB)이다. 출력 token 상한 및 개별 문자열 한도와 다른 제한이다. 개별 필드는 [출력 한도 정의](../../app/schemas/ai_output_limits.py)를 따른다. [기존 제공자 문서](providers.md)의 v3·64KiB·출력 4,000 일반 설명은 과거 profile 기록과 구분해야 한다.

## 로컬 런타임

API·worker·런타임은 같은 PC의 호스트 프로세스로 실행한다. worker에서 보는 loopback이 기준이다. 브라우저가 다른 PC에서 열렸다고 그 PC 모델에 연결되는 기능은 아니다.

| 런타임 | 설정 prefix·기본 주소 | 모델 고정 |
|---|---|---|
| Ollama | `AI_OLLAMA_*`, `http://127.0.0.1:11434` | 설치 모델 이름·전체 digest 확인 |
| LM Studio | `AI_LMSTUDIO_*`, `http://127.0.0.1:1234` | `/v1/models`의 ID 확인, 파일 해시는 증명하지 않음 |
| KoboldCpp | `AI_KOBOLDCPP_*`, `http://127.0.0.1:5001` | `/v1/models`의 ID 확인, 파일 해시는 증명하지 않음 |

설치·모델 다운로드는 앱이 대신하지 않는다. 관리자가 `.env.example`의 enabled·model·endpoint·한도를 설정한다. LM Studio/KoboldCpp URL에 `/v1`을 붙이지 않는다. 실제 context와 입력·출력 합계·template 여유를 맞춘다. 허용 주소·redirect·metadata 검사는 adapter와 설정 검증을 따른다. 원격 endpoint나 `host.docker.internal` 연결로 확장한 기능은 현재 지원으로 선언하지 않는다.

로컬은 제공자 API 비용 0 기록을 남기되 연산 비용·시간이 0인 것은 아니다. 외부 전송 비활성·클라우드 월 예산 소진과 로컬 실행을 구분한다. 자동 provider fallback은 없다.

## 검증·유효성

실제 smoke의 명시 실행 도구는 [smoke_ai_live](../../scripts/smoke_ai_live.py), 현재 절차는 [AI 평가](../validation/protocols/ai-evaluation.md)다. 선택한 profile의 사용량·schema 수용·비용·속도를 따로 기록한다. 이번 문서 정리는 유료 호출·모델 다운로드·런타임 시작을 수행하지 않았다.

추정: profile 변경의 이유·승인·검증 보고를 [D03](../decisions/index.md#d03)에 연결하는 것이 적절하다.

미확정: 외부 제공자의 현재 가용 모델·실제 청구·계정 데이터 정책과 로컬 모델별 의미 품질은 별도 확인 대상이다.
