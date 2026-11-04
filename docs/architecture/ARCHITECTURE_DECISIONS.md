# 아키텍처 결정 기록 (3차 확정)

작성일: 2026-10-30 · 기준 커밋: `65f1b62` · 상태(2026-11-04 갱신): **확정.** AD-01~AD-22는 구조·릴리스 설계에 대한 결정이다. 4차에 해당하는 결정은 적용되었고, R1~R4에 속한 결정(AD-10 설정화, AD-16 `channel.json`, AD-17 데이터 경로 등)의 진행 상황은 [최종 아키텍처](FINAL_ARCHITECTURE.md) §6에 있다. AD-23 이후는 기능 결정이며, 항목마다 결정한 날짜를 적는다.

함께 읽기: [최종 아키텍처](FINAL_ARCHITECTURE.md) · [디렉터리 구조](FINAL_DIRECTORY_TREE.md) · [이동 계획](FINAL_MIGRATION_PLAN.md)

1·2차 문서에서 같은 대상이 여러 위치로 제안되었거나 "결정 필요"로 남은 항목을 하나씩 결정한다. 각 결정은 **번복하려면 사용자 승인이 필요**하다. 4차 작업자는 여기 없는 대안을 선택하지 않는다.

## 결정 요약

| ID | 주제 | 결정 | 근거 요약 |
|---|---|---|---|
| AD-01 | 서버 패키지 위치 | `app/` 유지, `backend/` 미도입 | import 835줄·Alembic·Celery 문자열·CI 변경 비용 대비 이득 없음 |
| AD-02 | 에이전트 분리 / `shared/` | `app/workspace` 물리 분리 안 함, `shared/` 미생성 | 경계는 테스트와 PyInstaller `excludes`로 이미 보호됨. 분리 효용 < 위험 |
| AD-03 | 공유 계약 위치 | `contracts/` 유지 | 이미 유일한 계약 위치. 코드 공유와 계약 공유를 섞지 않음 |
| AD-04 | desktop / frontend 책임 | 유지. 복사 목록은 매니페스트화(R2) | 폴더 이동 없이 새 화면마다 빌드 스크립트 수정 문제만 해소 |
| AD-05 | migrations | 루트 유지 | `alembic.ini`·`migrations/env.py`·단일 head 보존. 이력 불변 |
| AD-06 | tests 분리 | `tests/server`, `tests/frontend`, `tests/workspace`(이름 유지), 평면 이동 | 형제 import 유지로 import 수정 최소화. 개명 이득 없음 |
| AD-07 | scripts / tools | `scripts/` 폐지 → `tools/{build,validation,ops,vm}` + `ops/legacy` | 성격별 분리. 삭제 없이 이동만 |
| AD-08 | packaging / spec | `packaging/agent/`에 spec + 잠금 의존성 | 에이전트 빌드 입력을 한 폴더로 묶음 |
| AD-09 | build / dist | `build-output/` 단일 생성물 루트 | 무시·백업·외부 보관 정책을 한 곳에서 관리 |
| AD-10 | storage | `storage/repos/` 유지(Git 제외), 설정화는 R1 | 서버 런타임 데이터. 구조 변경과 무관 |
| AD-11 | outputs / reports / Claude outputs | 아래 상세 | 제품과 무관한 산출물 분리 |
| AD-12 | docs 구조 | 유지, `docs/index.md`에 새 문서 연결 | 문서 CI가 index 도달성을 검사 |
| AD-13 | 환경 변수 파일 | 개발용 `.env` 한 개만. 테스트·운영은 파일 없이 프로세스 환경변수 | 비밀 파일이 운영·클라이언트로 흘러가는 경로 차단 |
| AD-14 | 가상환경 | 서버 개발 `.venv/`, 에이전트 빌드 `build-output/venv-agent/` | 용도별 분리, 빌드 환경은 잠금 파일로만 생성 |
| AD-15 | 빌드 의존성 | Python `packaging/agent/*.lock`(해시), Node `desktop/package-lock.json`, Node `>=24.20.0 <25` | 현재 CI 방식을 로컬에도 강제 |
| AD-16 | 운영 설정 | 서버는 프로세스 환경변수만, 클라이언트는 빌드 시 `desktop/channel.json` | 비밀과 공개 설정 분리 |
| AD-17 | 사용자 데이터 경로 | 단일 루트 `%LOCALAPPDATA%\GitViz\Workspace`, Roaming 폐기 | 설정 Roaming 안(2차 미결정)을 기각: DPAPI·SQLite는 기기 전용이라 동기화 이득 없음 |
| AD-18 | 설치 경로 | `%LOCALAPPDATA%\GitVizWorkspace` 유지 | 표준 사용자 권한·Squirrel 업데이트. 데이터 경로와 별도 작업 |
| AD-19 | 서버 코드 정리 범위 | `routers/auth`→`api/auth`, 분석 계산 이동만. DB 엔진 통합·설정 전면 통합 제외 | 나머지는 동작 위험이 높고 구조 변경과 분리해야 함 |
| AD-20 | 삭제 | 4차에서 삭제하지 않음. 이동 후 한 릴리스 경과 뒤 S10 | 불확실하면 보존 |
| AD-21 | `infrastructure/` | 만들지 않음 | 파일이 compose 1개 |
| AD-22 | 4차의 성격 | 구조 변경만(동작 불변). 동작 변경은 R1~R4로 분리 | 문제 발생 시 원인 분리와 롤백 단순화 |
| AD-23 | 데스크톱 AI 흐름 (2026-11-01, 소유자 결정) | 아래 4가지로 바꾼다. 화면 버튼이 승인이고, 일회 승인 토큰과 미리보기 일치 검사는 그대로 둔다. | 클릭 수와 확인 단계 축소. 처음 쓰는 사용자도 내 키로 클라우드 AI 사용 |
| | | ① AI 전송 전 동의 체크박스·native 확인을 없앤다. | |
| | | ② 제안 적용은 &lsquo;적용&rsquo; 버튼 하나로 한다(native 확인 없음). | |
| | | ③ 클라우드 BYOK는 로그인·공개 저장소 연결 없이 DPAPI로 저장한 키로 허용한다. 에이전트 계약 `cloudContext`에 `authorize-local`을 추가했다. | |
| | | ④ 알림·계정·연결 화면은 코드를 남기고 내비게이션에서만 숨긴다. | |
| AD-34 | 공동 편집 파일 묶음·HTML 미리보기 (2026-11-04, **설계 확정·미구현**, 소유자 방향 "폴더 대신 파일을 고르면 import 관계인 파일만 공동 편집", "HTML은 분리된 CSS·JS도 작동", "상위 폴더까지 넓히기 허용", "외부 CDN 허용", "이미지·폰트 지원", "상대 경로를 화면에 보여 주는 것은 렌더러 경로 규칙의 예외", "세부 권장안대로 결정") | ① 호스트가 시작 파일을 고르면 앱 main이 참조(HTML `link`·`script`·`img`, CSS `@import`·`url()`, JS `import`)를 따라 파일 묶음을 만들고 호스트가 확인한다. 루트는 시작 파일의 폴더이고 호스트가 직접 상위 폴더로 넓힐 수 있다(Git 저장소 루트까지, 저장소 밖은 세 단계 위까지, 드라이브·사용자 폴더 금지). `/`로 시작하는 경로는 세션 루트 기준. 루트 밖·링크·숨김·`node_modules`·허용 밖 확장자는 제외. 텍스트는 공동 편집(파일당 100KB·20개·합계 1MB), 이미지·폰트는 읽기 전용(파일당 2MB·30개·합계 10MB, 형식 시그니처 확인). ② 찾지 못한 참조는 목록으로 보여 주고 호스트만 수동 추가. 편집 중 새로 생긴 참조는 호스트 승인 뒤에만 읽어 공유한다(호스트 파일 유출 방지). ③ 미리보기는 각자 앱에서 공유 문서 상태로, `gitviz-preview:` 프로토콜이 메모리 내용을 돌려준다. 별도 sandbox 뷰, preload 없음, 메모리 전용 partition. 외부 https 리소스·https/wss 연결 허용, http·사설·루프백 주소 차단, 보는 사람별 외부 연결 끄기. ④ 렌더러·참여자는 세션 루트 기준 상대 경로를 받는다(절대 경로는 계속 main에만). 동기화는 텍스트는 파일별 sync, 바이너리는 해시 확인 조각 전송. 서버는 relay 형식(`asset`·`asset-request`·`path`)과 사용자당 바이트 상한만 더하고 내용을 저장하지 않는다. 새 의존성 `parse5`·`es-module-lexer`(설치 전 승인). 상세: [설계](collab-file-set-design.md) | AD-29의 파일 하나로는 분리된 CSS·JS를 쓰는 HTML을 함께 고치거나 미리볼 수 없다. 폴더 전체보다 공유 범위가 좁다. 대가: 동적 참조는 수동 추가가 필요하고, 분석기 의존성이 늘며, 새 참조 승인 절차가 생긴다. 외부 CDN을 허용하므로 미리보기를 보는 사람의 IP가 외부 사이트에 보인다. Python(Pyodide)·C/C++·Java(설치된 컴파일러) 실행은 후속 단계 |
| AD-33 | AI 리뷰를 앱 전용 BYOK로 전환 (2026-11-04, 소유자 결정 "서버 AI 설정 폐기, 앱 설정에 저장한 내 API key로 AI 리뷰", "서버 스레드", "저장된 키는 삭제", "코드 원문 보관 안 함", "비공개 저장소는 실행마다 동의하면 허용") | ① AI 리뷰도 AI 분석(AD-32 ⑧)처럼 앱이 DPAPI로 보관한 사용자 key를 실행 요청(`POST /jobs`·`/jobs/{id}/retry`)마다 `X-GitViz-AI-Key` 헤더로 보낸다. 서버 key 저장소(`/credentials` API, 암호화 키 `AI_CREDENTIAL_*`)를 없앤다. ② 실행은 Celery worker·outbox·dispatcher 대신 요청을 받은 API 프로세스의 스레드(`job_runner.submit`, 동시 2개)에서 하고, 재시도는 같은 스레드가 같은 key로 한다. 실행자를 잃은 작업은 이어받지 않고 `INTERRUPTED`로 닫는다. 정리는 API 프로세스의 유지관리 루프가 한다. ③ 코드 원문을 저장하지 않는다(`AI_INPUT_ENCRYPTION_KEYS` 폐기). 입력 준비 때 파일별 해시만 확정하고, 파일 보기와 생성 때 고정 SHA에서 다시 가져와 대조한다(다르면 `PREVIEW_CHANGED`). ④ `AI_ENABLED`·`AI_ALLOW_EXTERNAL_TRANSFER`·서버 월 예산을 없앤다. 실행당 한도와 사용 기록만 둔다. ⑤ 비공개 저장소의 클라우드 리뷰는 사용자가 실행마다 보낼 코드를 확인하고 동의하면 허용한다(AI 분석은 AD-32 ⑤ 유지). ⑥ Alembic `39b676ca36f2`가 저장된 key·코드 원문·outbox 행을 지우고 `ai_jobs_identity` 인덱스에서 key 버전을 뺀다. 표·열은 남겨 과거 결과를 보존하고, downgrade는 거부한다 | 서버 `.env`에 AI 설정이 없어 AI 리뷰를 쓸 수 없었다. 서버가 key와 코드 원문을 갖지 않으므로 운영자가 암호화 키를 관리할 필요가 없고 DB가 유출돼도 key·코드가 없다. 대가: 웹에서는 AI 리뷰를 실행할 수 없고(웹 신규 기능 보류와 일치), 서버가 재시작되면 진행 중 리뷰는 실패로 끝나며, 실행 중에는 서버가 key를 메모리에 가진다. 코드를 다시 가져오므로 GitHub 접근이 실행 때마다 필요하고, 강제 push로 고정 SHA가 사라지면 그 입력으로는 다시 실행할 수 없다. 비용 통제는 사용자 key 계정에 맡긴다 |
| AD-32 | AI 분석(인사이트) 실행 구조 (2026-11-03, 소유자 결정 "서버에서 실행", "JSON 계획 방식", "공개 저장소만 클라우드") | ① 자동 분석과 대화형 분석은 서버에서 실행하는 서로 다른 흐름이다(⑧에 따라 API 프로세스의 백그라운드 스레드). ② 도구 호출은 공급자 고유 tool-use 대신 JSON 계획(`action: tools/answer`)으로 받아 6개 공급자를 같은 방식으로 지원한다. 도구는 스냅샷 하나에 묶인 읽기 전용 함수 18개뿐이다. ③ 결과는 별도 테이블 `ai_insight_reports`·`ai_insight_conversations`·`ai_insight_messages`(Alembic `f1b3d5e7a9c2`, 추가만, downgrade 거부)에 둔다. ④ 근거·참조는 서버가 도구 결과와 대조해 통과한 것만 저장한다. ⑤ 클라우드 모델은 공개 저장소만, 로컬 모델은 저장소 접근만 확인한다. ⑥ 월 예산 대신 실행당 호출 수로 제한한다(하루 실행 수 제한은 ⑧에서 없앴다). ⑦ (2026-11-03 추가, 소유자 결정 "코드 열람 구현") 실행마다 사용자가 켜면(`include_code`, 기본 꺼짐) 스냅샷 기준 커밋의 소스 코드를 읽기 전용 도구 3개로 읽는다. AI 리뷰와 같은 비밀·생성 파일 차단을 쓰고 실행당 양을 제한하며, 코드 인용은 실제로 읽은 줄 범위 안이어야 한다. ⑧ (2026-11-03 추가, 소유자 결정 "앱에서만, key는 앱에 저장(B안)", "AI 제한·하루 한도 없앰") 앱 전용 BYOK: 사용자의 API key는 앱이 DPAPI로 보관하고 실행 요청마다 헤더로 보낸다. 서버는 key를 저장하지 않고 그 실행의 메모리에서만 쓰므로 실행은 Celery 대신 API 프로세스 스레드(동시 2개)에서 한다. AI 분석은 서버 AI 설정 없이 기본으로 켜지고(`AI_INSIGHTS_ENABLED`), 서버 외부 전송 허용 설정 대신 사용자의 실행별 선택을 따른다. 비공개 저장소의 클라우드 전송 차단과 실행당 상한은 유지한다. `AI_DAILY_JOB_LIMIT` 기본값은 0(제한 없음) | 기존 `ai_jobs`는 코드 입력(`input_id`)·lease·비용 예약에 묶여 있어 스냅샷 기반 다회 호출에 맞지 않는다. JSON 계획은 로컬 모델(Ollama 등)에도 쓸 수 있고 서버가 실행 전 인자를 검사한다. 코드를 켜지 않으면 집계 수치·경로·기여자 이름·커밋 제목만 보낸다. 코드 열람은 수치 해석을 "왜"까지 설명하게 해 답변 품질을 높이지만 비용·노출 범위가 커지므로 실행마다 고른다. ⑧은 서버에 사용자 key와 암호화 키를 두지 않아 `.env` 설정 없이 쓸 수 있고 DB가 유출돼도 key가 없다. 대신 웹에서는 쓸 수 없고(웹 신규 기능 보류 결정과 일치), 서버가 재시작되면 진행 중 실행은 실패로 끝나며, 실행 중에는 서버가 key를 메모리에 가진다. 하루 한도가 없으므로 비용 통제는 사용자 key 계정(제공자 쪽 한도)에 맡긴다 |
| AD-31 | 저장된 공개 여부로 조회 권한 판단 (2026-11-03, 소유자 결정) | 공개로 저장된(`repositories.is_private = false`) 저장소의 **조회**는 GitHub에 다시 묻지 않고 저장값을 믿는다(`verify_repository_access`의 `live=False`). 비공개·미확인(`NULL`) 저장소, 쓰기(재분석), 그리고 `live=True` 호출(분석 작업, 서버 AI, 앱 신원 증명)은 지금처럼 매번 GitHub로 확인한다. 공개 여부는 등록·재분석·분석 작업 때 갱신된다 | 조회마다 GitHub를 호출해 장애·호출 한도 때 저장된 결과도 못 보고 홈 목록 전체가 실패했다. 대가: GitHub에서 비공개로 바뀐 저장소는 다음 갱신 전까지 공개로 취급된다 |
| AD-30 | 로컬 스테이징 앱의 서버 인증서 (2026-11-02, 소유자 결정 "추천 방법으로 진행") | `bootstrap -App -Staging`으로 빌드한 앱에는 서버 주소(`https://localhost:8443`)와 스테이징 서버의 자체 서명 인증서가 들어간다(`GITVIZ_BUILD_SERVER_CA` → `dist/config/server.json`의 `certificate`). main 프로세스는 시작할 때 이 인증서를 기본 신뢰 목록에 더한다(`tls.setDefaultCACertificates`). 빌드와 실행 모두 서버 주소가 loopback(`localhost`·`127.0.0.1`·`[::1]`)이고 인증서가 그 호스트를 가리킬 때만 받아들여, 실제 서버 주소를 넣는 배포 빌드에는 인증서가 들어갈 수 없다. 실행 중 환경변수로는 바꿀 수 없다. 인증서는 14일짜리로 만료 2일 전부터 `Prepare`가 새로 만들고, 그러면 앱을 다시 빌드해야 한다(`bootstrap -Staging`이 알려 준다) | 탐색기에서 더블클릭해도 로그인·커뮤니티가 된다. 빌드한 PC의 서버에만 연결되므로 다른 PC는 clone 후 그 PC에서 빌드한다 |
| AD-29 | 커뮤니티 실시간 공동 편집 (2026-11-02, 소유자 결정 "웹은 구현하지 말고 앱만, 앱 안에 편집기 + Yjs", "호스트 PC의 로컬 파일", "3명까지") | 앱 커뮤니티 화면의 &lsquo;공동 편집&rsquo; 탭(웹에는 없음). 호스트가 native 대화상자로 자기 PC의 파일 하나(100KB 이하 UTF-8 텍스트, 심볼릭 링크 거부)를 고르면 호스트 포함 3명까지 함께 고친다. 편집기는 CodeMirror 6, 동시 편집은 Yjs(CRDT)로 앱 전용 번들(`dist/ui/web/js/collab-editor.bundle.js`)에 묶는다. 파일 경로는 main 프로세스에만 있고 화면은 불투명 핸들만 받는다(`desktop:collab-open|save|close`, 커뮤니티 페이지에서만). 저장은 호스트만, 호스트가 누를 때만 하며 열 때의 해시와 디스크가 다르면 native 확인 후에만 덮어쓴다. 줄바꿈(CRLF)·BOM은 저장 때 원래대로 돌린다. 서버는 자리와 Yjs 메시지 전달만 한다: `GET·POST …/collab`, `POST …/collab/{id}/join|heartbeat|leave|relay`. 자리는 음성과 같은 10초 하트비트·30초 만료, 호스트가 나가거나 만료되면 세션이 끝난다. 변경분·동기화·커서(awareness)는 채팅 스트림의 `collab` 이벤트로 그 세션에 앉은 사람에게만 가고, `collab-sessions` 이벤트로 목록이 바뀐다. 파일 내용은 서버에 저장하지 않는다. 세션 정보는 Redis(없으면 프로세스 메모리), DB 변경 없음. 새 의존성: desktop devDependencies `yjs`·`y-protocols`·`y-codemirror.next`·`codemirror`·`@codemirror/*`(state·view·commands·언어 6종)·`esbuild`(정확한 버전 고정, 번들로만 들어감) | 파일을 서버에 올리지 않아 저장소 권한·보관 문제가 없다. CRDT라 서버가 병합 규칙을 몰라도 되고, 3명 상한이면 스트림 중계로 충분하다 |
| AD-28 | 커뮤니티 음성 채팅 (2026-11-01, 소유자 결정 "한 번에 3명까지") | 커뮤니티마다 음성방 하나, 동시에 3명까지(사람당 자리 1개, 다시 들어오면 이전 자리를 바꾼다). 소리는 참여자끼리 WebRTC로 직접 주고받고(3명이라 서로 모두 연결), 서버는 자리와 연결 신호만 다룬다: `POST …/voice/join|heartbeat|leave|signal`, `GET …/voice`. 자리는 10초마다 하트비트, 30초 지나면 만료. 신호(offer/answer/ICE)는 채팅 스트림의 `voice-signal` 이벤트로 받는 사람 한 명에게만 간다. 자리 정보는 Redis(없으면 프로세스 메모리), DB 변경 없음. STUN/TURN은 배포 환경변수 `VOICE_STUN_URLS`·`VOICE_TURN_URLS`·`VOICE_TURN_SECRET`(coturn 단기 자격 증명, 비밀값은 서버에만)로 정하고 비우면 같은 네트워크 안에서만 연결된다. 2026-11-02 소유자 결정 "공개 STUN 사용": 예시 설정(`.env.example`)과 스테이징 기본값은 Google 공개 STUN(`stun:stun.l.google.com:19302`, `stun1`)이다. STUN 제공자에게는 참여자의 공인 IP 주소만 보이고 음성은 가지 않는다. TURN은 아직 없다. 앱은 커뮤니티 화면 최상위 프레임에만 마이크(오디오만) 권한을 주고 카메라·화면 공유 등은 계속 거부한다. 채팅 탭을 떠나면 음성에서도 나간다. 새 의존성 없음 | 서버가 음성을 중계하지 않아 서버 부하·비용이 없다. 3명 상한이면 직접 연결(메시)로 충분하다 |
| AD-27 | 커뮤니티 실시간 단체 채팅 (2026-11-01, 소유자 결정 "먼저 실시간 단체 채팅") | 커뮤니티(저장소)마다 채팅방 하나. 기록은 PostgreSQL `community_chat_messages`(Alembic `d4f6a8c0e2b3`, 추가만), 보내기·지우기·이전 기록은 일반 HTTP 요청(웹 CSRF, 앱 허용 목록 `communityChatMessages`·`sendCommunityChat`·`deleteCommunityChat`), 받기는 서버 전송 이벤트(SSE) `GET /api/community/v1/repositories/{id}/chat/stream`. 프로세스 사이 전달은 Redis pub/sub(`REDIS_URL`, 없으면 한 프로세스 메모리 버스), 접속 중 표시는 연결마다 45초 만료 항목. 앱은 main(`community-live.ts`)이 로그인 토큰으로 스트림을 받아 재연결(1–30초 지연)하고 화면에는 이벤트 객체만 IPC로 넘긴다(토큰은 화면에 안 감). 웹은 같은 출처 EventSource. 메시지는 일반 텍스트 2,000자, 30초에 20개 제한, 작성자·관리자만 지움(소프트 삭제). 내보낸 멤버의 열린 스트림은 즉시 끝난다. 새 의존성 없음(웹소켓 대신 SSE) | 기여자끼리 바로 대화. 서버에 웹소켓 라이브러리가 없어 표준 HTTP 스트림으로 구현하고, 보내기는 기존 인증·CSRF·제한 경로를 그대로 쓴다 |
| AD-26 | 초대 링크로 앱 열기 (2026-11-01, 소유자 결정) | 전용 스킴 `gitviz-join://invite/<토큰>`만 등록한다(내부 `gitviz://`는 OS에 등록하지 않음). 토큰 하나만 읽고 다른 경로·쿼리가 있으면 버린다. 설치본(Squirrel `app-x.y.z`)에서만 HKCU에 등록하고, 대상은 버전 폴더 밖 실행기이며 링크 앞에 `--`를 둬 스위치로 해석되지 않게 한다. 제거(`--squirrel-uninstall`) 때 등록을 지운다. 앱은 커뮤니티 화면에 초대를 채워 두기만 하고 참여는 사용자가 누른다. 브라우저의 초대 페이지에는 'GitViz 앱에서 열기' 버튼을 둔다 | 붙여넣기 없이 클릭 한 번으로 앱에서 참여. 링크만으로 몰래 참여시키거나 앱의 다른 화면을 여는 일은 막는다 |
| AD-25 | 프로젝트 커뮤니티 1단계 (2026-11-01, 소유자 결정) | 분석한 저장소마다 커뮤니티 하나. 기여자(`contributors.github_login` 일치)는 자동 참여, 그 밖의 사람은 URL 초대 링크로만 참여. 저장소를 처음 분석한 사람(`analysis_jobs.triggered_by_user_id` 가장 이른 것)이 관리자이고, 활동 중인 멤버에게 관리자를 넘길 수 있다(커뮤니티당 관리자 1명, 부분 고유 인덱스). 초대 토큰은 해시만 저장하고 링크의 `#` 뒤에 실어 서버 로그에 남지 않게 한다. 글·댓글은 일반 텍스트, 소프트 삭제, 1분에 10개 제한. 내보낸 멤버는 기여자여도·초대 링크가 있어도 관리자가 복원해야 돌아온다. API `/api/community/v1`, 화면 `pages/community.html`(웹·앱 공용), Alembic `c3e5a7b9d1f2`(추가만), 데스크톱 본문 계약 `contracts/desktop/v1/community-bodies.json`. 실시간 채팅·음성은 2·3단계 | 기여자끼리의 소통 공간. 비공개 저장소도 관리자가 초대하면 참여 가능(분석 결과 열람 권한은 기존 저장소 권한 검사를 그대로 따른다) |
| AD-24 | 데스크톱 로그인 (2026-11-01, 소유자 결정) | 확인 코드 입력·승인 버튼을 없애고 RFC 8252 루프백으로 바꾼다. 앱이 `127.0.0.1` 임의 포트에서 한 번만 받는 리스너를 열고 `redirectPort`를 보낸다. GitHub 로그인이 끝나면 서버가 브라우저를 `http://127.0.0.1:<port>/callback?flow&code`로 보낸다. `exchange`는 `callbackCode`(flowId의 HMAC)가 맞을 때만 세션을 낸다. 승인 POST는 없앴다. | 클릭 한 번 로그인. 전달된 링크는 받은 사람 PC의 루프백으로 돌아가므로 제3자는 코드를 얻지 못한다 |

