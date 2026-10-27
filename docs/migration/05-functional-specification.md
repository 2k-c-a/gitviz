# 기능 상세 명세

목적: N01~N08을 구현 가능한 입력·규칙·실패·완료 조건으로 고정한다. 근거: [01 O06/O09~O15/O18](01-repository-analysis.md), 사용자 확정 목록. 관련: [02 요구사항](02-requirements-analysis.md), [06 데이터](06-data-model.md), [07 계약](07-api-design.md), [12 검증](12-testing-strategy.md). 다음 규칙은 신규 설계이며 구현된 동작이 아니다. 언어·에디터·보존기간·비공개 전송은 Q04/Q06/Q07/Q08이 남아 있다.

## 공통 규칙

모든 요청은 protocolVersion, requestId, operation, 명시 projectId를 사용한다. 프로젝트를 요구하지 않는 목록·폴더 선택만 예외다. 원본 코드 쓰기·명령 실행·AI 전송은 각각 별도의 승인을 요구한다. 리뷰 결과나 '제안 만들기' 버튼은 쓰기 승인으로 승격되지 않는다. 결과는 snapshotId·scope·tool/profile version·생성 시각·limitations를 포함한다. 사용자 파일·README·prompt 형태의 문자열은 실행 명령이나 상위 지시가 아니다.

## F01 프로젝트 및 Git 상태 — N01 / U01·U02 / P02

- **사전 조건:** agent ready, native folder dialog로 선택. 등록은 로그인 불필요. Git이 없거나 repo가 아니면 등록 성공으로 위장하지 않는다.
- **입력/출력:** 폴더 선택 결과는 main→agent에만 전달. 결과 ProjectRef는 UUID·표시명·접근 상태·remote 연결(선택), RepositoryState는 branch nullable·detached·unborn·head nullable·staged/unstaged/untracked/conflicts·최근 commit이다.
- **처리:** 선택 경로의 실제 root와 metadata를 검증 → 사용자 선택 root가 repository root인지 확인(하위 폴더 선택 시 root 확대를 재확인) → 등록 중복 검사 → `git status --porcelain=v2 -z`, 제한된 log·diff를 shell 없이 실행한다. 실제 root·git-dir·common-dir와 Windows 파일 identity를 기록한다.
- **규칙:** 동일 canonical root 중복 등록은 기존 ID를 반환한다. worktree는 root가 다르면 별도 Project다. linked worktree의 외부 metadata는 별도 grant를 보여주며 파일 본문 범위는 확대하지 않는다. sparse checkout·submodule은 상태/제한을 명시하고 자동 초기화하지 않는다. 변경 설정은 project_setting에 version을 두고 CAS로 저장한다.
- **UI:** project picker·상태 요약·staged와 unstaged 별도 목록·최근 commit. 한 파일의 staged/unstaged 동시 항목을 합치지 않는다. 프로젝트 전환 시 요청 세대를 증가시킨다.
- **변경 데이터/의존:** local_project·project_setting; 기존 Repository는 remote 연결 ID만 참조하며 자동 등록하지 않는다.
- **예외/보안:** Git 없음·권한 거부·경로 이동·network share·안전하지 않은 repo ownership·충돌·unborn·SHA 알고리즘·비UTF8 이름은 typed error/limitation. `safe.directory=*` 설정 금지. 경로를 URL·서버 로그에 전송하지 않는다.
- **완료 C01/T01·T02:** 실제 Git fixture의 상태·최근 commit 일치, dirty index/파일 바이트 불변, 범위 이탈·late response 차단, 재실행 시 프로젝트별 설정 분리.

## F02 고정 변경과 커밋 전 리뷰 — N02 / U03 / P04·P05

