# Historical 원문 보관소

보존일: 2026-10-23 · 원본 코드 기준: `a8351f0` · [현재 문서 안내](../index.md)

**이 폴더의 원문은 과거 기록이다. 현재 설치·API·권한·기능 범위는 위 문서 안내에서 확인한다.**

확인: 사용자 승인에 따라 기존 31개 파일을 `originals/<이전 경로>`로 이동했다. 바이트와 원본 간 상대 링크를 유지하기 위해 이전 폴더 구조를 보존했다. 경로에 `docs`가 한 번 더 나타나는 것은 중복 문서군이 아니라 원본의 경로 맥락이다. 파일명·상단 버전·본문의 ‘현재/기준/완료’ 표현은 해당 작성 시점의 기록으로 읽는다.

- 초기 기획·설계·구현 계획은 결정의 계보다. 현재 기능 범위와 다를 수 있다.
- 전달·검토 문서의 과제는 자동으로 현재 미해결 목록이 되지 않는다.
- 검증 보고와 JSON은 같은 실행 묶음이며 실제/대역·중단·실패·미확인 한계를 유지한다.
- 현재 지표 정의·평가 절차는 별도 유지 문서로 정리했다. 원본을 갱신해 과거 결과를 바꾸지 않는다.

## 이전 경로와 현재 진입점