## 상세

### AD-01 · AD-02 에이전트와 서버
1차는 "에이전트 분리(Phase C)"를 보류 기본으로 두었다. 확정: **분리하지 않는다.**
- 경계는 `tests/workspace/test_import_boundary.py`와 spec의 `excludes`로 이미 검증된다.
- 분리하면 `workspace_agent.spec`의 `hiddenimports`/`datas`, 서버의 `app.services.ai.*` import, pyright/ruff 대상, CI 경로 필터, 에이전트 테스트 import 전체가 동시에 바뀌며 PyInstaller·설치·VM 검증을 다시 해야 한다.
- 공유 7개 모듈은 서버 안에 두고 에이전트가 정적 3 + `importlib` 동적 5로 사용한다. 이 관계는 유지한다.
- 재검토 조건: 에이전트를 서버와 다른 저장소/릴리스 주기로 분리해야 할 때.

### AD-06 tests 구조
`scripts/test_*.py`는 `import ai_test_support as support`처럼 **같은 폴더의 형제를 import**한다. 하위 폴더로 쪼개면 import를 모두 고쳐야 하므로 `tests/server/`에 **평면으로** 옮긴다(루트 계산 `parents[1]`→`parents[2]`만 수정). `tests/workspace`는 모듈 이름 `tests.workspace.*` 절대 import가 있고 `app/workspace`와 짝이므로 이름을 바꾸지 않는다(1차 제안 `tests/agent`는 철회).

