# 결정·가정·미결 질문

목적: 관찰·사용자 확정·설계 판단을 구별하고 구현/출시 차단 조건을 관리한다. 근거: [01 O01~O18](01-repository-analysis.md), 이번 대화의 사용자 범위 재확인, [기존 정책 D01~D04](../decisions/index.md). 관련: E01~E10·N01~N08·P01~P08, [03 비교](03-migration-strategy.md), [13 단계](13-implementation-plan.md). 담당자는 지정되지 않았으며 필요한 결정 역할만 제안한다.

## 사용자 확정 및 설계 결정

| ID | 상태·결정 | 근거·대안·영향 |
|---|---|---|
| U-R01 | 사용자 확정: 기존 기능 보존·Windows 전환·N01~N08 유지 | 이번 사용자 답변. 신규 목록 자리표시자를 앞선 요구로 보완했으며 임의 기능 추가 없음 |
| U-R02 | 사용자 확정: 지금은 repository Markdown만 | 코드·설정·패키지·schema·설치·migration 변경 금지 |
| ADR01 | 권장: Electron + 작은 Python agent + 기존 서버 | JS UI·Python 재사용·격리 경계. Tauri/네이티브 후보와 단점은 [03](03-migration-strategy.md). 팀 역량·실측·배포 조건 따라 P00 재검토 가능 |
| ADR02 | 권장: 기존 PG/Celery 서버 유지·local SQLite 별도 | PG를 SQLite로 치환하거나 모든 서버 기능을 앱으로 이동하지 않음. local/remote ID 분리 |
| ADR03 | 권장: 제한 브리지·typed stdio·transport seam | raw fs/exec/URL 금지, 원격 콘텐츠에 bridge 없음. localhost API listener 대안은 불필요한 네트워크 노출 때문에 제외 |
| ADR04 | 권장: 시스템 browser OAuth+서버 desktop session | server client secret 유지. app 내 GitHub OAuth secret·cookie 복사·custom protocol handoff는 선택하지 않음 |
| ADR05 | 권장: immutable input·일회 approval·journal | patch·원본 해시 불일치 거부. multi-file transaction 한계를 복구 상태로 노출 |
| ADR06 | 권장: local AI core만 선택 재사용 | 기존 DB orchestration을 가져오지 않고 정책/provider/line-map/validator 동등성 검사 |
| ADR07 | 권장: 코드 참조/공동 변경/AI 추론 분리 | 이력 통계와 결함·의존성의 의미 혼동 방지 |
| ADR08 | 권장: 전 기능 인수 후 출시, 단계별 내부 검증 | 작은 단위부터 구현하되 N 기능을 MVP에서 제외하지 않음 |
| ADR09 | 권장: 기존 웹·API·버전 호환성 유지 | 이전 03의 ADR05에 고유 ID 부여. additive 변경·기존 cookie/DTO·snapshot 의미 유지 |

## P00 승인 범위와 미결 상태

2026-10-24 (Asia/Seoul), 사용자의 다음 단계 승인에 따라 직전 제안의 첫 검토 단위인 **P00 문서 반영**을 수행한다. ADR 대응·RPC 제한·검증 기준·P01 파일/직접 의존성 목록을 설계 기준으로 기록한다. P01 제품 코드 작성·설치·빌드·외부 호출 승인은 포함하지 않는다. ADR의 구현 상태는 여전히 제안/미구현이며 Q01~Q10과 D01~D03은 이 승인만으로 해결되지 않는다.

요구사항 P01~P08은 표에서 `REQ-P01`처럼, 구현 단계 P00~P10은 `STEP-P01`처럼 표시할 수 있다. 원문 ID를 재정의하지 않는다. ADR 대응은 [03](03-migration-strategy.md), 정확한 P01 범위·잔여 선행 조건은 [13](13-implementation-plan.md), 실행 기준·현재 증거는 [baseline](validation/implementation-baseline.md)에 연결한다.

P00의 문서 기준 고정과 전체 정책 게이트 완료를 구분한다. 패키지 전체 버전/전이 의존성/hash·라이선스·취약점·Windows wheel/DLL·Forge 조합은 미검증이다. 지원 언어/editor·제품 SLO·배포 정책도 미정이다. 따라서 P00 전체 완료나 P01 실행 준비 완료를 선언하지 않는다.