- **입력:** ChangeSelection은 staged/unstaged 항목과 선택 경로, 선택적 untracked 포함 여부. Capture는 HEAD→index, index→working tree를 별도 facet으로 기록한다. 같은 경로의 두 facet은 고유 fileId를 갖는다.
- **캡처:** HEAD·index·전체 변경 목록·선택 파일 pre/post hash를 비교하고 중간 변경이면 CAPTURE_CHANGED로 재시도 안내. 바이너리·큰 파일·생성물·link·비밀 의심 내용을 제외하고 제외 이유와 전체/선택/분석 coverage를 따로 저장한다. Git diff만 저장해서 삭제/맥락 비밀 검사를 생략하지 않는다. 같은 path의 staged/unstaged는 별도 fileId·facet·좌우 줄 맵으로 검증하며 path만으로 지적 위치를 해석하지 않는다.
- **전송:** preview는 선택 bytes·문맥·제외·destination·executionLocation·profileFingerprint·purpose·inputHash·policyVersion을 보여준다. local model도 실행 범위 확인을 받는다. cloud는 원격 mapped repo의 공개 확인과 명시 동의가 모두 필요하다. private/unknown/unmapped cloud는 Q04 결정 전 차단하며 로컬 모델 경로는 유지한다.
- **출력:** finding은 category(correctness/security/performance/quality/testing 등), priority, severity(critical/high/medium/low/unknown), severityReason, condition, impact, evidence fileId/facet/path/side/line/quote, suggestion, verification, limitations, humanValidation을 가진다. 추론 지적이며 실행해 확인한 버그로 표시하지 않는다. 새 schema를 만들고 기존 priority를 severity로 변환하지 않는다. severity는 critical=비밀 유출/광범위 파괴 등, high=핵심 기능 손상/데이터 손실, medium=조건부 기능 오류·유의한 자원 문제, low=제한적 영향, unknown=판단 근거 부족으로 정의한다. 모델의 severityReason·발생 조건을 함께 표시하며 확정 피해 규모로 표현하지 않는다.
- **UI/상태:** preview→consent→queued/running→succeeded/failed/cancelled/uncertain. 사람 판정은 valid/false_positive/uncertain/unverified로 원본과 분리한다. scope가 달라지면 이전 동의·결과 적용 가능성을 무효화한다.
- **데이터/통합:** change_snapshot·change_file·review_run·review_finding·review_feedback·transfer_consent. content policy/line-map/provider/validator 재사용, SQL orchestration은 agent 구현으로 분리. 서버의 기존 PR·commit AI는 E10 그대로 유지.
- **실패:** 입력 변동·만료·provider 오류·rate limit·예산·잘린 JSON·잘못된 위치/quote는 실패/명시된 partial 상태. 결과 불명 호출은 새 과금 가능성을 설명하고 자동 재전송하지 않는다. 로그에 payload/key 저장 금지.
- **완료 C02/T03:** 전송 bytes와 동의 hash 일치, 비밀/미선택 코드 유출 차단, 카테고리·위치·severity 검증, 지적 0건·미검증·누락 범위를 정확히 표시.

## F03 리팩토링 제안과 승인 적용 — N03 / U04 / P05·P06

- **입력:** 고정 snapshot의 선택 파일 또는 changed facet, 사용자 목적. 파일 선택도 content snapshot을 먼저 만든다. 원본 hash 없는 live path만으로 patch를 생성하지 않는다. `reviews.preview/start(kind=refactor)`로 목적·instruction까지 동의한 generation을 실행하고, 완료된 refactor run을 `proposals.create(refactorRunId)`로 제안에 연결한다. proposals.create 자체는 추가 모델 호출을 하지 않는다.
- **생성/출력:** refactor 전용 prompt/schema로 작은 범위의 unified patch·파일별 preimageHash/targetHash·목적·기대효과(예측)·위험·검증 제안을 반환한다. 새 generation은 새 proposal ID·version이며 이전 승인을 상속하지 않는다. 모델에는 명령/파일 도구를 주지 않는다.
- **UI/규칙:** 전후 diff·영향 근거·예상 검증을 보여주고 승인/거절/재요청을 제공한다. refactor 명령이 코드 적용까지 포함하지 않는다. 거절은 상태 변경만, 재요청은 새 generation/proposal이며 기존 것은 superseded·미소비 승인 revoke로 전환하고 payload·상태 변경 이력을 보존한다.
- **적용 사전 조건:** 대상은 프로젝트 내부의 선택된 일반 text 파일이며 추적 중 파일과 기존 untracked 파일 모두 원본 hash로 검증한다. delete/rename/link/권한 변경·project 밖·`.git`·실행 설정 대량 수정 patch는 적용을 거부하고 작은 text 변경으로 재요청을 제공한다. staged 코드와 working tree가 다르면 index 대상 제안의 적용을 거부한다.
- **쓰기 절차:** 사용자 승인 → main이 일회 opaque approval capability 발행 → agent가 project/proposal/patchHash/preimage/index 조건·승인 기한을 확인 → 프로젝트 write lock → 읽은 handle identity·바이트 재확인 → 암호화 backup·apply_journal durable 기록 → 배타 handle을 유지한 채 원본을 임시 보존명으로 rename하고 새 파일을 원래 경로에 덮어쓰기 없이 게시(사용자 승인한 2단계 예외) → 각 파일 완료 기록 → 최종 targetHash 확인. index·commit·push는 수정하지 않는다.
- **동시성/복구:** 두 게시 단계 사이 원래 경로가 잠시 없을 수 있다. 중단 시 recovery_required로 표시하고 명시 승인으로만 복구한다. 재시작은 자동으로 원본을 쓰지 않는다. 원본 owner/group/DACL은 동일하게 복제·검사하며 SACL 보존은 보증 범위가 아니다. 임시 생성 직후 journal identity 기록 전 종료 등 소유권이 불명확한 파일은 자동 삭제하지 않고 수동 확인이 필요하다. 외부 editor/다른 Git 도구의 변경과 충돌하면 PRECONDITION_FAILED. 여러 파일은 하나의 파일시스템 transaction이 아니므로 applying/partial/recovery_required 상태를 둔다. 복구는 현 내용이 앱 targetHash와 같을 때만 사용자가 선택한 원복을 수행하며 제3자 변경을 덮어쓰지 않는다.
- **데이터:** proposal·approval·apply_journal, review_run(kind=refactor)와 연결. 재승인·중복 request는 같은 적용 결과를 반환하며 두 번째 쓰기를 하지 않는다.
- **완료 C03/T04:** 승인 없는 쓰기 0, 한글/CRLF/잠긴 파일·중간 종료·외부 변경 fixture 검증, decline/re-request 가능, 복구 후 index/기존 사용자 변경 보존.

