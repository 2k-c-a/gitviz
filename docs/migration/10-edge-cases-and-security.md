# 예외·위협·복구 설계

목적: Windows 앱의 권한 확대와 개발 작업의 실패를 검토한다. 근거: [01 O06~O18](01-repository-analysis.md), [현 정책 충돌](../decisions/index.md). 관련: E01~E10·N01~N08·P01~P08. [05 기능](05-functional-specification.md), [06 복구 모델](06-data-model.md), [12 테스트](12-testing-strategy.md)에 연결한다. 동일 사용자 악성 프로세스의 완전 격리·기업 관리 요구는 Q10, 데이터 보호·외부 전송 정책은 Q04/Q08이 미정이다.

## 보안 요구사항

| ID | 기준·구현 책임 | 검증 |
|---|---|---|
| S01 | project grant·실제 경로/handle identity·재검증·worktree metadata 분리, main+agent | T01/T02/T04/T05/T06 |
| S02 | 명시 project·동일 project FK·현재 원격 권한·계정별 cache/token, main+store+server | T02/T09/T11/T14 |
| S03 | 사용자 승인·대상/원본 hash·기한·단일 capability·멱등·journal, main+agent | T04/T06/T08/T12 |
| S04 | untrusted source·미리보기·동의 bytes·비밀 제외·근거/추론 구분, AI/impact | T03/T07/T08 |
| S05 | 고정 executable/argv·cwd·신뢰·config hash·timeout/output/자식 제한, runner/editor | T05/T06 |
| S06 | OS SecretStore·payload 암호화·ACL·비식별 로그·보존, main+store | T03/T09/T10/T12 |
| S07 | sandbox renderer·context isolation·sender 검증·CSP·원격 bridge 금지, desktop | T10 |
| S08 | 서명·feed·배포/호환성·backup·원복 게이트, release | T13/T15 |

## Electron 특화 위협

remote README·commit message·AI markdown·테스트 출력·파일명이 XSS를 유발하면 native 권한 탈취로 커질 수 있다. `nodeIntegration=false`, `contextIsolation=true`, `sandbox=true`를 명시하고 preload에 fs/process·raw ipcRenderer·generic invoke를 노출하지 않는다. sender의 webContents·main frame·등록된 app origin을 매 요청 확인하고 subframe·다른 창·navigation 이후 요청을 거부한다.

번들 UI는 등록된 secure app origin에서 제공하고 path traversal을 차단한다. CSP는 self 중심으로 CDN script/font를 로컬 자산화하고 inline handler는 nonce/hash/외부 handler로 단계 정리한다. eval·webSecurity 비활성화·임의 window.open·webview·외부 protocol 실행을 허용하지 않는다. remote API의 문자열을 innerHTML로 직접 넣지 않으며 기존 동적 HTML 지점도 실제 DOM E2E·XSS fixture로 검사한다. 외부 링크는 HTTPS allowlist와 사용자 클릭으로만 시스템 browser에 연다.

agent는 상속 pipe로만 통신하고 raw stdio 메시지를 renderer에 전달하지 않는다. 대용량 JSON·깊이·chunk·filename·출력 flood를 제한한다. 같은 사용자/관리자가 프로세스를 침해한 경우의 비밀 보호를 완전 보장하지 않는다. Electron·Chromium·Python·Git·native helper·npm/Python 의존성은 lock·라이선스/SBOM·취약점 점검과 보안 업데이트 계획이 필요하다.

## 실패·복구 행렬

