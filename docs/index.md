# GitViz 문서 안내

검토일: 2026-10-23 (Asia/Seoul) · 코드 기준: `a8351f0bf24c78625990f8756e5a03fc42a002c6`

이 문서군은 Repository 감사와 사용자 승인에 따라 정리한 현재 안내다. 현재 문서는 영역별 폴더에서 유지하고, 이전 원문 31개는 [Historical 보관소](archive/index.md)에 옮겨 보존했다. 현재 구현 설명은 해당 커밋의 관찰이며 제품 정책의 승인이나 운영 배포 상태를 대신하지 않는다.

## 읽는 순서

| 목적 | 문서 |
|---|---|
| 현재 구현과 폐기·보류 범위 | [현재 범위](product/current-scope.md) |
| 실행 구조와 저장 경계 | [시스템 구조](architecture/system.md) |
| 접근·불변 결과·기간·backfill | [스냅샷 안내](architecture/repository-access-and-snapshots.md) |
| AI HTTP 계약과 공유 예외 | [AI API](contracts/ai-api.md) |
| 지표·접근·스냅샷 계약 | [분석 계약](contracts/analytics.md) |
| 산식·분모·결측·해석 | [지표 정의](contracts/metrics.md) |
| 전체 분석 수집과 AI 코드 수집 | [수집 계약](contracts/collection.md) |
| URL·미리보기·결과 표현 | [프론트 계약](contracts/frontend.md) |
| 설치·프로세스 | [설치 안내](operations/setup.md) |
| AI 복구·비용·보관 | [AI 운영](operations/ai.md) |
| 제공자 profile·로컬 실행 | [제공자 안내](operations/providers.md) |
| 아직 판단하지 않은 정책 | [결정·충돌 목록](decisions/index.md) |
| 지금 확인한 것과 남은 검사 | [현재 검증 상태](validation/current-status.md) |
| 과거 실행 증거 | [검증 증거 목록](validation/evidence-index.md) |
| 전체 원본·중복·참조 관계 | [문서 인벤토리](audit/inventory.md) |
| 정리 단계·완료 조건 | [정리 계획](audit/organization-plan.md) |
| 문서군 자체 검사 | [일관성 검사 절차](validation/protocols/document-consistency.md) |
| 실제 연동·모델 평가 | [AI 평가 절차](validation/protocols/ai-evaluation.md) |
| 지표 타당성 실험 | [분석 평가 계획](product/analysis-validation-plan.md) |
| 과거 원문과 현재 문서의 대응 | [보관소](archive/index.md) · [이동 경로표](audit/relocation-map.json) |
| 미래 기능·실험 | [후속 계획](product/roadmap.md) |
| Windows 전환 설계·기능 추적·구현 계획 | [구현 전 설계 문서](migration/00-overview.md) |
| c459f29 기준 UX/UI 진단·재설계 Phase 1 | [프로젝트 진단과 설계 문서 5종](ux-redesign/phase-1-c459f29/project-audit.md) |
| 공통 디자인 시스템·데스크톱 셸 Phase 2 | [구현·검증 보고](ux-redesign/phase-2-common-ui.md) |
| 핵심 화면·기존 기능 통합 Phase 3 | [구현·실행 검증·남은 제약](ux-redesign/phase-3-core-screens.md) |
| 전체 UX/UI 검증·최종 개선 Phase 4 | [결함·수정·사용자 여정·최종 검증 보고](ux-redesign/phase-4-ux-quality.md) |
| 로컬 워크스페이스 정보 구조·화면 설계 Phase 5 (설계만, 미구현) | [정보 구조·와이어프레임·시각 방향](ux-redesign/phase-5-workspace-ia.md) |
| 프로젝트 구조 감사 (1차, 읽기 전용) | [구조 감사](architecture/PROJECT_STRUCTURE_AUDIT.md) · [현재 아키텍처](architecture/CURRENT_ARCHITECTURE.md) · [의존성 분석](architecture/DEPENDENCY_ANALYSIS.md) · [제안 아키텍처](architecture/PROPOSED_ARCHITECTURE.md) · [이동 후보](architecture/FILE_MIGRATION_CANDIDATES.md) |
| 개발·빌드·운영 환경 분리 설계 (2차) | [환경 아키텍처](architecture/ENVIRONMENT_ARCHITECTURE.md) · [개발 환경 설정](development/DEVELOPMENT_SETUP.md) · [빌드와 릴리스](deployment/BUILD_AND_RELEASE.md) · [설치 안내](deployment/INSTALLATION_GUIDE.md) · [데이터 저장 정책](deployment/DATA_STORAGE_POLICY.md) |
| 통합 최종 설계 (3차, 4차 작업의 기준) | [최종 아키텍처](architecture/FINAL_ARCHITECTURE.md) · [디렉터리 구조](architecture/FINAL_DIRECTORY_TREE.md) · [이동·변경 계획](architecture/FINAL_MIGRATION_PLAN.md) · [결정 기록](architecture/ARCHITECTURE_DECISIONS.md) · [환경 매트릭스](deployment/FINAL_ENVIRONMENT_MATRIX.md) · [릴리스 이행 계획](deployment/RELEASE_MIGRATION_PLAN.md) |
| 4차 구조 변경 실행 기록 | [구현 기록](architecture/IMPLEMENTATION_LOG.md) · [이동 이력](architecture/MIGRATION_HISTORY.md) |

## 사실과 결정의 구분

- **확인**: 문서 원문·소스·설정·Git 이력 또는 명시한 실행 결과로 확인했다.
- **추정**: 근거에 기반한 분류·구조·운영 제안이며 승인된 제품 정책으로 간주하지 않는다.
- **미확정**: 정책 담당자의 결정이나 추가 검증이 필요하다. 담당자를 임의로 지정하지 않는다.

현재 동작은 소스로 설명하고, 요구사항과 다르면 [충돌 목록](decisions/index.md)에 양쪽 근거를 함께 남긴다. 파일 이름·버전·수정 시각만으로 문서의 우선순위를 정하지 않는다. 과거 보고서의 통과 건수는 현재 코드의 통과로 승계하지 않는다.

## 원본 보존과 진입점

[루트 README](../README.md)는 설치·기능 진입점이고 이 문서 안내로 연결된다. 현재 계약·운영 설명은 검토한 구현을 설명하며, 초기 기획·설계·수용·담당 전달 기록은 [보관소](archive/index.md)에서 해당 시점의 자료로 읽는다. 원본끼리의 링크를 유지하도록 `archive/originals/<이전 경로>` 구조를 사용한다.

확인: 원본 31개의 해시는 [보존 기준](audit/preservation-baseline.json)에 유지했고 이동 위치는 [경로표](audit/relocation-map.json)로 검증한다. 줄바꿈을 포함한 원문 바이트를 보존한다. 현재 문서와 보존 원문의 책임·버전은 별개다. 미확정: 실제 운영 환경 설정·DB revision·최신 원격 CI 결과는 이번 정리로 검증되지 않았다.
