# 기술 선택과 기능 보존 전략

목적: 현재 스택과 Windows 통합 범위를 기준으로 하나의 권장안을 제시한다. 근거: [01 O01~O18](01-repository-analysis.md). 관련: E01~E10, N01~N08, P01~P08. ADR01~ADR09는 설계 권고이며 설치·성능 측정 결과가 아니다. [14 미결 조건](14-decisions-and-open-questions.md), [13 실행 순서](13-implementation-plan.md)를 함께 읽는다.

## 후보 비교

| 관점 | Electron + 작은 Python agent | Tauri + Python sidecar | WebView2 네이티브 Windows 호스트 |
|---|---|---|---|
| 기존 UI/Python 재사용 | HTML/JS·Python 유지 | 동일하게 가능 | WebView UI 가능·Python 별도 프로세스 필요 |
| OS·파일 통합 | Node/Electron host API, 좁은 브리지 | Rust command/capability | Windows API 직접 사용·브리지 신규 |
| 보안 | renderer 격리·IPC 검증·CSP 필수 | capability·command scope 검증 필수 | WebMessage·navigation·COM/host 경계 검증 필수 |
| 성능·메모리 특성 | Chromium/Node 동봉 비용 | 시스템 WebView 활용, Python 비용은 별도 | WebView2/runtime·호스트 비용 | 
| 설치·업데이트 | Forge와 Squirrel 후보, 서명·feed 필요 | target별 sidecar·서명·updater 설정 | 설치/업데이트 체계 추가 설계 |
| 프로세스 격리 | renderer sandbox, agent 별도. agent 자체는 사용자 권한 | WebView/sidecar 분리. capability가 sidecar OS sandbox는 아님 | 호스트/agent 별도·권한 최소화 필요 |
| 배포 복잡성 | JS/TS·Python 두 런타임·Windows package | JS·Rust·Python·WebView 검증 | JS·C#/C++·Python·WebView 검증 |
| 유지보수·팀 적합성 | 현재 JS/Python에서 추가 언어 부담이 비교적 작다는 추정 | Rust 숙련도는 미확인 | 네이티브 숙련도는 미확인 |

숫자 비교는 측정 전이다. 크기·기동 시간·메모리·차트 동등성은 같은 fixture와 clean Windows VM에서 측정해야 한다. 팀 역량·배포 제약 Q01/Q02/Q10이 이 선택을 뒤집을 수 있다.

## ADR01 권장안: Electron + local agent + 기존 서버

기존 UI를 번들 자산으로 제공하고 preload는 operation 단위 API만 노출한다. Electron main은 창·dialog·인증·에디터·프로세스·SecretStore를 소유한다. Python agent는 로컬 프로젝트·Git·검증·제안·리뷰와 SQLite를 관리한다. Python 런타임은 릴리스 패키지에 포함하는 방향이며 사용자의 개발용 Python/venv를 제품 의존성으로 삼지 않는다.

기존 FastAPI·PG·Redis·Celery·GitHub OAuth·원격 분석·원격 AI 작업은 서버에 유지한다. 기존 서버를 그대로 PC에 넣는 방식은 PG·Redis 운영과 OAuth 비밀·프로세스 복구 부담 때문에 선택하지 않는다. 원격 웹페이지 자체에 native bridge를 부여하는 단순 래퍼도 선택하지 않는다.

Electron은 UI 호환성과 JS/Python 경계의 명료성을 우선한 권고다. Tauri는 Python sidecar를 지원하므로 Python 때문에 제외하지 않는다. 다만 이번 범위에서 Rust 계층·WebView 차이 검증을 추가할 명확한 이득이 아직 측정되지 않았다. 네이티브 호스트도 기존 비즈니스 로직을 없애주지 않으며 새 호스트 언어·배포 체계의 비용이 있다.

## ADR02 데이터와 실행 분리

원격 분석은 서버의 권위 있는 immutable snapshot이다. 로컬 Project와 remote Repository는 별도 ID이며 사용자가 명시적으로 연결한다. remote URL 변경 시 연결 확인이 다시 필요하다. 로컬 결과를 remote result처럼 `/shared`에 노출하지 않는다. SQLite의 FK·transaction·hash 검사는 신규 구현·검증이 필요하며 PostgreSQL trigger가 자동 이식된다고 가정하지 않는다.

## ADR03 제한 브리지와 UI transport