## F04 외부 에디터 — N04 / U05 / P07

- **사전 조건/입력:** projectId·검증된 fileId/상대경로·유효 line/column·editorId. 절대 editor 실행 경로는 native 설정 화면에서만 등록한다.
- **처리/출력:** VS Code 계열 CLI 후보, JetBrains 등 선택 adapter별 executable/argv 템플릿을 사용한다. 지원표 Q07을 확정한 adapter만 제공한다. shell 문자열·사용자 정의 명령 템플릿을 받지 않는다. 실행 성공은 편집 완료를 뜻하지 않는다.
- **UI/데이터:** project_setting의 editorId, main의 검증된 설치 경로. 실패 시 미설치·파일 없음·접근 거부·line 이동 미지원 이유와 경로 복사/다른 editor 선택을 안내한다.
- **보안/완료 C04/T05:** Windows 공백·한글·특수문자·선행 하이픈 경로가 옵션 주입 없이 전달됨, 없는 editor에서 안전 실패, agent의 file scope와 main editor scope가 같은 프로젝트를 검사함.

## F05 탐지와 테스트·린트 — N05 / U05 / P04·P07

- **탐지 입력:** scoped manifest·lockfile·기존 설정만 읽는다. JS의 package scripts·lockfile, Python의 pyproject/setup/requirements 및 pytest/unittest/lint 설정, 기타 언어는 Q06에서 확정한다. config를 import/실행해 탐지하지 않는다.
- **출력/계획:** language/framework/packageManager는 evidence path·확신 수준과 함께 반환. 충돌하는 lockfile·monorepo cwd는 사용자가 선택한다. CommandPlan은 executable의 검증된 경로·argv 배열·cwd·script 내용/설정 hash·환경 allowlist·timeout·outputLimit·risk를 포함한다. 명령이 없으면 사용자에게 설정 필요를 표시하며 AI가 임의 명령을 만들어 실행하지 않는다.
- **승인/실행:** Windows 사용자 신뢰와 계획별 승인을 받은 뒤 config hash·cwd·snapshot을 재확인한다. package manager의 pre/post hooks도 포함하여 검토한다. 의존성 자동 설치·lint --fix·coverage 설정 수정은 하지 않는다. 승인된 .cmd 실행에 shell이 불가피하면 고정 launcher와 엄격 인자 처리로 제한하고 arbitrary shell 입력을 금지한다.
- **결과:** runId·snapshotId·commandPlanId·status·exitCode nullable·stdout/stderr 별도 순서 sequence·truncated·시작/종료 시각. failed/cancelled/timed_out/interrupted는 exitCode 0과 다르다. diagnostic은 project/file/line/tool·scope를 가진다. parser가 없는 출력은 raw text와 '위치 연결 불가'로 표시한다.
- **권한/위험:** 실행 프로세스는 같은 Windows 사용자 권한이다. cwd 제한·argv allowlist는 실행한 테스트의 파일/네트워크 접근을 OS sandbox로 제한하지 않는다. 이를 승인 화면에 설명하고 신뢰하지 않는 프로젝트 실행을 기본 차단한다.
- **완료 C05/T06:** 계획과 실행 일치·config 변경 시 재승인·자식 트리 종료·출력 한도·권한 오류 검증. 실패 diagnostic을 선택 파일·review finding과 연결하되 인과관계로 단정하지 않음.

