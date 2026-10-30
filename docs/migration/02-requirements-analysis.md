# 요구사항과 추적성

목적: 기존 기능 보존과 사용자가 재확인한 신규 기능을 구현·검증에 연결한다. 범위: E=관찰된 기존 기능, N=확정 신규 기능, P=플랫폼 전환 요구. 근거: [01 O01~O18](01-repository-analysis.md), 사용자 최초 첨부와 이번 범위 확인. 설계의 상세 규칙은 [05](05-functional-specification.md), 테스트는 [12](12-testing-strategy.md), 단계는 [13](13-implementation-plan.md). 확정되지 않은 정책은 [14](14-decisions-and-open-questions.md)로 분리한다.

## 기존 기능 보존 목록

API 표에서 A는 `/repositories/{id}/analytics`, I는 `/api/ai/v1`이다. 화면·모듈은 실제 존재 경로이고 신규 transport는 예정 파일이다. E 기능 모두 P03/P09에서 회귀하며, 기존 서버 데이터 모델은 유지한다.

| ID | 현재 동작·화면/모듈 | API·데이터 | 재사용 / 필요한 변경·신규 / 제약 |
|---|---|---|---|
| E01 | 개발 타임라인, `timeline.html`, `timeline.js` | `A/timeline`, Commit·Snapshot | 차트·집계 유지 / transport·로컬 자산 / 기간·SHA 보존 |
| E02 | 주요 기여자·주간 커밋, 같은 화면 | `A/top-contributors`, `A/contributor-heatmap`, Contributor·Alias·Commit | 집계·히트맵 유지 / transport / 식별 누락·분모 유지 |
| E03 | 릴리즈·버전, timeline | `A/releases`, Release | 화면·API 유지 / transport / Git tag와 GitHub release 혼동 금지 |
| E04 | 커밋 유형 비율, timeline | `A/commit-types`, Commit.commit_type | 분류·비율 유지 / transport / 분류 추론 한계 유지 |
| E05 | 협업 네트워크·상위 쌍, `network.html/js` | `A/network`, `A/network/top-pairs`, Snapshot | D3·NetworkX 산식 유지 / transport·자산 / 관계는 코드 의존 아님 |
| E06 | 수정 집중 파일·공동 변경·기여자·트리, `code-analysis.html/js`, `drilldown.js` | `A/risk-scores`, `A/coupling`, `A/coupling/{file_id}`, `A/files/{file_id}/detail`, `A/bus-factor`, `A/tree`, contributor drilldown; File·Change·Snapshot | 기존 계산·상세 유지 / 프로젝트 연결 / BF·상대점수 의미 유지 |
| E07 | GitHub 검색·간이 건강도, `search.html/js` | `GET /search/github-repositories`, lightweight_score | 검색·점수 유지 / transport / deep risk와 혼합 금지 |
| E08 | 등록·내 프로젝트·분석 진행, `myprojects.html/js`, `modal.js` | `/repositories`, `/repositories/mine`, `/{id}/reanalyze`, `/{id}/status`; Repository·Access·AnalysisJob | 서버 수집 그대로 / 로컬 Project 연결 UI 신규 / 로컬 clone 갱신에 사용 금지 |
| E09 | 불변 분석 이력·비교, `history.html/js` | `GET /repositories/{id}/snapshots`, `/snapshots/compare`; AnalysisSnapshot | SHA·hash·계산 버전·정렬·비교 유지 / transport / rename 비교 한계 유지 |
| E10 | PR/커밋 summary·review·피드백·이력·공유 읽기, `ai-review.html/js`, `ai-api.js` | `I/inputs`, `/jobs`, `/results`, `/findings/{id}/feedback`, `/history/snapshots`, `/shared/results`; AI 모델군 | 기존 서버 실행·BYOK 유지 / 인증·transport / D01 공유 미확정·로컬 결과 업로드 금지 |

## 신규 기능 추적표

Fxx는 [05](05-functional-specification.md)의 명세 절, Uxx는 [04](04-mvp-and-user-flows.md)의 흐름, Sxx는 [10](10-edge-cases-and-security.md)의 보안 기준이다. API의 `W`는 로컬 `workspace.*` IPC이며 HTTP가 아니다. 완료 기준 C01~C08은 각 행과 F절에 동일하게 정의한다.