### AD-07 scripts 분배 기준
| 성격 | 위치 |
|---|---|
| 자동 테스트, 그 지원 모듈과 픽스처, 지원 모듈을 import하는 통합 검증(`validation_reports`·`verify_ai_mvp_integration`·`verify_live_repository`·`verify_production_integration`·`verify_snapshot_integration`·`serve_readonly_validation`·`evaluate_ai_review`) | `tests/server` (서로 형제 import) |
| Node 테스트 4개 | `tests/frontend` |
| PyInstaller 빌드·감사 | `tools/build` |
| Windows 게이트와 단계별 패키지 검증, AI 목업 서버 | `tools/validation` |
| 수동 운영·백필·정리·점검 도구, `ai_ops.py` | `tools/ops` |
| 일회성 마이그레이션·검증 | `tools/ops/legacy` |
| Hyper-V·스테이징 | `tools/vm` |

`ai_ops.py`는 `test_ai_ops.py`가 import하지만 `docs/operations/ai.md`와 `docker-compose.yml` 주석(점검 명령 안내)이 운영 점검 도구로 지칭하므로 `tools/ops`에 둔다. 이동 시 테스트의 import 경로(`sys.path`)와 문서·주석을 같은 커밋에서 고친다.

### AD-08 · AD-15 packaging
`workspace_agent.spec`은 `root = Path(SPECPATH)`를 저장소 루트로 가정한다. `packaging/agent/`로 옮기면 `root = Path(SPECPATH).parents[1]`로 바꾸고, 생성되는 진입 shim(`build/workspace-agent/entry.py`)은 `build-output/agent/work/entry.py`로 옮긴다. 잠금 파일(`requirements-workspace.lock`)은 해시 고정이며 내용을 바꾸지 않는다(파일 이동만; 경로가 `-r` 상대 참조인 `requirements-workspace-build.txt`는 같은 폴더로 같이 이동하므로 유효).

