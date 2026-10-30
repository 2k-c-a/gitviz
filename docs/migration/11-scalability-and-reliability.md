# 신뢰성·운영·확장

목적: 현재 필요한 신뢰성과 향후 확장을 구분한다. 근거: [01 O03/O08/O12/O14/O16](01-repository-analysis.md), [현재 AI 운영](../operations/ai.md). 관련: N01~N08·P03/P06/P07/P08, [06 저장](06-data-model.md), [10 복구](10-edge-cases-and-security.md), [13 단계](13-implementation-plan.md). 동시 사용자·SLO·기업 proxy·telemetry는 미확인/Q10이다.

## 현재 설계에 필요한 한도

- 한 앱 instance·한 agent writer, project당 apply 1개. 기본 validation 1개/project·전체2개, AI provider 호출 전체1개를 제안한다. 별도 부하 측정 후 설정값으로 고정하며 무제한 큐를 두지 않는다.
- Git/검증/모델/IPC 각각 timeout·취소·byte/파일/출력 한도. 초기 review 상한은 기존 정책의 작은 한도를 초과하지 않으며 profile별 context·동의·예산을 다시 검사한다. 서버 profile 숫자를 PC의 메모리 한도로 착각하지 않는다.
- renderer는 프로젝트 전환 시 abort·generation token 검사, 숨긴 화면 polling 감속·terminal 중지·Retry-After. 많은 등록 project에 대해 모든 상세 API를 동시에 호출하지 않는다.
- 파일 트리·diff·출력은 페이지/가상화·chunk로 읽고 repo 전체 source를 메모리에 올리지 않는다. 대형 repo는 분석 범위·시간 제한과 부분/취소 결과를 표시한다.

## 서버 부하·비용

기존 서버 분석 작업·PG·Celery는 계속 공유한다. desktop은 새 client이므로 사용자별 기존 일일 한도·예산·GitHub rate limit를 우회하지 않는다. 앱별 polling interval·동시 요청·ETag 도입 여부는 기존 private/no-store·권한 검사 의미와 충돌하지 않게 검토한다. 지금 private 응답을 장기 캐시하도록 API를 바꾸지 않는다.

desktop 인증 flow 발급/교환은 pre-auth rate limit·TTL cleanup·DB index가 필요하다. 신규 세션과 기존 web session은 별도이며 desktop logout이 web 사용자를 임의 로그아웃시키지 않는다. remote analysis 완료 알림은 알려진 job에만 polling하고 서버의 새로운 push 시스템을 강제하지 않는다.

PC cloud review의 BYOK 비용은 서버 usage에 합산되지 않는다. UI는 원격 서버 예산과 로컬 실행 예산을 별도로 표시한다. local attempt reservation과 uncertain 상태는 재시작 후에도 유지한다. provider가 보고하지 않은 비용은 estimate로 표시하고 무료 로컬 실행도 시스템 리소스 비용까지 0이라고 주장하지 않는다.

## 캐시·계정·오프라인

local source·review·journal은 로컬 데이터이며 remote cache와 다르다. 초기 설계에서는 private remote 자료의 영속 cache를 기본 비활성으로 제안한다. 메모리 cache도 계정·repo·snapshot·권한을 key에 포함하고 로그아웃/권한 상실 시 폐기한다. 공개 원격 이력의 disk cache와 오프라인 제공은 Q03/Q08 승인 후 설계 확장한다.

오프라인에서 가능한 것: 연결된 로컬 폴더의 Git 상태·diff·editor·설치된 test/lint·기존 로컬 결과·PC 모델(설치됨). 불가능한 것은 명확히 unavailable: GitHub 검색/OAuth/현재 접근 검사·원격 분석/AI job·cloud 호출. 코드 업로드·명령 실행의 자동 동기화 큐는 만들지 않는다.

## 버전·데이터·복구

appVersion·agent protocolVersion·localSchemaVersion·server apiCapabilities를 시작 시 확인한다. 현재 AI DTO 1.9의 의미 변경은 별도 D02 결정이며 이 설계에서 번호를 바꾸지 않는다. remote capability가 없으면 해당 기능 제한과 최소 서버 버전을 안내한다. 새 앱이 구 서버에 요청 필드를 임의 축소해 승인 범위를 바꾸지 않는다.

local job과 fs journal은 durable 상태로 복원한다. source snapshot은 immutable, feedback·read flag·설정은 CAS. startup은 running validation을 interrupted, apply를 recovery_required, provider 미확정 attempt를 uncertain으로 정리한다. 재호출 전 서버/로컬 operation 상태를 조회하며 외부 모델 exactly-once를 보장하지 않는다.

DB backup·agent binary·schema range의 대응을 기록한다. 코드 복원과 DB 복원을 혼동하지 않는다. journal 원복은 현재 파일이 앱이 기록한 target과 같고 사용자가 승인한 경우만 가능하다. project root를 삭제하거나 server DB를 자동 내려서 복구하지 않는다.

## 관측·성능 검증·향후 범위

로그는 operation 지연·queue 길이·실패 code·component version·truncation 수만 기본 기록한다. crash report는 opt-in·비식별 검토가 필요하며 자동 telemetry를 이번 범위에 추가하지 않는다. 실제 기동/메모리/큰 repo 응답 SLO는 Q10 확정 후 P09의 동일 fixture·동일 VM 조건으로 측정한다. 측정 전 '빠르다/가볍다/성능 개선'을 출시 문구로 쓰지 않는다.

향후 검토만 하는 것: 원격 snapshot 동기화·push 알림·다중 사용자 local DB·분산 runner·악성 repo OS sandbox·다중 OS·조직 정책 관리. 현재 필요한 것은 single-user SQLite, 제한된 agent, 기존 서버 용량 계측이다. 확장 가능성만으로 새 broker·vector DB·Kubernetes를 도입하지 않는다.
