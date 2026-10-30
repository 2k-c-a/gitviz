# 현재 제품 범위

검토일: 2026-10-23 · 코드 기준: `a8351f0` · [문서 안내](../index.md)

## 확인한 구현

| 영역 | 현재 코드의 동작 | 근거 |
|---|---|---|
| 저장소 분석 | GitHub 이력 수집, 고정 SHA·기간·계산 버전의 불변 스냅샷 저장, 이력·비교 조회 | [분석 워커](../../app/tasks/analyze_repository.py), [스냅샷 서비스](../../app/services/analysis_snapshots.py), [비교 API](../../app/api/snapshots.py) |
| 분석 지표 | 유지보수 점검 우선순위, BF80, 기여 분포, 공동 변경, 협업 관계 | [지표 정의](../contracts/metrics.md), [분석 계약](../contracts/analytics.md) |
| AI 입력 | PR 또는 커밋을 선택하고 SHA·선택 파일·전송 자료를 고정 | [입력 서비스](../../app/services/ai/input_builder.py), [수집 계약](../contracts/collection.md) |
| AI 생성 | summary/review 독립 실행, 전송·로컬 실행 확인, 결과·피드백·사용량 저장 | [생성 서비스](../../app/services/ai/generation_service.py), [AI API](../contracts/ai-api.md) |
| S11 | 전체 변경 파일의 추가 검토 후보. 코드 지적과 별개인 스냅샷 근거 | [이력 서비스](../../app/services/ai/history_context.py) |
| 제공자 | Anthropic·OpenAI·Gemini BYOK 및 같은 PC의 Ollama·LM Studio·KoboldCpp | [현재 profile 안내](../operations/providers.md) |
| 결과 공유 | 현재 저장소 접근 권한으로 다른 작성자의 결과·판단·메모를 읽는 별도 GET 경로. 쓰기는 작성자 전용 | [공유 API](../../app/api/ai/shared.py), [미확정 정책 D01](../decisions/index.md#d01) |

AI는 기본 비활성이다. 기능이 구현되어 있다는 사실은 실제 모델 품질·운영 배포·인증 E2E가 검증됐다는 뜻이 아니다. [현재 검증 상태](../validation/current-status.md)를 별도로 읽는다.

## 폐기·보류와 보존

확인: [구현 계획의 범위 갱신](../archive/originals/docs/GitViz_AI_의존성기반_구현계획_v0.5.md)과 앱 router 구성을 대조했다.

| 범위 | 기록된 결정·구현 상태 | 보존 대상 |
|---|---|---|
| S12 인수인계 | 폐기. 해당 화면·API·실행 서비스가 현재 등록되어 있지 않음 | 과거 DB 자료·모델·migration·설계 |
| S13 팀 보고서 | 폐기. 팀 관리·명시적 공유·보고서 API가 현재 등록되어 있지 않음 | 과거 DB 자료·모델·migration·권한 계약 |
| S14 App·Webhook·자동 리뷰 | 보류 계획. 현재 구현으로 안내하지 않음 | 계획과 선행 결정 |
| S15 GitHub 댓글 게시 | 보류 계획. 현재 구현으로 안내하지 않음 | 계획과 선행 결정 |

현재 `/shared` 읽기 전용 기능은 폐기된 S13 팀 역할·공유 테이블 기반 기능과 다르다. 이를 같은 권한 모델로 설명하면 안 된다.

## 표현 제한과 미확정

확인: 지적 0건은 오류 없음이 아니고, 구조·위치 검증은 의미 정확도 보증이 아니다. BF는 실제 지식 인원, 공동 변경은 실제 코드 의존성, 상대 점수는 결함 확률이 아니다.

추정: 위 범위표를 제품 설명의 출발점으로 사용하고 초기 인수인계 시연 흐름은 Historical로 연결한다.

미확정: 공유 정책의 승인·소급 적용, API 버전 정책, 실제 모델별 지원 품질은 [결정 목록](../decisions/index.md)과 [후속 계획](roadmap.md)에 남긴다.