| 상황 | 감지·사용자 표시 | 데이터/복구 규칙 | 테스트 |
|---|---|---|---|
| 잘못된 입력·경로·다른 project 리소스 | INVALID_REQUEST/PATH_OUTSIDE_SCOPE/RESOURCE_NOT_FOUND | 실행/쓰기/전송 전 거부, 존재 정보 최소화 | T01/T10 |
| 중복 mutation·응답 유실 | local_operation/bodyHash·동일 key | 같은 결과 재조회, 다른 body 409 상당, tombstone 유지 | T03/T04/T06 |
| Git 미설치·비repo·unborn·detached·conflict | typed 상태·기능별 안내 | clone/reset/분쟁 해결 자동 실행 금지 | T02 |
| 작업 중 파일/index/설정 변경 | pre/post hash·file identity·config hash | snapshot/approval 무효화, 재조회/재승인 | T02/T04/T06 |
| 파일 잠금·ACL·디스크 부족 | ACCESS_DENIED/STORAGE_UNAVAILABLE | 쓰기 전 journal 기록 실패면 적용 안 함, partial이면 복구 필요 | T04/T12 |
| 앱/agent 강제 종료 | durable operation/job/journal 상태 | validation interrupted, proposal recovery_required, provider uncertain | T12 |
| patch 일부만 적용 | 파일별 journal·target hash | 현재 target과 일치하는 파일만 명시 승인 원복, 외부 수정 보존 | T04/T12 |
| 네트워크 단절·timeout | remote offline/provider unavailable 구분 | 로컬 계속, 송신/명령 자동 재개 금지 | T03/T14 |
| AI 결과 불명·중복 비용 위험 | attempt 시작/요청 ID·완료 미확인 | uncertain 예약 유지·자동 retry 금지·사용자 상태 확인 | T03/T12 |
| 잘못된/잘린 모델 출력·주입 문구 | schema/line/quote 검사 | 코드 적용 불가, 실패 상태·미검증 범위 표시 | T03/T04 |
| rate limit·GitHub 접근 확인 실패 | Retry-After·권한 미확인 | 제한 backoff, 권한 성공으로 간주 금지 | T11/T14 |
| 인증 만료·계정 변경 | bearer 401·capability 변경 | 원격 요청 중지·재로그인, private cache 제거·local 유지 | T11/T14 |
| DB migration 실패/새 schema | schema range·transaction 결과 | backup 보존·쓰기 차단·복구 안내, 운영DB downgrade 금지 | T12/T13 |
| 업데이트 중단·위조 package | hash·서명·publisher·버전 검사 | 설치 중지·기존 binary 유지·DB 호환 확인 | T13 |
| 알림 대상 삭제/이동·재전달 | target FK·dedupe key·scope | unavailable 안내, 중복 upsert, 명령 실행 없음 | T09 |
| 웹·앱 동시 편집/피드백 | expectedVersion·서버 권한·snapshot | 409 갱신, last-write-wins로 덮어쓰기 금지 | T14/T15 |
| 일부 기능만 실패 | component health·typed error | 차트/원격 실패가 local Git를 막지 않음, 데이터 없음과 오류 구별 | T14 |

## 외부 전송·권한·비밀

현재 cloud 정책은 공개 저장소만 허용한다. local 변경의 private/unknown/unmapped 외부 전송은 Q04 결정 전 차단한다. 로컬 모델 사용은 별개이며 '로컬'이 서버 PC인지 사용자 PC인지 executionLocation을 명확히 표시한다. 미리보기와 consent hash가 다른 payload는 전송하지 않는다. 보낸 맥락 줄·파일명·사용자 instruction도 전송 범위에 포함하여 설명한다.

원문·patch·테스트 출력·메모·key는 일반 로그에서 제외하고 source를 제외했다고 로그 전체가 안전하다고 가정하지 않는다. 비식별 진단도 사용자 preview·동의 후 내보낸다. cloud BYOK는 사용자 PC에서 재등록하며 서버 저장 key 복호화/export API를 만들지 않는다.

현재 [공유 GET](../../app/api/ai/shared.py)은 단순 '팀원 전용'으로 표현할 수 없다. 원격 기존 동작을 자동 변경하지 않으며 노출 안내와 제품 결정 D01/Q05를 출시 게이트로 둔다. 로컬 결과는 별도 저장이므로 기존 shared 경로에 신규 자료가 유입되지 않게 한다.

서버 OAuth의 client secret·암호화 key는 앱에 넣지 않는다. 인증 handoff의 challenge·pollSecret·browser binding·한 번 소비·짧은 TTL·CSRF를 함께 검증한다. UI에서 인증 실패와 네트워크 실패를 구분하고 불명 상태에서 로그인 계정이나 GitHub 접근을 추측하지 않는다.