| ID·하위 요구 | 명세·흐름 | UI·모듈 / IPC | 데이터 | 보안 | 테스트·단계·완료 |
|---|---|---|---|---|---|
| N01 폴더 선택·Git 확인·브랜치·변경·stage 구분·최근 커밋·여러 프로젝트·설정/분석 분리 | F01·U01/U02 | workspace, ProjectRegistry/LocalGit; `W.projects.*`, `W.git.status` | local_project, change_snapshot | S01/S02/S03 | T01/T02; P02; C01=프로젝트 전환·Git 상태·경계 검증 |
| N02 커밋 전 리뷰·버그/보안/성능/품질/테스트 누락·심각도·파일/위치/영향/개선·수정 결정 | F02·U03 | changes/review, ReviewService; `W.changes.capture`, `W.reviews.*` | review_run, review_finding | S02/S04/S06 | T03; P05; C02=고정 입력·명시 동의·근거·미검증 표시 |
| N03 선택 코드 리팩토링·목적/기대효과·전후 비교·작은 변경·승인/거절/재요청·덮어쓰기 방지 | F03·U04 | proposal/diff, ProposalService; `W.proposals.*` | proposal, approval, apply_journal | S01/S03/S04 | T04; P06; C03=승인 없는 쓰기 0·해시 충돌·중단 복구 |
| N04 선호 외부 에디터·경로/위치·Windows 실행 차이·실패 안내·느슨한 결합 | F04·U05 | editor preference, EditorAdapter; `W.editors.*` | project_setting | S01/S05 | T05; P07; C04=경로/위치 전달·미설치 대체 |
| N05 언어/프레임워크/패키지 탐지·기존 명령 우선·명령/cwd 검토·결과/종료/stdout/stderr·실패 연결·실행 제한 | F05·U05 | validation, Detector/Runner; `W.validation.*` | command_plan, validation_run, diagnostic | S01/S03/S05/S06 | T06; P04/P07; C05=검토한 계획만 실행·취소/timeout·실패 위치 연결 |
| N06 참조 함수/클래스/모듈·위치·공동 변경·영향 테스트/기능·사실과 AI 추론 구분 | F06·U04 | impact, ImpactService; `W.impact.analyze` | impact_evidence | S02/S04 | T07; P04/P06; C06=정적/이력/추론 근거·미분석 범위 구분 |
| N07 diff·테스트/린트 비교·실측 복잡도/중복/범위·목적/결과/잔여위험 | F07·U06 | comparison, ComparisonService; `W.comparisons.*` | measurement, comparison | S03/S04 | T08; P07; C07=동일 도구/환경 비교·미측정 null |
| N08 여러 프로젝트 통합 이벤트·유형/심각도/읽음 필터·클릭 이동·중복·시각/원인 | F08·U02/U06 | inbox, NotificationService; `W.notifications.*` | workspace_event, notification | S02/S06 | T09; P08; C08=중복 처리·필터·대상 이동·재시작 보존 |

## 플랫폼 요구 추적

| ID | 요구·설계 | 테스트·단계 |
|---|---|---|
| P01 | 기존 UI 번들·보안 브리지·표준 사용자 실행 | T10·P01/P09 |
| P02 | 웹 OAuth 유지·시스템 브라우저의 데스크톱 로그인·토큰/계정 분리 | T11·P03 |
| P03 | 로컬 DB·비밀 저장·강제 종료 복구 | T12·P01/P04 |
| P04 | Windows 에디터·프로세스·파일 잠금·권한 | T05/T06/T12·P02/P07 |
| P05 | 설치/제거·서명·업데이트 무결성·호환성 | T13·P10; 배포 상세 Q01/Q02 |
| P06 | 네트워크·인증 만료·부분 실패·재실행 | T11/T12/T14·P03/P09; 오프라인 범위 Q03 |
| P07 | 기존 웹/앱 동시 사용·API 호환성·프로젝트 분리 | T14/T15·P03/P09 |
| P08 | 책임 경계·최소 로그·진단·운영 제한 | T10/T12/T14·P01/P09 |

## 충돌·잘못된 가정·누락

| ID | 발견·영향 | 심각도 | 권장 대응 |
|---|---|---|---|
| R01 | 수집 clone의 reset을 로컬 폴더에 재사용하면 원본 손실 | 치명 | read-only LocalGit 신규, clone manager 금지 |
| R02 | 웹 OAuth client secret를 설치 파일에 넣으면 비밀 유출 | 치명 | 서버 OAuth 유지·일회 로그인 handoff |
| R03 | 현재 PR/commit FK에 미커밋 변경을 끼우면 허위 리비전·권한 혼동 | 높음 | 별도 로컬 모델·버전 계약 |
| R04 | PG 스키마를 SQLite로 단순 치환하면 trigger·FK·원자성 손실 | 높음 | 서버 DB 유지·작은 로컬 DB 신규 |
| R05 | shared GET 결과/인용/메모 공개 정책 D01 미확정 | 높음 | 기존 동작 관찰 보존, 로컬 결과 신규 공유 금지·Q05 |
| R06 | provider localhost는 서버 PC일 수 있어 사용자 PC 모델과 다름 | 높음 | 로컬 agent에서 실행 위치 명시 |
| R07 | priority를 severity로 표시하면 기존 의미 왜곡 | 중간 | 새 schema에 별도 severity·reason, 기존 unknown |
| R08 | 공동 변경을 정적 의존으로 간주하면 영향 오판 | 높음 | evidence 종류·시점·도구를 분리 |
| R09 | 테스트 이름만 허용하면 script가 실행할 임의 코드를 통제 못함 | 높음 | 신뢰·명령 동의, 비격리 실행 사실 명시 |
| R10 | CA 없는 원격 async DB에서 TLS 인증서 검증 해제 | 높음 | 기존 서버 배포 보안 게이트로 수정 계획, 이번 코드 변경 없음 |
| R11 | OS/배포/오프라인·보존기간·에디터/언어 지원 범위 미정 | 중간~높음 | Q01~Q10 결정 후 해당 출시 게이트 통과 |

기능은 제외하지 않는다. 모호한 정책을 임의 확정하지 않고 안전한 기본값과 차단 조건을 [14](14-decisions-and-open-questions.md)에 명시한다.