1. 기존 UI의 transport 생성 지점에만 어댑터를 추가한다. `createAiClient({fetchImpl})` 주입 경로를 활용하고 분석 client도 동일한 주입 경계를 만든다. AI client에는 기본값이 기존 cookie인 authMode를 추가하여 desktop에서는 renderer의 CSRF 취득/헤더 처리를 생략하고 main의 검증된 bearer transport만 사용한다.
2. 웹 빌드는 지금의 same-origin cookie fetch를 유지한다. 앱은 명시한 path/method의 서버 호출만 main에 요청한다. main은 검증된 endpoint와 계정에 대해서만 인증 정보를 붙인다.

## ADR04 인증

시스템 브라우저에서 기존 서버 OAuth를 수행하고 서버가 발행한 제한된 desktop 세션을 main이 받는 handoff를 신규 설계한다. GitHub client secret·서버 암호화 key는 PC에 복사하지 않는다. 로컬 작업은 Windows 사용자 소유이며 GitHub 로그인 없이 가능하다. cloud local review의 BYOK는 PC에 별도로 등록한다. 서버 key를 export하지 않는다. 자세한 흐름·서버 변경은 [07](07-api-design.md)에 고정한다.

## ADR09 단계 전환과 호환성

별도 desktop feature switch로 신규 경로를 추가하고 기존 웹 기본 경로를 유지한다. P03 서버 인증은 additive endpoint/table로 도입하며 기존 AI v1 DTO·공유 의미를 변경하지 않는다. UI 재사용 시 기존 URL snapshot 기준·coverage·결측·통계 의미를 고정한다. API mismatch는 명시적 호환성 오류로 표시하며 새 필드를 무시해 실행 범위를 넓히지 않는다.

## ADR05 고정 입력·승인·journal

[05 F02/F03](05-functional-specification.md)의 고정 bytes·원본/대상 hash·일회 capability와 [06](06-data-model.md)의 durable journal을 적용한다. 여러 파일을 하나의 파일시스템 transaction으로 표현하지 않는다. partial/recovery_required를 노출하고 제3자 변경은 덮어쓰지 않는다.

## ADR06 AI core 선택 재사용

agent용 AI provider·content policy·line-map·validator를 DB 없는 core adapter로 연결한다. 기존 `generation_service`·`job_service`·`input_store`를 import하지 않는다. 서버 adapter와 agent adapter 모두 같은 fixture로 의미 동등성을 검사한다.
기존 프롬프트/profile는 불변으로 두고 로컬 review/refactor에는 새 버전을 사용한다. 기존 PR 전용 문구·도구 없는 응답 계약과 patch 제안 계약을 혼합하지 않는다.

## ADR07 영향 근거 구분

정적 참조, 과거 공동 변경, AI 추론을 별도 evidence kind로 보존한다. source SHA/hash·도구·지원 범위·시점을 기록하며 공동 변경이나 문자열 검색을 확인된 call graph로 표시하지 않는다.

## ADR08 단계별 승인과 전체 인수

[13](13-implementation-plan.md)의 각 단계는 별도 승인·검증·보고 단위다. 내부 검증판을 전체 출시로 취급하지 않으며 E01~E10·N01~N08·플랫폼/보안 게이트를 충족한 뒤 출시한다.

## ADR 식별자 대응

[14](14-decisions-and-open-questions.md)가 ID의 기준이다. 이전 03의 ADR03은 ADR03(UI/transport)과 ADR06(AI core)으로 분리하고, 이전 03의 ADR05(호환성)는 ADR09로 옮겼다. 14의 ADR05(고정 입력·승인·journal)는 그대로 유지한다. 내용 이동은 새 제품 정책 승인이나 구현 완료를 뜻하지 않는다.

이전 답변의 개발용 전체 Python 서버 동봉 가능성은 이번 Windows 설계에서 권장하지 않는다. 서버 기능을 불필요하게 이동하지 말라는 현재 요구와 기존 PG/Celery 결합을 반영한 구체화다.

## 공식 자료 확인

2026-10-23 확인. 프레임워크 최신 버전 번호·지원 OS는 이 문서에서 고정하지 않고 P00에서 검증 후 lock한다.

- [Electron security](https://www.electronjs.org/docs/latest/tutorial/security): renderer 격리와 IPC·외부 콘텐츠 경계 검토의 근거.
- [Electron updates](https://www.electronjs.org/docs/latest/tutorial/updates): Forge/Squirrel·업데이트 feed 후보의 근거. 배포 채널 확정은 아님.
- [Electron safeStorage](https://www.electronjs.org/docs/latest/api/safe-storage): OS 기반 비밀 저장 어댑터 후보. 선택 버전의 async API·잠금 실패를 P01에서 확인한다.
- [Tauri sidecar](https://v2.tauri.app/develop/sidecar/), [capabilities](https://v2.tauri.app/security/capabilities/): Python 번들 가능성과 WebView별 권한 모델의 근거.
