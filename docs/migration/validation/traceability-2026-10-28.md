# 통합 요구사항 추적표

기록일: 2026-10-28 (Asia/Seoul). 기준 코드: `qa/stage-d-fixes` HEAD `0d13a8e`에서 분기한 `feat/p08-workspace-inbox`. 원문 요구사항은 [02](../02-requirements-analysis.md), 완료 조건은 [05](../05-functional-specification.md), 보안 기준은 [10](../10-edge-cases-and-security.md), 테스트 ID는 [12](../12-testing-strategy.md), 단계는 [13](../13-implementation-plan.md)을 따른다. 이 표는 ID를 새로 만들지 않으며 기존 단계 기록과 QA 결과를 한곳에 모은 것이다.

## 상태 용어와 증거 원칙

상태는 미착수·구현 중·구현 완료, 검증 전·검증 완료·부분 구현·미구현·사용자 결정 대기·환경 제약으로 검증 불가 중 하나다. "검증 완료"는 표시한 증거 범위(개발 호스트 fixture, clean VM 등)에서만 유효하다. 코드 존재, 테스트 통과, 설계 전체 충족은 서로 다른 항목이다.

증거 위치:

- 단계 기록: [P01](p01-2026-10-24.md), [P01 잔여](p01-remaining-2026-10-24.md), [P02](p02-2026-10-24.md), [P03](p03-2026-10-25.md), [P04](p04-2026-10-25.md), [P05](p05-2026-10-26.md), [P06](p06-2026-10-26.md), [P07](p07-2026-10-27.md)
- QA 단계 D 결함 수정과 재검증: [Stage D 기록](stage-d-2026-10-28.md)
- 로그: `.validation/p0N/`, `%TEMP%\gitviz-qa\staged-20261028-114615\`(ignore·저장소 밖)

## 구현 단계 상태

| 단계 | 상태 | 근거·남은 gate |
|---|---|---|
| P00 | 부분 구현 | 문서 기준 고정. 의존성/정책 게이트 일부 미결 |
| P01 | 검증 완료 | clean VM·중단 경계 22개·NTFS 용량 고갈 |
| P02 | 검증 완료(HEAD fixture) | D-07/D-08 이후 workspace 103개·projects/changes e2e 통과 |
| P03 | 부분 구현 | 코드·격리 PG·Electron fixture 통과. 실제 GitHub OAuth·staging·웹/앱 동시 사용 미실행 |
| P04 | 검증 완료(승인 범위) | — |
| P05 | 부분 구현 | PC 로컬 경로 완료, D-02로 main 네이티브 동의. cloud BYOK·공개 매핑·실제 모델 품질 미완료 |
| P06 | 검증 완료(승인 범위) | historical evidence는 원격 매핑 부재로 unavailable |
| P07 | 부분 구현 | 단위·Electron·패키지 fixture 통과. 에디터 줄·열 화면 인수 연기. 독립 패키지는 HEAD 이전 agent(`113A…`) 기준 |
| P08 | 부분 구현 | P08a 로컬 inbox 검증 완료(호스트 fixture, [P08a 기록](p08a-2026-10-28.md)). P08b(원격 분석 완료·connectRemote·navbar) 미착수 |
| P09 | 미착수 | P08·정책 결정 선행 |
| P10 | 미착수 | Q01/Q02/Q08/Q10·서명 자격 선행 |

## 기존 기능 E01~E10

| ID | 핵심 요구 | 관련 단계 | 테스트·명령 | 상태 | 증거·미해결 |
|---|---|---|---|---|---|
| E01~E04 | timeline·기여자·릴리즈·커밋 유형 화면/API 의미 보존 | P03/P09 | `scripts/test_analytics_calculations.py`, `test_metric_policy.py`, remote-compat e2e | 부분 구현 | 계산 회귀·앱 transport fixture 통과. 실제 브라우저 DOM·운영 데이터 미확인 |
| E05 | 협업 네트워크 | P03/P09 | 동일 | 부분 구현 | 동일 |
| E06 | 코드 분석·drilldown | P03/P09 | 동일 | 부분 구현 | 동일 |
| E07 | GitHub 검색·간이 점수 | P03/P09 | `test_lightweight_score.py` | 부분 구현 | 실제 GitHub 호출 미실행 |
| E08 | 등록·내 프로젝트·분석 진행 | P03/P08b/P09 | `test_backend_improvements.py`, remote-compat e2e | 부분 구현 | 로컬 Project 연결(connectRemote) 미구현 |
| E09 | 불변 snapshot·비교 | P03/P09 | `test_repository_snapshots.py` | 부분 구현 | 격리 PG 통과(QA D) |
| E10 | PR/커밋 AI 리뷰·피드백·공유 읽기 | P03/P09 | AI 서버 테스트 23종(저장소·archive) | 부분 구현 | QA D 통과. D01·D03 정책 미결, 로컬 결과 업로드 없음 유지 |

## 신규 기능 N01~N08

| ID·완료 | 설계 | 단계 | 구현 파일·인터페이스 | 테스트 | 상태 | 증거·미해결 |
|---|---|---|---|---|---|---|
| N01·C01 | F01·S01/S02/S03 | P02 | `projects.py`, `scoped_files.py`, `git_service.py`; `projects.*`, `git.status` | T01/T02, `test_changeset_limits.py`, projects e2e | 검증 완료(fixture) | 10,000 tracked×10%까지 ok, 초과 시 `CHANGESET_TOO_LARGE`(D-07 사후 승인). junction은 `unsupported` 표시(D-08). R-1 시간 여유·R-2 junction 내 capture 불가 |
| N02·C02 | F02·S02/S04/S06 | P04/P05 | `snapshots.py`, `jobs.py`, `review.py`, `desktop/src/projects.ts`; `changes.capture`, `reviews.*` | T03, `review-consent.test.ts`, review e2e, A03 probe 13건 | 부분 구현 | PC 로컬 경로 검증. cloud BYOK 미구현, 실제 모델 품질 미검증, R-3 재시도 무재질문 |
| N03·C03 | F03·S01/S03/S04 | P06 | `proposals.py`, `apply.py`, `patches.py`, `write_files.py`; `proposals.*` | T04, `test_apply_recovery.py`, refactor e2e, AP 9건 | 검증 완료(fixture) | multi-file 원자성 없음(설계상 복구 상태 노출) |
| N04·C04 | F04·S01/S05 | P07 | `desktop/src/editor.ts`, `files.locate`, `projects.setEditor` | T05 `editor.test.ts` | 부분 구현 | VS Code·Cursor argv·서명 검사 통과. 실제 줄·열 화면 인수 연기 |
| N05·C05 | F05·S01/S03/S05/S06 | P04/P07 | `detection.py`, `validation.py`, `runner.py`, `diagnostics.py`; `validation.*` | T06, validation e2e | 검증 완료(fixture) | OS sandbox 아님(설계상 고지). detect는 변경 규모와 무관(D-07) |
| N06·C06 | F06·S02/S04 | P04/P06 | `impact.py`, `desktop/src/impact*.ts`; `impact.*` | T07 | 부분 구현 | historical evidence unavailable(`projects.connectRemote` 미구현) |
| N07·C07 | F07·S03/S04 | P07 | `comparisons.py`, `comparison-metrics.ts`, `metrics.ts`; `comparisons.*` | T08 `test_comparison_metrics.py` | 검증 완료(fixture) | 실측 지표는 분기 수·exact window 중복만 |
| N08·C08 | F08·S02/S06 | P08 | `events.py`, `notifications.py`, `notification_schema.py`, `desktop/src/notifications.ts`, `inbox.js`, `navigation.js`; `notifications.list/markRead`, `events.read`, main IPC `workspace:events-subscribe/unsubscribe` | T09 `test_notifications.py`(17), `notifications.test.ts`(3), `inbox.e2e.ts` | 부분 구현 | 로컬 review/validation/apply 사건은 검증 완료(호스트 fixture). 원격 분석 완료 통합(P08b)·OS toast(Q09) 미구현 |

## 플랫폼 요구 REQ-P01~P08

| ID | 요구 | 단계 | 테스트 | 상태 | 증거·미해결 |
|---|---|---|---|---|---|
| REQ-P01 | 기존 UI 번들·보안 브리지·표준 사용자 | P01/P09 | T10, `bridge-security.test.ts`, foundation e2e, IPC-01~07 | 검증 완료(fixture) | D-03으로 workspace 브리지는 index 문서만. 웹 화면 `window.desktop.remote` 노출 유지(R-5). `workspace.e2e.ts` 미작성 |
| REQ-P02 | 웹 OAuth 유지·시스템 브라우저 로그인 | P03 | T11 `test_desktop_auth.py`(+`--postgres`), auth e2e | 부분 구현 | 실제 OAuth 미실행. D-01·D-05 반영 |
| REQ-P03 | 로컬 DB·비밀 저장·강제 종료 복구 | P01/P04 | T12 `test_recovery.py`, CN-01/02 | 검증 완료(fixture) | Q08 보존 정책 미결 |
| REQ-P04 | Windows 에디터·프로세스·파일 잠금 | P02/P07 | T05/T06/T12 | 부분 구현 | 에디터 화면 인수 연기 |
| REQ-P05 | 설치·서명·업데이트 | P10 | T13 | 미구현 | Q01/Q02 |
| REQ-P06 | 네트워크·만료·부분 실패 | P03/P09 | T11/T12/T14 | 부분 구현 | 실제 서버 미실행 |
| REQ-P07 | 웹/앱 동시 사용·API 호환 | P03/P09 | T14/T15 | 부분 구현 | A08 미재현·archive 통과. 실제 동시 사용 미실행 |
| REQ-P08 | 책임 경계·최소 로그·진단 | P01/P09 | T10/T12/T14 | 부분 구현 | 진단 내보내기 미구현 |

## 보안 기준 S01~S08

| ID | 상태 | 증거 |
|---|---|---|
| S01 경계 | 검증 완료(시험 경로) | PV 48·PVS 5·HL-01 |
| S02 프로젝트·계정 분리 | 부분 구현 | IPC-07, A03 other-project, T09 다른 프로젝트 알림 위조 거부·inbox e2e. 원격 계정 분리는 P03 실제 서버 인수 대기 |
| S03 승인·hash·journal | 검증 완료(시험 경로) | AP 9·IPC-02~06, D-10 상수 시간 비교 |
| S04 untrusted source·동의 bytes | 검증 완료(시험 경로) | A03 13건 |
| S05 고정 실행·자식 제한 | 검증 완료(시험 경로) | T06, validation e2e |
| S06 암호화·비식별 로그 | 검증 완료(시험 경로) | CN-01a/b·CN-02. 이벤트/알림에 소스·경로·프롬프트 미저장(T09) |
| S07 renderer 격리 | 검증 완료(시험 경로) | IPC-01, A05-a/b/c |
| S08 서명·배포 | 미구현 | P10 |

"위반 0"은 시험한 경로에 대한 결과다. 모든 경로를 증명하지 않는다.

## 출시 차단 항목

N08 원격 부분(P08b)·REQ-P05 미구현, P03 실제 서버 인수, P07 에디터 화면 인수, 정책 D01·D02·D03·Q01·Q02·Q03·Q08·Q10, Windows CI 부재. 전체 출시 완료로 선언하지 않는다.
