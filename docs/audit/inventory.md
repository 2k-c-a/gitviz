# 문서 인벤토리·분류·참조 관계

검토일: 2026-10-23 · 코드 기준: `a8351f0` · [문서 안내](../index.md)

확인: 기존 docs 22개(20 Markdown·2 JSON)와 루트 관련 8개·migration 안내 1개를 읽었다. 아래 목적·내용은 원문 관찰이며 초기 분류 제안과 정리 결과를 함께 기록한다. 원본 31개는 모두 `docs/archive/originals/<이전 경로>`로 이동했고 아래 원본 링크는 보존 위치를 가리킨다. 현재 문서는 [문서 안내](../index.md), 이전→보존→현재 경로는 [경로표](relocation-map.json)에서 확인한다. 코드·설정·테스트·Git 대조 결과는 [충돌 목록](../decisions/index.md)과 [현재 검증 상태](../validation/current-status.md)에 연결한다.

## 이전 docs 원본 22개

| ID·원본 | 목적·의존 관계 | 분류 후보·현재 적용 한계 |
|---|---|---|
| O01 [도우미 기획 v0.2](../archive/originals/docs/GitViz_AI_도우미_기획_v0.2.md) | 인계 대상·준비 상태·초안·원문 미전송, 당시 `0ecbca7` 관찰 | Historical. 현재 리뷰 제품 범위가 아님 |
| O02 [활용 기획 v0.3](../archive/originals/docs/GitViz_AI_활용_기획_v0.3.md) | O01을 PR 설명→리뷰→이력→인수인계→팀·자동화로 확장 | Historical. 코드 전송 허용 등 변화의 근거 |
| O03 [요구사항·설계 v0.4](../archive/originals/docs/GitViz_AI_요구사항_및_시스템설계_v0.4.md) | O02 기반 DB·API·보안·운영 제안. `d59f1e7`과 `0ecbca7` 차이 | Historical + 결정 추출. 초기 한도·API는 현행과 다름 |
| O04 [의존성 구현 계획 v0.5](../archive/originals/docs/GitViz_AI_의존성기반_구현계획_v0.5.md) | O03 기반 S0~S15, 파일·DAG·완료 조건, 범위 갱신 | 최초 계획은 Historical, 폐기·보류 갱신은 범위 근거. 신규 파일 목록을 완료 증거로 쓰지 않음 |
| O05 [초안 검토](../archive/originals/docs/AI_ASSISTANT_PLAN_REVIEW_2026-10-07.md) | 초기 기획 검토, 당시 코드·검증 한계 | Historical. Downloads 원본 경로는 재현성 미확정 |
| O06 [AI 인터페이스](../archive/originals/docs/AI_INTERFACE_CONTRACT.md) | O03·O04 근거, DTO·상태·동의·작업·API·S11·커밋·공유 | 현행 계약 후보. D01~D04 해소 전 무조건 우선 문서 아님 |
| O07 [AI 운영](../archive/originals/docs/AI_OPERATIONS.md) | O06·O11과 연결, 설정·프로세스·복구·삭제 | 현행 안내 후보. 폐기 API 행·로컬 예외는 [새 안내](../operations/ai.md)로 구분 |
| O08 [제공자 지원](../archive/originals/docs/AI_PROVIDER_SUPPORT.md) | profile·BYOK·로컬 설정·대역 검증 | 현행 + Historical 혼합. v3/64KiB 설명은 [현재값](../operations/providers.md)과 다름 |
| O09 [로컬 모델 계획](../archive/originals/docs/AI_LOCAL_MODEL_PLAN.md) | O08 참조, 같은 PC 구현·원격 PC 후속 | 현재 지원 경계 + roadmap 분리 후보 |
| O10 [S9 UI 설계](../archive/originals/docs/AI_S9_UI_DESIGN.md) | O06·HTTP·URL·상태·오류·화면 검증 | 현재 UI 후보. v1.4·prefix·커밋·공유·로컬 보완 필요 |
| O11 [MVP 수용](../archive/originals/docs/AI_MVP_ACCEPTANCE.md) | O07·O12 참조, 당시 fake provider 수용·미확인 출시 조건 | Historical 실행 보고. 제공자 제한·테스트 수를 현재 상태로 승계하지 않음 |
| O12 [모델 평가 절차](../archive/originals/docs/AI_EVALUATION_PROTOCOL.md) | O11 참조, smoke·사람 정답·평가·복구 | 평가 절차 후보. 24K 일반 한도는 profile별로 해석 |
| O13 [팀 접근](../archive/originals/docs/AI_TEAM_ACCESS.md) | O06과 상호 참조, 명시 공유·역할·보고서 | Historical. 본문의 ‘기준’ 선언을 현재 `/shared` 권한으로 적용하지 않음 |
| O14 [분석 타당성 계획](../archive/originals/docs/ANALYSIS_VALIDATION_PLAN.md) | 고정 기간·약한 정답·baseline·평가 지표 | 실험 계획. 정확도 결과 아님 |
| O15 [백엔드 개선](../archive/originals/docs/BACKEND_IMPROVEMENTS.md) | O16·O17 분담, O18 후속 검증 | Historical 구현·검증. 당시 head·테스트 수·미수정 범위에 한정 |
| O16 [수집 전달](../archive/originals/docs/DATA_COLLECTION_HANDOFF.md) | 정책·수집 과제·O18 결과·S3 코드 취득 | 현재 수집 계약·과거 과제·검증을 분리할 후보 |
| O17 [시각화 전달](../archive/originals/docs/VISUALIZATION_HANDOFF.md) | 지표·URL·비교·O18·O10·O06 | 현재 표현 계약·과거 수정 과제를 분리할 후보 |
| O18 [공개 저장소 LIVE 보고](../archive/originals/docs/LIVE_VALIDATION.md) | O19·O20 증거, 실제 Git/REST, 인증 fixture·eager | Historical. 51/52, PR 상세 인증 실패 포함 |
| O19 [LIVE 최초 JSON](../archive/originals/docs/LIVE_VALIDATION_INITIAL.json) | O18의 job running 잔류 재현 | Historical 원시 증거. 수정 후 결과로 덮어쓰지 않음 |
| O20 [LIVE 최종 JSON](../archive/originals/docs/LIVE_VALIDATION_RESULTS.json) | O18의 실행 모드·SHA·52개 검사 | Historical 원시 증거. 1개 실패 유지 |
| O21 [PostgreSQL 검증](../archive/originals/docs/POSTGRES_VALIDATION_2026-10-03.md) | 격리 PG18 설치·기존 업그레이드·snapshot·지표 | Historical. GitHub·브라우저·정확도 검증 아님 |
| O22 [WEB 통합](../archive/originals/docs/WEB_INTEGRATION_2026-10-03.md) | 실제 OAuth·Redis14·별도 worker·PG18, O16·O17 과제 | Historical. 당시 프로세스·기여자 문제를 현재 상태로 단정하지 않음 |

