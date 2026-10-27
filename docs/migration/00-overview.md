# Windows AI 개발 워크스페이스 전환 설계

검토일: 2026-10-23 (Asia/Seoul). 조사 HEAD: `a8351f0bf24c78625990f8756e5a03fc42a002c6`와 작업 시작 당시의 미커밋 작업 트리. 상태: **구현 전 설계 제안**.

## 목적과 범위

기존 GitViz 분석·검색·PR/커밋 AI 리뷰를 유지하면서 Windows 로컬 개발 기능을 통합한다. 이번 산출물은 Markdown 설계뿐이다. 애플리케이션·설정·패키지·DB를 변경하지 않으며 설치, migration, 서비스 실행도 수행하지 않는다.

사용자는 이번 대화에서 **앞선 신규 기능 목록 유지, 플랫폼 조건 미정**을 확인했다. N01~N08이 그 목록의 기능 묶음이다. Windows 최소 버전, 배포 채널, 완전 오프라인 범위를 확정된 요구사항처럼 취급하지 않는다.

## 문서 지도

| 문서 | 책임 |
|---|---|
| [01 저장소 조사](01-repository-analysis.md) | 현재 파일·실행 경로·기능 구현 근거 |
| [02 요구사항·추적표](02-requirements-analysis.md) | E/N/P 식별자, 기능·보안·테스트·단계 연결 |
| [03 전환 전략](03-migration-strategy.md) | 후보 비교·권장안·기능 보존 |
| [04 범위·흐름](04-mvp-and-user-flows.md) | 전체 출시 범위와 사용자 흐름 |
| [05 기능 명세](05-functional-specification.md) | 기능별 입출력·규칙·실패·완료 조건 |
| [06 데이터 모델](06-data-model.md) | 기존/신규 모델, ERD·제약·복구 |
| [07 통신 계약](07-api-design.md) | 기존 HTTP, 신규 인증 HTTP, 로컬 IPC |
| [08 시스템 구조](08-system-architecture.md) | 프로세스·데이터 경계·예정 파일 |
| [09 Windows 통합](09-windows-platform-integration.md) | 설치·인증·창·에디터·업데이트 |
| [10 예외·보안](10-edge-cases-and-security.md) | 위협·실패·복구·보안 요구사항 |
| [11 신뢰성·운영](11-scalability-and-reliability.md) | 부하·버전·작업 수명·운영 비용 |
| [12 검증 전략](12-testing-strategy.md) | 테스트 ID·명령·실제 검증 기록 |
| [13 구현 계획](13-implementation-plan.md) | 단계별 파일·인터페이스·검증·롤백 |
| [14 결정·질문](14-decisions-and-open-questions.md) | 설계 결정·가정·출시 차단 질문 |

## 사실과 제안의 구분

- **관찰 Oxx**: 이번에 실제 파일·설정·등록 경로를 읽은 사실. [01 근거 목록](01-repository-analysis.md)에 연결한다. 운영 배포 사실이나 실행 통과를 의미하지 않는다.
- **사용자 확정**: 기존 기능 보존, N01~N08, Windows 전환, 이번에는 문서만 작성.
- **설계 ADRxx**: 이 문서가 권장하는 선택. 구현 완료·사용자 정책 승인으로 표현하지 않는다.
- **가정 Axx / 질문 Qxx**: 미정 조건과 그 영향. [14](14-decisions-and-open-questions.md)에서 관리한다.
- 백틱으로 쓴 `desktop/`, `app/workspace/`, `tests/workspace/` 등은 명시한 **신규 생성 예정** 경로다. 실제 파일처럼 링크하지 않는다.

## 권장안

Electron에 번들된 기존 웹 UI, 제한된 preload 브리지, 로컬 Python workspace agent를 사용한다. **기존 FastAPI·PostgreSQL·Redis·Celery 서버 기능은 서버에 남긴다.** agent는 로컬 Git·파일·리뷰·승인된 patch·검증만 담당하고 PostgreSQL/Celery를 PC에 배포하지 않는다. 순수 AI 코드 재사용에는 DB 의존성 제거와 동등성 검사가 선행한다.

로컬 SQLite는 프로젝트·고정 변경·제안·실행·알림만 저장한다. 기존 서버 데이터의 복제 DB가 아니다. 로컬 리뷰 결과와 원문은 서버의 기존 공유 API에 올리지 않는다. 원격 UI를 네이티브 권한이 있는 창에 로드하지 않는다.

## 성공 기준과 비목표

E01~E10의 기존 의미·권한·스냅샷 재현성을 보존하고 N01~N08을 모두 검증해야 전체 출시다. 각 단계의 부분 결과는 내부 검증본이며 기능을 MVP에서 제외한 것으로 해석하지 않는다. 무승인 코드 쓰기·커밋·푸시 0건, 프로젝트 경계 이탈 0건, 승인한 내용과 다른 AI 전송 0건이 필수다.

자동 커밋/푸시·reset·파일 대량 삭제, S12/S13 재도입, S14/S15 재개, 다른 OS 지원, 신규 팀 기능은 범위에 넣지 않는다. 사용자의 커밋 결정은 요약 확인 및 외부 도구 이용까지 지원하며 앱 내 커밋 쓰기 기능은 요청 범위가 명확해질 때까지 추가하지 않는다.

## 기존 문서와의 관계

근거: [현재 문서 안내](../index.md), [현재 범위](../product/current-scope.md), [현 시스템](../architecture/system.md). 이 설계는 현재 제품 계약을 덮어쓰지 않는다. 기존 [정책 충돌 D01~D04](../decisions/index.md)는 해소되지 않았다. 관련 요구사항: E01~E10, N01~N08, P01~P08. 미결 조건은 Q01~Q10이며 출시 게이트와 연결한다.