## F06 변경 영향 — N06 / U04 / P04·P06

- **입력:** snapshotId·선택 symbol/file, 선택 remote snapshot ID. 분석 중 live 파일 대신 고정 입력/버전을 기준으로 삼는다.
- **출력:** kind=`static_reference|historical_cochange|ai_inference`, observed/inferred 구분, path/location·source SHA/hash·toolVersion·supportCount·limitations. static reference는 parser가 확인한 import/call/type 연결 범위만, dynamic dispatch는 미해결로 표시한다. 문자열 grep은 확정 call graph가 아니다.
- **이력:** 사용자가 연결한 동일 remote의 유효 snapshot에서만 과거 공동 변경·기여 분포를 가져온다. remote identity·기간·계산 버전·hash·미래 자료를 검증하는 기존 S11 규칙을 재사용한다. Git 이력과 PR 기반 단위를 구분한다. offline/연결 없음은 이력 unavailable이지 0이 아니다.
- **UI:** 영향 가능 테스트/기능/모듈은 근거별 목록이며 추론된 기능명은 inferred로 표시. 미지원 언어도 파일·이력 분석은 가능하고 symbol 분석은 unsupported 범위를 명시한다.
- **완료 C06/T07:** 알려진 import/call fixture에서 위치·종류 일치, 새 파일/rename/미지원 언어·stale snapshot·오염된 hash 처리, AI 추론이 observed로 승격되지 않음. 지원 언어 Q06 확정은 출시 게이트다.

## F07 전후 비교와 의사결정 — N07 / U06 / P07

- **입력:** beforeSnapshotId·afterSnapshotId·proposalId, 각각의 validation/measurement ID. 다른 프로젝트 조합 거부.
- **측정:** comparisons.create는 두 고정 입력의 기본 changedFiles/additions/deletions/bytes를 계산해 measurement를 생성한다. 지원되는 고정 parser의 symbol/complexity·중복 측정도 같은 단계에서 수행하고 tool/version/config·단위·분모·입력 hash를 기록한다. 외부 측정 도구가 필요하면 먼저 F05의 승인된 계획으로 실행한 결과 ID를 참조한다. 도구 없는 값은 null+reason. AST 지표를 지원하지 않는 언어에 줄 수를 복잡도로 표시하지 않는다.
- **비교:** baseline 검증은 코드 변경 전에 명시 실행해야 하며 과거 결과를 꾸며내지 않는다. 명령·도구·lock/config·선택 범위·환경 fingerprint 차이가 있으면 not_comparable 이유 표시. 테스트 pass→fail·fail→pass도 같은 test identity에서만 연결한다.
- **결과/UI:** diff, 실제 검증 차이, 실측 지표, 목적/기대효과/관찰 결과/남은 위험을 분리. AI 요약은 inference로 표시. 속도 개선은 benchmark가 없으면 주장하지 않는다.
- **완료 C07/T08:** 미측정·실패·범위 차이에서 개선을 생성하지 않음, 동일 입력 재측정 결과의 재현성 및 비교 근거 표시.

## F08 통합 알림 — N08 / U02·U06 / P08

- **입력:** review/validation/proposal/analysis adapter가 발생시킨 WorkspaceEvent. provider의 자유 문자열로 native action을 만들지 않는다. analysis 완료는 앱이 조회/추적한 원격 job의 terminal 전환을 감지하며 새 webhook을 요구하지 않는다.
- **데이터:** eventId·projectId·sourceType/sourceId/sourceRevision·type·severity·causeCode·occurredAt/receivedAt·target. notification은 UNIQUE(projectId,sourceType,sourceId,sourceRevision,type)와 readAt/version을 가진다. 동일 이벤트는 새 알림을 만들지 않고 발생 횟수·lastSeen만 갱신한다.
- **UI/동작:** 여러 프로젝트, 유형·심각도·읽음 필터, createdAt+ID cursor pagination, 읽음 CAS, 대상 project→review/file/run으로 이동. 대상 삭제·경로 이동·접근 만료는 안전한 unavailable 안내. 동일 source의 새 generation은 별도 이벤트다.
- **심각도:** deterministic test/lint failure와 AI 위험 추론의 심각도를 구분하고 cause를 표시한다. 알림은 코드 수정 승인이나 프로세스 실행 트리거가 아니다. 앱 내 inbox 필수, OS toast는 선택(Q09).
- **완료 C08/T09:** 재전달·재시작·여러 프로젝트 필터/읽음·다른 프로젝트 target 위조·삭제 대상 검증. 네트워크 단절 동안 원격 이벤트 전체를 수집했다고 주장하지 않는다.