### AD-09 build-output
- 에이전트: `build-output/agent/{work,dist}` (`dist/agent`가 `extraResource`).
- 데스크톱: forge `outDir` = `build-output/desktop`. 검증용 변형 패키지도 같은 곳.
- 이전 빌드(`desktop/out/make-0.7.0-*` 4개 약 4 GB)는 `build-output/archive/`로 옮기거나 저장소 밖으로 보관한다 (복구 불가 작업이므로 별도 승인, S9에 포함하지 않음).
- `tests/workspace/test_p06_migration.py`·`test_p07_migration.py`는 `desktop/out/GitVizWorkspaceP0xVerify-win32-x64`를 하드코딩하므로 같은 커밋에서 갱신한다.

### AD-11 비코드 산출물
| 대상 | 결정 |
|---|---|
| `outputs/` | 이미 Git 제외. 위치 유지, 저장소 밖 이전은 사용자 몫 |
| `Claude outputs/` | `.gitignore`에 추가하고 추적 중인 1개는 `git rm --cached`(파일은 디스크에 유지). **커밋하지 않는다는 기존 원칙과 일치** |
| `reports/`, `test-react/` | 빈 폴더. 삭제 후보(S10), 영향 없음 |
| `certs/` | Git 제외 유지. 내용은 열지 않음 |
| `.validation/` | 유지. 58 GB 외부 이전은 별도 승인 작업 |