## 가정과 영향

| ID | 가정 | 영향·확인 시점 |
|---|---|---|
| A01 | 개발 우선 target은 Windows 11 x64·표준 사용자 | 최소 지원 OS 확정 아님. Q01→P00/P10 |
| A02 | 기존 FastAPI 서버에 additive 인증 endpoint를 배포할 수 있음 | 불가하면 ADR04 재설계 필요. Q02/Q10→P03 이전 |
| A03 | UI·Python 재사용이 Rust/네이티브 추가보다 팀에 유리 | 실제 팀 역량 미확인. Q10→P00 |
| A04 | Git for Windows는 탐지·안내, Python agent는 번들 | Git 번들·license/업데이트 책임은 Q02→P01/P10 |
| A05 | local은 single Windows user·한 앱 instance | 조직 정책·로밍 profile·공유 PC 요구가 있으면 SecretStore/ACL 변경. Q10 |
| A06 | source/output 7일·결과/알림30일, 미해결 journal 보존 | 제안값, 기존 server TTL을 local에 자동 적용하지 않음. Q08→P04/P10 |
| A07 | local 작업은 offline 가능, 원격 기능은 online | 전체 offline 동등성 요구는 별도 비용/동기화 설계. Q03→P00 |
| A08 | session8시간·flow5분·approval5분, refresh 없음 | 보안 초기값이며 사내 idle/재로그인 정책은 Q10→P03 |

## 결정 필요 질문

| ID | 질문·필요 역할 | 미결 시 기본 처리·차단 단계 |
|---|---|---|
| Q01 | 최소 Windows 버전·x64/ARM64·UNC/긴 경로/VDI 지원 범위? 제품/플랫폼 | Win11 x64 개발 검증만; 지원/출시 주장 금지. P00·P10 |
| Q02 | installer·Store/MSIX/사내배포·자동 확인/다운로드/설치 정책·서명 인증서/업데이트 host·Git 번들? 배포 담당 | Forge/Squirrel 후보만, 서명/업데이트 설치는 확정 전 미구현. P10 차단 |
| Q03 | offline 필수 범위·원격 snapshot/cache·private 자료 오프라인 조회 필요? 제품/보안 | local 기능+설치 모델만, remote unavailable·미전송 자동 업로드 없음. 확대 요구면 P00 재설계 |
| Q04 | local private/unknown/unmapped source를 cloud에 보낼 수 있는가, 어떤 동의/조직 정책인가? 제품/보안 | cloud 차단·PC 모델 허용. 요구가 cloud 전면 지원이면 P05 출시 차단, 승인 없이 기존 정책 완화 금지 |
| Q05 | 현재 shared GET의 결과·인용·메모 독자/opt-in·소급 공개를 유지할 것인가? 제품/권한 | 기존 동작을 사실대로 기록, local 결과 신규 공유 없음. E10 desktop 공개 전 D01 결정 |
| Q06 | 지원 언어/프레임워크·symbol parser·복잡도/중복 측정기·test/lint 탐지 최소 목록? 제품/개발 | 파일/Git 분석 가능, 미지원 symbol/지표 명시. N05/N06/N07 정식 인수는 P00에서 범위 확정 필요 |
| Q07 | 첫 출시 필수 외부 editor와 파일/line 이동 요구? 제품/플랫폼 | 사용자가 VS Code·Cursor 초기 지원을 확정(2026-10-27). 실제 줄·열 화면 인수는 별도 게이트 |
| Q08 | local TTL·암호화 메타데이터·backup/키 소실·제거시 삭제·진단 보관 정책? 보안/운영 | ACL+payload 암호화·미해결 journal 보존, release retention 공지 전 P10 차단 |
| Q09 | OS toast가 필수인가? 제품 | 앱내 inbox 필수, toast 선택. 미정이어도 N08 앱내 기능 구현 가능 |
| Q10 | 서버 배포 가능성·API 버전/지원 기간·팀 역량·기업 proxy/CA·악성repo 격리·성능 목표·세션 정책? 플랫폼/보안/운영 | 신뢰된 repo·표준 사용자·보안 기본값, 수치/OS/기업 지원 과장 금지. P00/P03/P09/P10 해당 gate |