| 이전 경로 | 보존 위치 | 현재 안내 |
|---|---|---|
| `HANDOFF.md` | [보존 원문](originals/HANDOFF.md) | 과거 기록으로 보존 |
| `INTEGRATION_RESULTS.json` | [보존 원문](originals/INTEGRATION_RESULTS.json) | 과거 기록으로 보존 |
| `INTEGRATION_VALIDATION.md` | [보존 원문](originals/INTEGRATION_VALIDATION.md) | 과거 기록으로 보존 |
| `METRICS.md` | [보존 원문](originals/METRICS.md) | [docs/contracts/metrics.md](../contracts/metrics.md) |
| `README.md` | [보존 원문](originals/README.md) | [README.md](../../README.md) |
| `REVIEW_2026-10-02.md` | [보존 원문](originals/REVIEW_2026-10-02.md) | 과거 기록으로 보존 |
| `SNAPSHOT_AND_ACCESS.md` | [보존 원문](originals/SNAPSHOT_AND_ACCESS.md) | [docs/architecture/repository-access-and-snapshots.md](../architecture/repository-access-and-snapshots.md) |
| `SNAPSHOT_INTEGRATION_RESULTS.json` | [보존 원문](originals/SNAPSHOT_INTEGRATION_RESULTS.json) | 과거 기록으로 보존 |
| `docs/AI_ASSISTANT_PLAN_REVIEW_2026-10-07.md` | [보존 원문](originals/docs/AI_ASSISTANT_PLAN_REVIEW_2026-10-07.md) | 과거 기록으로 보존 |
| `docs/AI_EVALUATION_PROTOCOL.md` | [보존 원문](originals/docs/AI_EVALUATION_PROTOCOL.md) | [docs/validation/protocols/ai-evaluation.md](../validation/protocols/ai-evaluation.md) |
| `docs/AI_INTERFACE_CONTRACT.md` | [보존 원문](originals/docs/AI_INTERFACE_CONTRACT.md) | [docs/contracts/ai-api.md](../contracts/ai-api.md) |
| `docs/AI_LOCAL_MODEL_PLAN.md` | [보존 원문](originals/docs/AI_LOCAL_MODEL_PLAN.md) | [docs/operations/providers.md](../operations/providers.md) |
| `docs/AI_MVP_ACCEPTANCE.md` | [보존 원문](originals/docs/AI_MVP_ACCEPTANCE.md) | 과거 기록으로 보존 |
| `docs/AI_OPERATIONS.md` | [보존 원문](originals/docs/AI_OPERATIONS.md) | [docs/operations/ai.md](../operations/ai.md) |
| `docs/AI_PROVIDER_SUPPORT.md` | [보존 원문](originals/docs/AI_PROVIDER_SUPPORT.md) | [docs/operations/providers.md](../operations/providers.md) |
| `docs/AI_S9_UI_DESIGN.md` | [보존 원문](originals/docs/AI_S9_UI_DESIGN.md) | [docs/contracts/frontend.md](../contracts/frontend.md) |
| `docs/AI_TEAM_ACCESS.md` | [보존 원문](originals/docs/AI_TEAM_ACCESS.md) | 과거 기록으로 보존 |
| `docs/ANALYSIS_VALIDATION_PLAN.md` | [보존 원문](originals/docs/ANALYSIS_VALIDATION_PLAN.md) | [docs/product/analysis-validation-plan.md](../product/analysis-validation-plan.md) |
| `docs/BACKEND_IMPROVEMENTS.md` | [보존 원문](originals/docs/BACKEND_IMPROVEMENTS.md) | 과거 기록으로 보존 |
| `docs/DATA_COLLECTION_HANDOFF.md` | [보존 원문](originals/docs/DATA_COLLECTION_HANDOFF.md) | [docs/contracts/collection.md](../contracts/collection.md) |
| `docs/GitViz_AI_도우미_기획_v0.2.md` | [보존 원문](originals/docs/GitViz_AI_도우미_기획_v0.2.md) | 과거 기록으로 보존 |
| `docs/GitViz_AI_요구사항_및_시스템설계_v0.4.md` | [보존 원문](originals/docs/GitViz_AI_요구사항_및_시스템설계_v0.4.md) | 과거 기록으로 보존 |
| `docs/GitViz_AI_의존성기반_구현계획_v0.5.md` | [보존 원문](originals/docs/GitViz_AI_의존성기반_구현계획_v0.5.md) | [docs/product/roadmap.md](../product/roadmap.md) |
| `docs/GitViz_AI_활용_기획_v0.3.md` | [보존 원문](originals/docs/GitViz_AI_활용_기획_v0.3.md) | 과거 기록으로 보존 |
| `docs/LIVE_VALIDATION.md` | [보존 원문](originals/docs/LIVE_VALIDATION.md) | 과거 기록으로 보존 |
| `docs/LIVE_VALIDATION_INITIAL.json` | [보존 원문](originals/docs/LIVE_VALIDATION_INITIAL.json) | 과거 기록으로 보존 |
| `docs/LIVE_VALIDATION_RESULTS.json` | [보존 원문](originals/docs/LIVE_VALIDATION_RESULTS.json) | 과거 기록으로 보존 |
| `docs/POSTGRES_VALIDATION_2026-10-03.md` | [보존 원문](originals/docs/POSTGRES_VALIDATION_2026-10-03.md) | 과거 기록으로 보존 |
| `docs/VISUALIZATION_HANDOFF.md` | [보존 원문](originals/docs/VISUALIZATION_HANDOFF.md) | [docs/contracts/frontend.md](../contracts/frontend.md) |
| `docs/WEB_INTEGRATION_2026-10-03.md` | [보존 원문](originals/docs/WEB_INTEGRATION_2026-10-03.md) | 과거 기록으로 보존 |
| `migrations/README` | [보존 원문](originals/migrations/README) | [migrations/README](../../migrations/README) |

## 보존·복원

원본 SHA-256은 [보존 기준](../audit/preservation-baseline.json), 이동·현재 경로는 [경로표](../audit/relocation-map.json)에 있다. `python -B docs/audit/check_documents.py`로 원본 바이트·링크·인벤토리를 검사한다. 복원은 경로표의 원문을 필요한 새 경로로 복사하는 방식으로 검토하며, 현재 문서를 과거 원문으로 무조건 덮어쓰지 않는다.

미확정: 원문 내용이 실제 당시 배포·외부 서비스 상태와 모두 일치했는지는 이번 해시 보존으로 증명되지 않는다. Git에서 제외된 로컬 `.validation` 증거는 별도 출처 확인 대상이며 이 보관소에 자동 편입하지 않았다.