## 이전 Repository 주변 원본 9개

| ID·원본 | 목적·관계 | 분류 후보 |
|---|---|---|
| O23 [README](../archive/originals/README.md) | 설치·실행·AI·snapshot·문서 진입 | 원본은 Historical. 현재 루트 README는 새 문서 안내로 연결 |
| O24 [METRICS](../archive/originals/METRICS.md) | 지표 산식·분모·co-edit·해석·검사 | [현재 지표 정의](../contracts/metrics.md)로 정리, 원문은 Historical |
| O25 [SNAPSHOT_AND_ACCESS](../archive/originals/SNAPSHOT_AND_ACCESS.md) | 접근·불변 결과·기간·backfill·당시 적용 | [현재 스냅샷 안내](../architecture/repository-access-and-snapshots.md)와 과거 적용 기록으로 분리 |
| O26 [HANDOFF](../archive/originals/HANDOFF.md) | 초기 시각화→분석, placeholder·DB read·실행·토큰 | Historical. 없어진 화면·개인 token fallback 설명 주의 |
| O27 [REVIEW](../archive/originals/REVIEW_2026-10-02.md) | `05a58db` 당시 결함·검증·우선순위, O15~O17 참조 | Historical. 현재 미해결 목록 아님 |
| O28 [INTEGRATION_VALIDATION](../archive/originals/INTEGRATION_VALIDATION.md) | 당시 운영 DB read-only·HTTP·브라우저 | Historical. O29와 함께 보존 |
| O29 [INTEGRATION_RESULTS](../archive/originals/INTEGRATION_RESULTS.json) | O28의 32개 검사·측정 | Historical. 환경·시점은 동반 보고서에서 읽음 |
| O30 [SNAPSHOT_INTEGRATION_RESULTS](../archive/originals/SNAPSHOT_INTEGRATION_RESULTS.json) | 인증 대역 스냅샷 검사 | Historical. 실제 GitHub 권한 전체 검증 아님 |
| O31 [migration README](../archive/originals/migrations/README) | 일반 단일 DB 기본 문구 | 현재 migrations/README를 설치·검증 안내로 수정, 원문 보존 |