## 모순과 범위 경계

사용자 요청은 Windows 버전·배포·offline 정보가 제공된 것으로 가정한 템플릿이었지만 실제로는 자리표시자였고, 사용자가 플랫폼 미정임을 확인했다. 따라서 기능 설계는 구체화하되 배포 결정을 확정하지 않았다.

기존 웹의 '같은 PC 모델'은 server PC를 의미할 수 있으므로 desktop PC 실행과 분리한다. 기존 review prompt의 priority는 심각도가 아니며 local schema severity를 새로 정의한다. 코드 변경 승인과 test 실행 승인은 서로 다른 권한이다. 사용자 폴더 선택은 test 코드의 OS sandbox 권한 부여나 cloud 송신 동의가 아니다.

N03을 위해 안전한 text 파일 patch 적용을 구현하되 파일 삭제·대규모 수정·Git reset·강제 push를 자동 허용하지 않는다. 앱 내 커밋 UI·자동 staging·웹훅·vector 검색·팀 보고서는 이번 확정 목록에 추가하지 않는다. 향후 필요하면 별도 요구사항 ID·보안·데이터 계약으로 관리한다.

## 설계 검토 체크리스트

- [x] 사용자 확인 목록 N01~N08을 F01~F08·C01~C08로 연결.
- [x] 기존 E01~E10의 실제 화면·API·모델·보존/변경/제약 기록.
- [x] server/desktop/agent/renderer·local/remote 데이터 경계 구분.
- [x] 로컬 FK·동시성·승인·통신·인증·복구와 구현 계획 연결.
- [x] P00~P10의 선행·실파일/신규 예정·interface·test·완료·rollback 기록.
- [x] Windows installer/서명/update·Electron IPC/XSS·OS 프로세스 위험 검토.
- [x] 미결 정책을 사용자 승인이나 구현 완료로 표현하지 않음.
- [ ] Q01~Q10 중 출시 차단 사항 확정 — 이번 문서 작성만으로 체크하지 않음.
- [ ] 실제 Windows 제품 검증 — 앱 구현 후 수행.

문서 자체의 링크·추적성·변경 범위 검사는 [12 실제 기록](12-testing-strategy.md)에 남긴다. 기능/OS/보안 통과와 혼동하지 않는다.

## P01 후속 승인 — 2026-10-24

P00 이후 사용자가 P01 착수, 격리 설치·테스트·빌드, workspace 전용 cryptography50.0.2 교체를 각각 승인했다. 기존 서버 requirements/venv는 유지한다. P01은 이후 잔여 검증 기록의 clean VM·중단 경계·실제 NTFS 용량 고갈 인수까지 완료했다. Q01~Q10·D01~D03의 일괄 해결이나 P02 승인으로 취급하지 않는다. [P01 실행 기록](validation/p01-2026-10-24.md)을 현재 상태 근거로 사용한다.

## P01 잔여 검증 갱신 — 2026-10-24

[잔여 검증 기록](validation/p01-remaining-2026-10-24.md)에22개 중단 경계·SQLite FULL·실제 Electron 복구·clean VM·실제 NTFS 용량 고갈 인수를 기록했다. Python/Node 없는 P01Tester 표준 계정에서8개 앱 검사군과 EXE/ASAR/agent 해시 일치, 별도64MiB 가상 디스크의 여유0바이트·ENOSPC·WAL 롤백·원본/백업 보존·공간 확보 후 복구를 확인했다. 승인된 P01 기반 범위는 구현·검증 완료다. P02 이후·installer/서명/배포·전체 제품 검증은 포함하지 않는다.

## P03 사용자 결정 — 2026-10-25

사용자는 Q05에 대해 desktop도 기존 웹 shared GET 조회 권한을 그대로 유지하도록 답했다. 로컬 결과 신규 공유나 기존 웹 정책의 소급 변경은 포함하지 않는다. Q10/A08에 대해 flow5분·desktop session8시간·refresh 없음으로 구현하고 실제 서버 배포·외부 OAuth 검증을 별도 승인 단계에 남기는 제안에 동의했다. Q01~Q10 전체 해결 또는 운영 서버 배포 가능성 검증으로 확대하지 않는다. 동일 버전 CDN 자산 번들링과 격리 PostgreSQL 인수도 각각 승인했다. 구현/검증/남은 gate는 [P03 기록](validation/p03-2026-10-25.md)에 구분한다.

