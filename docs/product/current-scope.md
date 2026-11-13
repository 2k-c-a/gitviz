# 현재 제품 범위

검토일: 2026-11-02 · 코드 기준: `bdbf01c` (브랜치 `feature/desktop-community`) · [문서 안내](../index.md)

기본 사용 방식은 Windows 데스크톱 앱이다(2026-11-01 결정: 웹은 신규 기능 보류, 기존 화면은 유지). 앱은 같은 서버에 접속하는 클라이언트이며, 내 PC의 Git 저장소를 다루는 로컬 기능을 더한다. 실행 방법은 [README](../../README.md#데스크톱-앱-실행-windows).

## 확인한 구현 — 데스크톱 앱

| 영역 | 현재 코드의 동작 | 근거 |
|---|---|---|
| 로컬 프로젝트 | 폴더 선택으로 등록, 브랜치·staged/unstaged·최근 커밋 읽기 전용 표시, 변경 고정. "파일" 탭(2026-11-05, 이전 "변경" 탭)은 변경 목록 아래에 프로젝트 파일 트리를 보여 준다. 트리는 폴더를 펼칠 때 한 단계씩 읽고(폴더당 1,000개, Git 무시 항목·`.git` 숨김, 내용은 읽지 않음), 파일 이름을 누르면 설정의 기본 에디터(VS Code·Cursor)로 열린다. 변경이 없는 파일도 트리에서 골라 파일 전체를 담고 AI 리뷰·리팩토링·테스트·영향 분석에 쓸 수 있다(`selected` 입력). "변경 분석" 탭(2026-11-07)은 현재 브랜치의 최근 180일 Git 이력에서 자주 수정된 파일(변경 집중도 포함)·같이 바뀐 파일·주별 타임라인을 서버 분석과 같은 기준(`metric_policy.py`, 현재 트리, 대형 커밋 제외)으로 계산한다. 에이전트 operation `workspace.history.analyze`가 커밋 메타데이터와 numstat만 읽고 저장·전송하지 않는다. 같은 날 테스트·린트·전후 비교·영향 범위 탭은 "검증" 탭 하나의 세 구역으로 합쳤다 | [projects](../../desktop/src/projects.ts), [에이전트](../../app/workspace/projects.py), [화면](../../frontend/js/workspace/changes.js) |
| 커밋 전 AI 리뷰·리팩터링 | 로컬 모델 또는 DPAPI로 저장한 내 키(BYOK)로 리뷰, 제안 → ‘적용’ 버튼 한 번으로 해시 확인 후 적용, 영향 분석, 테스트·린트 전후 비교. 전송 동의 체크박스·native 확인은 없앴고 화면 버튼이 승인이다([AD-23](../architecture/ARCHITECTURE_DECISIONS.md)) | [proposals](../../desktop/src/proposals.ts), [cloud-review](../../desktop/src/cloud-review.ts) |
| 로그인 | 시스템 브라우저 GitHub 로그인 → 루프백(`127.0.0.1` 임의 포트)으로 돌아와 앱 전용 bearer 세션. 확인 코드·승인 버튼 없음([AD-24](../architecture/ARCHITECTURE_DECISIONS.md)) | [auth.ts](../../desktop/src/auth.ts), [서버](../../app/api/desktop/auth.py) |
| 서버 기능 | 저장소 검색(페이지 이동)·분석·결과 화면을 서버 API로 사용. 왼쪽 메뉴(2026-11-08): "내 PC"(프로젝트·알림, 앱에서만) → "GitHub 저장소"(내 분석·새 분석·커뮤니티) → 설정. "새 분석"은 검색어와 GitHub 주소를 함께 받는다(주소면 바로 분석, 이미 분석한 저장소면 결과로 이동). 내 분석의 "내 GitHub 저장소" 탭(로그인 사용자의 토큰으로 github.com/repos와 같은 범위: 소유·협업자·소속 조직, 2026-11-04). 원격 분석 연결·알림함 | [remote-contracts](../../desktop/src/remote-contracts.ts) |
| 공동 편집 | 호스트가 고른 시작 파일과 그 파일이 불러오는 HTML·CSS·JS(이미지·글꼴은 읽기 전용)를 3명까지 실시간 편집(CodeMirror 6 + Yjs). 편집 중 새 참조는 호스트 승인 후 공유, 호스트는 새 파일 추가 가능(저장 때 생성), HTML은 앱 안 격리된 창에서 미리보기. Python(앱 안 Pyodide)·C/C++·Java(내 PC 컴파일러, 자원 제한)는 누구나 자기 앱에서 실행하고 결과는 공유하지 않는다. 저장은 호스트만, 서버는 전달만([AD-29](../architecture/ARCHITECTURE_DECISIONS.md), [AD-34](../architecture/collab-file-set-design.md)) | [collab-files](../../desktop/src/collab-files.ts), [화면](../../frontend/js/community.js) |
| 배포 | 서명 없는 설치 파일(Squirrel, 사용자별 설치). 앱 버전은 1.0.0(2026-11-13, [GitHub Releases](https://github.com/2k-c-a/gitviz/releases)로 배포. 2026-11-04부터 앱·에이전트 통일, [빌드와 릴리스](../deployment/BUILD_AND_RELEASE.md)), 배포 판정 문서는 0.7.0 기준이다. 서명 확인 후 수동 업데이트. 로컬 스테이징 빌드는 서버 주소·인증서를 넣어 탐색기 더블클릭으로 동작([AD-30](../architecture/ARCHITECTURE_DECISIONS.md)) | [설치·업데이트](../operations/windows-desktop.md) |

## 확인한 구현 — 커뮤니티 (웹·앱 공용 화면, 공동 편집은 앱 전용)

| 영역 | 현재 코드의 동작 | 근거 |
|---|---|---|
| 커뮤니티·멤버 | 분석한 저장소마다 하나. 기여자는 자동 참여, 그 밖은 초대 링크(`gitviz-join://` 앱 열기 포함). 처음 분석한 사람이 관리자, 넘기기·내보내기 가능 | [API](../../app/api/community.py), [AD-25·26](../architecture/ARCHITECTURE_DECISIONS.md) |
| 글·댓글 | 일반 텍스트, 소프트 삭제, 1분 10개 제한 | [서비스](../../app/services/community.py) |
| 일정 | 커뮤니티 "일정" 탭(글 다음, 2026-11-07). 월간(기본)·주간·일간 캘린더, 유형(회의·일반·개발 작업·코드 리뷰·스프린트·마일스톤·배포 및 릴리즈)·프로젝트(이 저장소/내 모든 커뮤니티)·담당자 필터, 제목·설명 검색, 담당자·참여자(전원 선택, 최대 30명)·참석 응답, 반복(매일·매주·매월), 커뮤니티 공개/참여자만 공개. 개발 연동은 수집된 PR(리뷰어 포함)·릴리스와 스프린트·마일스톤 일정을 쓰고, 이슈는 번호와 GitHub 링크만 둔다. 첨부는 링크만, 일정 시작 전 알림은 일정 화면을 연 동안의 화면 안 알림뿐이다. 나를 참여자로 넣은 일정은 앱이 Windows 알림으로 알린다([AD-36](../architecture/ARCHITECTURE_DECISIONS.md)). 의견은 연결한 커뮤니티 글의 댓글로 나눈다([AD-35](../architecture/ARCHITECTURE_DECISIONS.md)) | [서비스](../../app/services/community_events.py) |
| 실시간 채팅 | 기록은 PostgreSQL, 받기는 SSE, 프로세스 간 Redis pub/sub([AD-27](../architecture/ARCHITECTURE_DECISIONS.md)) | [채팅](../../app/services/community_chat.py) |
| 커뮤니티 알림(앱) | 2026-11-11. 앱이 어느 화면에 있든 30초마다 확인해 새 채팅(커뮤니티별로 묶음, 보고 있는 채팅방은 제외)과 일정 초대를 Windows 알림으로 띄우고, 누르면 그 탭을 연다. 왼쪽 "커뮤니티" 메뉴에 안 읽은 수 배지. 채팅 화면의 "채팅 알림 끄기"는 그 커뮤니티 채팅만 끈다([AD-36](../architecture/ARCHITECTURE_DECISIONS.md)) | [서비스](../../app/services/community_notifications.py), [앱](../../desktop/src/community-notifications.ts) |
| 음성 채팅 | 3명까지 WebRTC 메시. 서버는 자리·신호만. 공개 STUN 기본([AD-28](../architecture/ARCHITECTURE_DECISIONS.md)). 커뮤니티를 고르면 화면 왼쪽에 떠 있고, 글·채팅·공동 편집·멤버 탭을 바꿔도 끊기지 않는다(2026-11-05: 실시간 연결을 커뮤니티당 하나로 묶어 "접속 중"에는 그 커뮤니티를 연 사람이 모두 보인다). 내 커뮤니티 목록은 위 왼쪽, 초대 링크로 참여는 위 오른쪽이고 좁은 화면에서는 아래로 쌓인다 | [음성](../../app/services/community_voice.py) |

## 확인한 구현 — 서버 분석·AI

| 영역 | 현재 코드의 동작 | 근거 |
|---|---|---|
| 저장소 분석 | GitHub 이력 수집, 고정 SHA·기간·계산 버전의 불변 스냅샷 저장, 이력·비교 조회 | [분석 워커](../../app/tasks/analyze_repository.py), [스냅샷 서비스](../../app/services/analysis_snapshots.py), [비교 API](../../app/api/snapshots.py) |
| 분석 지표 | 변경 집중도(계산 이름 유지보수 점검 우선순위, 2026-11-11 화면 표기 변경), BF80, 기여 분포, 공동 변경, 협업 관계. "파일 분석" 화면(2026-11-11)은 공동 변경·기여 집중도 보기를 빼고, 디렉토리 트리를 탐색기형 들여쓰기 트리로 보여 준다(열: 파일·변경 집중 파일·변경 집중도·수정량·버그 수정·주요 수정자, 변경 집중도 순·이름순 정렬, 검색, 이 폴더만 보기). 공동 변경·기여 분포 값은 스냅샷과 AI 분석에 그대로 남아 있다 | [지표 정의](../contracts/metrics.md), [분석 계약](../contracts/analytics.md) |
| AI 입력 | PR 또는 커밋을 선택하고 SHA·선택 파일·전송 자료를 고정 | [입력 서비스](../../app/services/ai/input_builder.py), [수집 계약](../contracts/collection.md) |
| AI 생성 | summary/review 독립 실행, 전송·로컬 실행 확인, 결과·피드백·사용량 저장 | [생성 서비스](../../app/services/ai/generation_service.py), [AI API](../contracts/ai-api.md) |
| AI 분석(인사이트) | 분석 개요의 [AI 분석] 버튼 → 자동 분석 리포트(6개 범주 카드)와 대화형 분석. 저장된 스냅샷을 읽기 전용 도구로 조회하고 근거를 서버가 검증한다. 실행마다 켜면 기준 커밋의 소스 코드도 읽고 줄 단위로 인용한다. 앱 전용이고, 앱 설정에 저장한 사용자 API key로 실행하며 서버는 key를 저장하지 않는다. 서버 `.env`의 AI 설정 없이 동작하고 하루 실행 한도가 없다([AD-32](../architecture/ARCHITECTURE_DECISIONS.md)). 실제 모델 품질은 시험하지 않았다(가짜 제공자로만 통합 시험). 자동 분석 화면(2026-11-10)은 위험 수준별 개수·범주 이동·먼저 볼 인사이트를 맨 위에 두고, 범주 안을 위험 수준 순으로 정렬하며 위험 수준 필터를 둔다. 완료된 자동 분석은 HTML(인쇄해 PDF로 저장 가능)·Markdown 보고서로 내려받는다. 보고서는 저장된 결과만 옮기고 외부 자원·스크립트가 없는 단일 파일이다 | [인사이트 서비스](../../app/services/ai/insight_service.py), [AI API](../contracts/ai-api.md) |
| S11 | 전체 변경 파일의 추가 검토 후보. 코드 지적과 별개인 스냅샷 근거 | [이력 서비스](../../app/services/ai/history_context.py) |
| 제공자 | Anthropic·OpenAI·Gemini BYOK 및 같은 PC의 Ollama·LM Studio·KoboldCpp | [현재 profile 안내](../operations/providers.md) |
| 결과 공유 | 현재 저장소 접근 권한으로 다른 작성자의 결과·판단·메모를 읽는 별도 GET 경로. 쓰기는 작성자 전용 | [공유 API](../../app/api/ai/shared.py), [미확정 정책 D01](../decisions/index.md#d01) |

화면 배치(2026-11-04): AI 리뷰(커밋·PR 단위 분석)는 왼쪽 목록의 최상위 항목이 아니라 현재 저장소 "AI 분석"의 세 번째 탭이다(자동 분석·대화형 분석·커밋·PR 단위 분석). 2026-11-10부터 커밋·PR 단위 분석 화면 위쪽의 리뷰 모델 선택 카드를 없앴다. 모델은 앱 설정의 기본 모델 → 키가 저장된(또는 로컬) 모델 순으로 정해지고, 실행 직전 전송 동의 칸에서만 바꾼다. 설정 화면의 모델·키 상태 카드는 그대로다. AI 리뷰·AI 분석은 앱 설정에 저장한 사용자 API key로 실행하고 서버에는 AI key·암호화 키 설정이 없다([AD-33](../architecture/ARCHITECTURE_DECISIONS.md)). 기능이 구현되어 있다는 사실은 실제 모델 품질·운영 배포·인증 E2E가 검증됐다는 뜻이 아니다. 커뮤니티 채팅·음성·공동 편집도 실제 여러 계정·여러 네트워크에서의 사용은 아직 시험하지 않았다. [현재 검증 상태](../validation/current-status.md)를 별도로 읽는다.

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