## 감사에 사용한 보조 자료

확인: [.env.example](../../.env.example), [compose](../../docker-compose.yml), [CI](../../.github/workflows/backend-validation.yml), migration 그래프, 실제 소스·테스트·평가 manifest와 Git 이력을 대조했다. `.env` 비밀값은 이 문서군에 수록하지 않는다.

확인: `.validation/SNAPSHOT_INTEGRITY_REVIEW.md`와 결과 JSON은 로컬에 존재하지만 Git에서 제외된다. 선택적으로 읽은 보조 증거이며 [D05](../decisions/index.md#d05)의 출처 확인 전 정식 실행 기록으로 복사하지 않았다. `outputs` 발표 자료·모든 로컬 로그·화면 이미지는 전수 본문 감사 대상이 아니다.

## 중복·통합 후보

| 중복 | 사실 | 정리 제안 |
|---|---|---|
| 기능 범위 | O01~O04·O11·O23에 서로 다른 시점 범위 | [현재 범위](../product/current-scope.md)에서 관찰값 설명, 원문은 보존 |
| 지표 정의 | O16·O17·O24·O25 중복 | [현재 지표 정의](../contracts/metrics.md)에서 유지, 소비자는 [분석 안내](../contracts/analytics.md) 참조 |
| AI API·상태 | O03·O06·O10·O16·O17 중복 | [AI API](../contracts/ai-api.md)와 [UI](../contracts/frontend.md) 분리 |
| provider·로컬·설정 | O07~O09·O23 중복 | [운영](../operations/ai.md)·[provider](../operations/providers.md)·[roadmap](../product/roadmap.md)로 용도 분리 |
| 검증 상태 | O11·O12·O15·O18~O22·O25·O28~O30 | [현재 상태](../validation/current-status.md)와 [과거 증거](../validation/evidence-index.md) 분리 |

## 링크·참조 관계와 잘못 읽기 쉬운 참조

확인: 감사 당시 기존 Markdown의 일반 상대 링크 41건은 모두 대상이 존재했다. 이는 문서 내용의 현재성·외부 URL·모든 prose/코드 참조·앵커까지 보증한 수치가 아니다.

- O01 → O02 → O03 → O04는 기획 계보다. O03 ↔ O04 및 O06 → O03/O04 참조는 현재 승인 우선순위가 아니다.
- O06 ↔ O13은 폐기 팀 계약으로 이어진다. 현재 계약을 찾다가 Historical 본문의 ‘기준’ 선언을 적용하지 않도록 이 index에서 경고한다.
- O07 → O06/O11, O11 ↔ O12는 운영·수용·평가 연결이다. 과거 미확인과 새 실행 기록은 병합하지 않는다.
- O18 → O19/O20, O28 → O29는 보고서·증거 묶음이다. JSON만 이동하지 않는다.
- O17 → O10/O06, O16/O17/O15 → O18은 담당 전달·후속 검증 관계다.
- 보존 O23은 O04·O06~O12·O14~O18·O25로 진입한다. 현재 [루트 README](../../README.md)는 docs/index와 현재 영역 문서로 연결한다.
- O04의 `providers/http_provider.py`는 현재 adapter 구성과 다른 최초 파일안이다. App·webhook·게시 관련 미존재 경로는 보류 계획이며 깨진 현재 구현 링크로 집계하지 않는다.
- O26의 `app/api/iot.py`는 현재 추적 파일에 없다. 로컬 전용 설명과 배포 구현을 구분한다.
- O05의 Downloads 절대 경로와 O26의 `gitviz_erd_v8`은 Repository만으로 재현할 수 있는 원본 연결이 미확정이다.

확인: 사용자 승인에 따라 이동·현재 문서 수정·참조 갱신을 수행했다. 원문 바이트는 보존하며 보관소 내부 상대 링크도 검사한다. 추정: 이후 변경도 파일 존재뿐 아니라 현재/계획/Historical의 의미까지 검토한다.