## P05 착수·Q04 기본 처리 확인 — 2026-10-26

사용자는 P05 착수와 Q04의 기본 보안 정책 구현을 승인했다. private/unknown/unmapped cloud는 차단하고 PC 로컬 모델 경로를 우선 활성화한다. 공개 mapped cloud도 인증된 서버의 공개 매핑 proof가 필요하며 현재 local_project에는 그 매핑이 없으므로 cloud 실행을 열지 않는다. 이는 private cloud 전송 완화·Q08 보존기간 확정·실제 모델 호출 승인이나 P03 실제 서버 인수 완료가 아니다. [P05 추적 기록](validation/p05-2026-10-26.md)에 구현·검증·잔여 범위를 구분한다.

## P05 검증 승인·현재 한계 — 2026-10-26

사용자가 임시 DB migration/전체 단위/Electron fixture, 이어서 격리 freeze/기존 실제 Electron 회귀/독립 P05 검증 패키징을 각각 승인했다. [P05 기록](validation/p05-2026-10-26.md)의 PC 로컬 경로가 해당 범위에서 통과했다. 공개 매핑/cloud BYOK·다른 PC runtime·실제 모델 품질·Q08 최종 보존/삭제 정책과 P03 실제 서버 인수는 여전히 남는다. 실제 모델·운영 서버 호출이나 배포 승인으로 확대하지 않는다.

## P06 완료 및 P07 결정 — 2026-10-27

P06의 격리 단위·S11·source Electron과 별도 승인한 frozen agent/독립 패키지 gate가 [P06 기록](validation/p06-2026-10-26.md)에서 통과했다. 승인한 2단계 게시 예외는 F03에 반영했고 TypeScript6.0.3의 동일 버전 runtime 포함을 유지한다.

사용자는 P06 검증 뒤 P07 착수, Q07의 VS Code·Cursor 지원, 두 에디터 설치 및 임시 DB·전체 단위·source Electron 통합 검증을 승인했다. [P07 추적](validation/p07-2026-10-27.md)에 실행 증거와 미검증 화면 인수·freeze/패키징 gate를 구분한다. 이는 실제 운영 서버 OAuth/웹·앱 동시 사용 인수나 Q08 보존 정책·installer/서명/배포 완료가 아니다.

## P07 빌드 승인과 화면 인수 연기

사용자가 P07 freeze·Electron 회귀·검증 패키징을 별도로 승인했고 독립 패키지 인수가 통과했다. 사용자는 에디터 화면 인수를 나중으로 연기했다. 설치·공식 publisher/product/hash 검사와 argv/파일 scope 검증은 완료했으나 실제 GUI 커서 위치는 deferred로 남긴다. 해당 상태와 SHA 증거는 P07 기록이 권위다.

## 사용자 결정 — 2026-10-28

- **D-07 사후 승인:** QA 단계 D의 D-07에서 지시 범위 밖이던 `dispatcher.py`, `scoped_files.py`(`pinned_paths`), `projects.js` 변경과 git status 범주별 2,000개 상한을 승인했다. 상세는 [Stage D 기록](validation/stage-d-2026-10-28.md). 20,000개 pin 상한과 RPC 시간 여유는 Q10 성능 목표로 남는다.
- **문서·추적표 갱신 허용:** [통합 추적표](validation/traceability-2026-10-28.md)를 신설하고 05/07/08/12/13에 상태 주석을 추가했다.
- **알림 보존 기본값:** Q08이 정해질 때까지 workspace_event·notification은 자동 삭제하지 않는다. A06의 30일은 제안값으로만 유지한다.
- **임시 DB schema v7 migration 시험:** 승인. 실제 사용자 DB migration은 포함하지 않는다.
- **Windows 테스트:** Hyper-V를 사용한다.
- **작업 브랜치:** 인수 에이전트가 `feat/p08-workspace-inbox`를 만들었다. push는 하지 않는다.

Q08 전체, Q09, D01~D03은 이 결정으로 해결되지 않는다.
