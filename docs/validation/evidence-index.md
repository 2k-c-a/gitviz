# 과거 검증 증거 연결

검토일: 2026-10-23 · 코드 기준: `a8351f0` · [현재 검증 상태](current-status.md)

확인: 아래 결과는 원문이 보고한 과거 실행이다. 이번에 동일 환경으로 재실행한 결과가 아니다. 숫자가 다른 실행을 합쳐 하나의 통과 수로 만들지 않는다. 보고서가 기준 commit을 명확히 고정하지 않았다면 실행일만으로 추정하지 않는다.

| 실행·자료 | 보고된 결과 | 환경·실제/대역 경계 | 재현 도구·한계 |
|---|---|---|---|
| [초기 검토](../archive/originals/REVIEW_2026-10-02.md) | 계산 9·스냅샷 14 등과 pending 결함 | `05a58db` 기준 검토, 일부 함수 분리 | 현재 결함 목록 아님 |
| [백엔드 개선](../archive/originals/docs/BACKEND_IMPROVEMENTS.md) | 처음 12, 후속 성공 worker 포함 13 | 격리 PG18, 실제 운영·OAuth 제외 | [backend tests](../../scripts/test_backend_improvements.py). 문서 내 실행별 집계 구분 |
| [운영 DB 통합](../archive/originals/INTEGRATION_VALIDATION.md) + [JSON](../archive/originals/INTEGRATION_RESULTS.json) | 32개 검사, HTTP 22개 | 기존 운영 PG read-only·브라우저, OAuth·재수집 제외 | [verify_production_integration](../../scripts/verify_production_integration.py). 과거 데이터·접근 환경 필요 |
| [스냅샷 적용 기록](../archive/originals/SNAPSHOT_AND_ACCESS.md) + [JSON](../archive/originals/SNAPSHOT_INTEGRATION_RESULTS.json) | 53개 검사 기록 | JSON의 `authorization_mode=test_fixture` | [verify_snapshot_integration](../../scripts/verify_snapshot_integration.py). 실제 GitHub 인증 전체 통과 아님 |
| [LIVE 최초 JSON](../archive/originals/docs/LIVE_VALIDATION_INITIAL.json) | job running·snapshot 저장 재현 | 2026-10-02 UTC 실행, Git/REST 실제, 서명 세션 fixture·eager | 수정 전 실패 증거로 보존 |
| [LIVE 최종 보고](../archive/originals/docs/LIVE_VALIDATION.md) + [JSON](../archive/originals/docs/LIVE_VALIDATION_RESULTS.json) | 52개 중 51 통과, samplemod PR 상세 실패 | PG18 전용 DB·실제 Git/REST, OAuth callback·Redis 별도 worker 제외 | [verify_live_repository](../../scripts/verify_live_repository.py). 토큰·네트워크·전용 DB 필요 |
| [PG 정책 검증](../archive/originals/docs/POSTGRES_VALIDATION_2026-10-03.md) | backend 14·정책 12 | PG18 전용 DB, GitHub·수집 대역 | 빈 설치·기존 upgrade·snapshot/HTTP. CI PG16과 별도 |
| [WEB 통합](../archive/originals/docs/WEB_INTEGRATION_2026-10-03.md) | 실제 신규·재분석·화면 이동 성공 | 실제 사용자 OAuth, Redis DB14·별도 worker·PG18 | PR 상세 23/23은 존재율, 모든 페이지 완전성 아님. `.validation/web-evidence/` 증거의 정식 보존 미확정 |
| [초기 AI 기획 검토](../archive/originals/docs/AI_ASSISTANT_PLAN_REVIEW_2026-10-07.md) | 선택 회귀 통과, snapshot 전체 중단 | `0ecbca71bcd2fe5f08400397990c18d16fbf50eb` 당시 코드 | 중단한 검사를 전체 통과로 쓰지 않음 |
| [AI MVP 수용](../archive/originals/docs/AI_MVP_ACCEPTANCE.md) | 통합 16 등, 구조·안전 수용 | 일회용 PG16·Redis7·별도 API/worker/dispatcher, 가짜 provider·GitHub | [verify_ai_mvp_integration](../../scripts/verify_ai_mvp_integration.py). 모델 정확도·실제 인증 제외 |
| [provider 확장 기록](../archive/originals/docs/AI_PROVIDER_SUPPORT.md) | Python 484·Node 32 등 기록 | HTTP 대역·격리 PG·모의 브라우저 | 당시 묶음 집계, 현재 전체 통과·모델별 품질 아님 |

## Git에서 제외된 로컬 스냅샷 자료

확인: `.validation/SNAPSHOT_INTEGRITY_REVIEW.md`와 `.validation/SNAPSHOT_INTEGRITY_RESULTS.json`이 로컬에 존재하며 `git check-ignore` 대상이다. 원문은 2026-10-07 읽기 전용 검증 394개 실패 0, 현재 형식 17개 hash 일치, 기준 SHA 확보 16개 등의 결과를 기록한다. HTTP 183개는 접근 대역이며 실제 인증 사용자 repo13 조회는 미확인으로 구분한다. 구형 snapshot·식별 정보 누락·SHA 미확보 제한도 기록한다.

미확정: 작성 커밋·운영 환경 재현성·정식 보존 여부가 확정되지 않았다. 일반 clone에서 없는 파일에 의존하지 않도록 필수 Markdown 링크로 만들거나 내용을 정식 실행 결과로 복사하지 않았다. 이 증거를 이유로 기존 수용 보고의 출시 전 확인을 완료 처리하지 않는다. [D05](../decisions/index.md#d05)를 참조한다.

## 보존 규칙

추정: 새 실행은 실행 ID, 시각·시간대, commit·dirty 상태, 명령·종료 코드, DB/큐 전용 여부, 외부 호출·대역·eager 여부, fixture·SHA·profile, 실패·중단·제외·한계를 남긴다. 원본 JSON은 해당 보고서와 함께 보존하고 과거 실패를 새 결과로 덮어쓰지 않는다. 인증 토큰·개인 메모·코드 원문·DB 비밀번호를 실행 보고서에 복사하지 않는다.