### AD-13 · AD-16 설정
환경별 상세는 [환경 매트릭스](../deployment/FINAL_ENVIRONMENT_MATRIX.md). 핵심: `load_dotenv`는 이미 설정된 환경변수를 덮어쓰지 않으므로 우선순위는 *프로세스 환경 > `.env` > 코드 기본값*이다. `GITVIZ_ENV=production`에서는 `.env`를 읽지 않는다(R1). 클라이언트의 `GITVIZ_SERVER_ORIGIN` 환경변수 덮어쓰기는 `app.isPackaged === false`일 때만 허용한다(R1).

### AD-17 사용자 데이터
2차 문서의 "설정은 Roaming?" 미결정을 닫는다. **전부 Local.** DB와 키가 기기 고정(DPAPI)이므로 설정만 로밍하면 다른 PC에서 키 없는 반쪽 상태가 된다. 데이터 루트는 설치 루트와 **형제 폴더**(`GitViz\Workspace`)여야 한다: 제거 정리(`cleanupPlan`)가 `GitVizWorkspace` 루트를 통째로 삭제하기 때문이다. 이전은 R3, 방법은 [릴리스 이행 계획](../deployment/RELEASE_MIGRATION_PLAN.md).

### AD-19 서버 코드 정리 범위
포함: `app/routers/auth.py`→`app/api/auth.py`(import 변경 5개 모듈 + 테스트/문서), `app/api/analytics.py`의 `_coupled_files_for`·`COUPLED_SORT_KEYS`를 서비스 모듈로 이동(역방향 의존 제거, 스냅샷 해시 회귀 테스트로 보호).
제외(후속, 4차 아님): 동기/비동기 DB 엔진 통합(`Base` metadata·Alembic autogenerate·프로덕션 스키마 영향), `os.getenv` 전면 통합, 로깅 전면 도입. `GITVIZ_ENV` 가드는 R1에서 최소 형태로만 도입한다.

### AD-22 구조 변경과 동작 변경의 분리
4차(S0~S9)는 **사용자가 보는 동작과 설치 산출물 내용이 같아야** 한다. 판정: 이동 전후 `app.asar` 항목 목록·`resources\agent` 구성·마이그레이션 head·테스트 결과가 동일. 동작이 바뀌는 항목(환경 가드, 채널 설정, asar 허용 목록, 데이터 경로 이전)은 릴리스 단위(R1~R4)로 분리해 롤백 단위를 작게 유지한다.
