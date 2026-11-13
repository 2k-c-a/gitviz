# GitViz

GitHub 저장소의 개발 이력을 수집하고 수정 집중도·공동 변경·협업 관계를 분석한다. 결과는 기준 SHA와 기간을 가진 불변 스냅샷으로 저장한다. 선택 기능인 AI 리뷰는 PR·커밋의 변경 설명과 코드 검토를 돕는다. Windows 데스크톱 앱에서 내 Git 프로젝트의 AI 리뷰·리팩터링과, 저장소별 커뮤니티(글·실시간 채팅·3인 음성 채팅·3인 실시간 공동 편집)를 쓸 수 있다.

**[전체 문서 안내](docs/index.md)** · [현재 기능 범위](docs/product/current-scope.md) · [검증 상태](docs/validation/current-status.md)

## 데스크톱 앱 실행 (Windows)

GitViz는 Windows 데스크톱 앱(Electron)이 기본 사용 방식이다. 앱은 내 PC의 Git 저장소를 다루는 로컬 기능(프로젝트, AI 리뷰·리팩터링 — 내 AI 키 사용)과 서버에 연결해서 쓰는 기능(GitHub 로그인, 저장소 탐색·분석, 커뮤니티 글·채팅·음성 채팅·공동 편집)으로 나뉜다.

준비물: Windows 10/11 x64, Node.js 24.20 이상 25 미만, Python 3.12, Git.

### 원클릭

```powershell
git clone https://github.com/2k-c-a/gitviz.git
cd gitviz
pwsh tools/dev/bootstrap.ps1 -App -Run       # 앱 빌드 후 실행 (서버 없이: 프로젝트, AI 리뷰·리팩터링)
pwsh tools/dev/bootstrap.ps1 -App -Staging   # 로컬 HTTPS 서버를 띄우고, 그 서버에 연결되는 앱을 빌드 (처음 한 번)
pwsh tools/dev/bootstrap.ps1 -Staging        # 그다음부터: 서버만 켠다 (재부팅 뒤 등)
```

`-App -Staging`으로 빌드한 앱은 서버 주소와 서버의 자체 서명 인증서가 들어 있어, 서버가 켜져 있으면 탐색기에서 `build-output\desktop\GitVizWorkspace-win32-x64\GitVizWorkspace.exe`를 더블클릭해도 로그인·저장소 탐색·커뮤니티·채팅·음성·공동 편집이 모두 된다. 어느 명령이든 `-Run`을 붙이면 앱 실행까지 한다. 이 PC에서 빌드한 앱은 이 PC의 서버에만 연결되므로, 다른 PC에서는 clone 후 그 PC에서 `-App -Staging`을 실행한다(빌드한 앱을 복사하지 않는다).

- `-App`은 아래 1단계(도구 확인, 앱 의존성, 작업 에이전트 빌드, 앱 빌드·패키징)를 한 번에 한다. 앱 의존성은 `desktop\node_modules`가 `package-lock.json`과 맞으면 다시 설치하지 않는다. 앱이 열려 있으면 패키징 파일이 잠겨 있으므로 앱을 닫고 실행한다.
- `-Staging`은 3단계의 서버 준비·실행(`Prepare`, `Up`, `Worker`)을 한다. 서버가 이미 떠 있으면 그대로 쓰고, `staging.env`가 비어 있으면 GitHub OAuth 값을 적으라고 안내한 뒤 멈춘다. 서버와 worker는 각자 새 창에서 돌고, 끄려면 `pwsh tools/vm/staging_stack.ps1 -Mode Down`을 실행한다. 재부팅하면 서버가 꺼지므로 앱을 쓰기 전에 다시 켠다.
- 서버 인증서는 14일짜리라 만료 2일 전부터 서버를 켤 때 새로 만든다. 그러면 앱을 다시 빌드해야 하고(`-App -Staging`), `-Staging`이 그때 알려 준다.

단계별로 직접 하려면 아래를 따른다.

### 1. 앱 빌드

프로젝트 루트 PowerShell에서:

```powershell
git clone https://github.com/2k-c-a/gitviz.git
cd gitviz

# 앱 의존성
npm --prefix desktop ci

# 로컬 작업 에이전트(앱에 함께 들어가는 Python 실행 파일) 빌드
python -m venv .validation\venv-workspace
.\.validation\venv-workspace\Scripts\python.exe -m pip install --require-hashes --only-binary=:all: -r packaging/agent/requirements-workspace.lock
.\.validation\venv-workspace\Scripts\python.exe -m pip install -r packaging/agent/requirements-workspace-build.txt
.\.validation\venv-workspace\Scripts\python.exe tools/build/build_workspace_agent.py

# 앱 빌드와 패키징 → build-output\desktop\GitVizWorkspace-win32-x64\
npm --prefix desktop run build
npm --prefix desktop run package
```

### 2. 앱만 실행 (서버 없이)

```powershell
.\build-output\desktop\GitVizWorkspace-win32-x64\GitVizWorkspace.exe
```

프로젝트(로컬 Git 폴더 등록), AI 리뷰·리팩터링(설정에서 내 AI 키 저장)은 서버 없이 동작한다. 로그인·저장소 탐색·커뮤니티는 서버가 없으면 "서버가 설정되지 않음"으로 표시된다.

### 3. 서버에 연결해 실행 (로그인·커뮤니티·채팅·음성)

내 PC에 격리된 HTTPS 서버(스테이징)를 띄워 앱을 연결한다. 개발용 DB와 `.env`는 건드리지 않는다.

추가 준비물:

- PostgreSQL 16 이상 (`C:\Program Files\PostgreSQL\<버전>\bin` 중 가장 높은 버전을 자동으로 쓴다. 스테이징 DB를 이미 만들었다면 그때 쓴 버전을 쓴다), winget으로 설치한 Redis(`redis-server.exe`)
- GitHub OAuth App: Homepage URL `https://localhost:8443`, Authorization callback URL `https://localhost:8443/api/auth/callback`

```powershell
# 서버 의존성 (.venv)
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# DB·Redis·인증서·비밀키 준비와 마이그레이션 (여러 번 실행해도 된다)
pwsh tools/vm/staging_stack.ps1 -Mode Prepare
```

`.validation\staging\staging.env`에 OAuth App 값을 적는다(이 폴더는 Git에 올라가지 않는다).

```text
GITHUB_CLIENT_ID=...
GITHUB_CLIENT_SECRET=...
```

```powershell
pwsh tools/vm/staging_stack.ps1 -Mode Up       # https://localhost:8443
pwsh tools/vm/staging_stack.ps1 -Mode Worker   # 저장소 분석 작업 처리

# 서버 주소 없이 빌드한 앱(1단계)을 이 서버에 연결해 실행 (자체 서명 인증서를 신뢰시킨다)
$env:GITVIZ_SERVER_ORIGIN = 'https://localhost:8443'
$env:NODE_EXTRA_CA_CERTS = "$PWD\.validation\staging\tls\server.pem"
.\build-output\desktop\GitVizWorkspace-win32-x64\GitVizWorkspace.exe
```

환경변수 없이(탐색기 더블클릭으로) 쓰려면 서버 주소와 인증서를 넣어 빌드한다. 원클릭의 `-App -Staging`이 이것을 한다.

```powershell
$env:GITVIZ_BUILD_SERVER_ORIGIN = 'https://localhost:8443'
$env:GITVIZ_BUILD_SERVER_CA = "$PWD\.validation\staging\tls\server.pem"
npm --prefix desktop run build; npm --prefix desktop run package
```

앱 오른쪽 위 **로그인**을 누르면 브라우저에서 GitHub 로그인 후 앱으로 자동으로 돌아온다. 상태 확인은 `-Mode Status`, 종료는 `-Mode Down`.

- `GITVIZ_SERVER_ORIGIN`은 빌드에 서버 주소를 넣지 않은 앱에서만 쓰인다. 배포용 앱은 `$env:GITVIZ_BUILD_SERVER_ORIGIN = 'https://<서버 주소>'`로 빌드하면 그 주소가 앱에 들어간다.
- `GITVIZ_BUILD_SERVER_CA`(인증서 넣기)는 `localhost`·`127.0.0.1` 서버 주소에만 쓸 수 있고, 인증서가 그 주소를 가리켜야 한다. 배포용 앱에는 넣지 않는다.
- 음성 채팅의 다른 네트워크 사이 연결은 서버 환경변수 `VOICE_STUN_URLS`의 STUN 서버로 한다. 예시 설정과 스테이징은 공개 STUN(`stun.l.google.com`)을 쓴다. 이때 STUN 제공자에게 참여자의 공인 IP 주소가 보이지만 음성은 가지 않는다. 회사·학교 방화벽처럼 직접 연결이 막힌 곳까지 되게 하려면 `VOICE_TURN_URLS`·`VOICE_TURN_SECRET`로 TURN 서버(coturn)를 더한다([.env.example](.env.example)). 비우면 같은 네트워크 안에서만 연결된다.
- 공동 편집은 앱에서만 된다. 공유한 사람(호스트) PC의 파일 하나(100KB 이하 텍스트)를 3명까지 함께 고치고, 저장은 호스트 앱이 자기 파일에 한다. 서버는 변경분을 전달만 하고 파일 내용을 저장하지 않는다.

### 3-1. 여러 PC로 시연 (같은 네트워크 2~3대)

커뮤니티(글·댓글·채팅·음성·공동 편집)는 서버를 거친다. 호스트 PC 한 대에서 3단계 서버를 켜고, 정식 인증서가 있는 HTTPS 터널(ngrok)로 다른 PC가 접속하게 한다. 서버는 그대로 `127.0.0.1`에서만 열리므로 방화벽 설정이 필요 없다. 음성은 같은 네트워크 안에서 PC끼리 직접 연결된다.

1. ngrok 계정을 만들고 무료 고정 도메인(예: `demo-xxxx.ngrok-free.app`)을 받은 뒤, 호스트 PC에 ngrok을 설치하고 `ngrok config add-authtoken <토큰>`을 실행한다.
2. GitHub에 시연용 OAuth 앱을 새로 만든다. Homepage URL은 `https://<도메인>`, Authorization callback URL은 `https://<도메인>/api/auth/callback`으로 한다. 기존 localhost용 앱은 그대로 둔다.
3. `.validation\staging\staging.env`에 `PUBLIC_ORIGIN=https://<도메인>`을 넣고, `GITHUB_CLIENT_ID`·`GITHUB_CLIENT_SECRET`을 시연용 OAuth 앱 값으로 바꾼다. 시연이 끝나면 `PUBLIC_ORIGIN` 줄을 지우고 원래 값으로 되돌린다.
4. 서버를 켜고(`pwsh tools/vm/staging_stack.ps1 -Mode Down` 후 `pwsh tools/dev/bootstrap.ps1 -Staging`) 터널을 연다: `ngrok http https://localhost:8443 --url=https://<도메인>`.
5. 그 주소를 넣은 설치 파일을 만든다(인증서는 넣지 않는다): `$env:GITVIZ_BUILD_SERVER_ORIGIN='https://<도메인>'` → `npm --prefix desktop run build` → `npm --prefix desktop run make`. 만든 `GitVizWorkspaceSetup.exe`를 모든 PC(호스트 포함)에 설치한다.
6. PC마다 다른 GitHub 계정으로 로그인한다. 호스트가 저장소를 분석하면 그 저장소의 커뮤니티가 생기고, 기여자가 아닌 계정은 호스트가 보낸 초대 링크로 들어온다.

ngrok 무료 도메인은 브라우저로 처음 열 때 안내 페이지를 한 번 보여 준다(**Visit Site**를 누르면 계속된다). 시연 동안만 서버와 터널을 켜 두고, 끝나면 둘 다 끈다(`staging_stack.ps1 -Mode Down`, ngrok 창 닫기).

### 4. 설치 파일 만들기 (선택)

설치 파일 하나(`GitVizWorkspaceSetup.exe`)에 앱 런타임(Electron·Node.js)과 로컬 작업 에이전트(Python 런타임·VC++/UCRT DLL 포함)가 모두 들어 있다. 사용하는 PC에 따로 있어야 하는 것은 Git for Windows 하나이며, 앱이 처음 실행될 때 검사하고 설치까지 해 준다(아래).

```powershell
# 1) 에이전트 동결 → build-output\agent\dist\agent  (잠금 venv 준비: docs/deployment/BUILD_AND_RELEASE.md §3)
.\.validation\venv-workspace\Scripts\python.exe -X utf8 -B tools\build\build_workspace_agent.py
.\.validation\venv-workspace\Scripts\python.exe tools\build\audit_workspace_agent.py --directory build-output\agent\dist\agent --output <보고서 파일>
# 2) 앱 빌드와 설치 파일 → build-output\desktop\make\squirrel.windows\x64\GitVizWorkspaceSetup.exe
npm --prefix desktop run build
npm --prefix desktop run make
node desktop\scripts\audit-bundle.mjs build-output\desktop\GitVizWorkspace-win32-x64\resources\app.asar <보고서 파일>
# 3) 배포 후보로 복사하고 해시를 남긴다 → build-output\release-candidates\<버전>\ (+ SHA256SUMS.txt)
```

서버 주소 없이 빌드한 설치본은 로컬 기능만 켜진다. 운영 서버에 연결되는 설치본은 `GITVIZ_BUILD_SERVER_ORIGIN=https://<서버>`를 설정하고 `build`부터 다시 한다([환경 아키텍처 §2.4](docs/architecture/ENVIRONMENT_ARCHITECTURE.md)).

**설치와 첫 실행(사용자):**

1. `GitVizWorkspaceSetup.exe`를 실행한다. 관리자 권한 없이 내 계정에 설치되고(`%LOCALAPPDATA%\GitVizWorkspace`), 바탕화면·시작 메뉴 바로가기가 생기며, 설치가 끝나면 앱이 바로 열린다. 제거는 Windows 설정 > 앱에서 한다(내 데이터는 남는다).
2. 앱의 **시작하기 > 시스템 환경 확인**이 단계(시스템 환경 확인 → 의존성 검사 → 누락된 의존성 설치 → 준비 완료)를 보여 준다. 항목은 운영체제(Windows 10·11), 아키텍처(x64), 앱 런타임, 로컬 작업 엔진, Git for Windows(2.30 이상)이고, 상태는 정상 설치·미설치·버전 불일치·설치 필요로 표시된다.
3. Git이 없거나 오래됐으면 **필요한 항목 설치**를 누른다. 공식 배포처(github.com/git-for-windows)의 고정 버전(2.56.0.windows.4)을 받아 크기와 SHA-256을 확인한 뒤, 관리자 권한 없이 내 계정(`%LOCALAPPDATA%\Programs\Git`)에 조용히 설치한다. 이미 정상인 Git은 다시 설치하지 않는다. 끝나면 **앱 다시 시작**을 누른다.
4. 내려받기·설치가 실패하면 이유를 화면에 보여 주고, **공식 다운로드 페이지 열기**로 직접 설치할 수 있다. 검사·설치 결과(항목 상태와 오류 코드만, 경로·계정 없음)는 앱 로그에 남는다.

**내려받기:** 설치 파일은 [GitHub Releases](https://github.com/2k-c-a/gitviz/releases/latest)에 올린다(1.0.0부터). `v<버전>` 태그를 푸시하면 `windows-release.yml`이 검증·빌드한 뒤 초안 Release를 만들고, 확인 후 게시한다([Windows 데스크톱 안내](docs/operations/windows-desktop.md#github-releases-배포)).

현재 앱(1.0.0)은 코드 서명이 없어 설치 때 Windows SmartScreen 경고가 나온다. 설치본은 `gitviz-join://` 초대 링크를 연다. Git 설치 경로와 직접 설치 방법은 [설치 가이드 §0](docs/deployment/INSTALLATION_GUIDE.md)에 있다. 앱 로그는 `%APPDATA%\gitviz-workspace\logs\app.log`에 남는다. 설치·업데이트 상태는 [Windows 데스크톱 안내](docs/operations/windows-desktop.md)를 따른다.

### 앱 테스트

```powershell
npm --prefix desktop run lint
npm --prefix desktop test                                 # 단위 테스트
cd desktop; npx electron dist/tests/phase3-flows.e2e.js   # E2E 예시 (dist/tests/*.e2e.js)
```

## 웹 서버 개발 실행

앱 없이 웹 화면(`http://localhost:8000`)으로 서버를 개발할 때 쓴다. Python 3.12, Git, Docker(Docker Desktop 실행 중)를 준비한다.

### 원클릭 (웹 서버)

```powershell
git clone https://github.com/2k-c-a/gitviz.git
cd gitviz
pwsh tools/dev/bootstrap.ps1 -LocalDefaults -Run
```

`.venv` 생성, 의존성 설치, `.env` 준비, DB·Redis 컨테이너 기동, 마이그레이션까지 하고 `-Run`이면 서버와 worker를 새 창으로 띄운다(`http://localhost:8000`). `-LocalDefaults`는 `.env`를 이번에 새로 만든 경우에만 로컬 compose 주소와 임의 암호화·세션 키를 채운다. 이미 있는 `.env`는 읽거나 고치지 않는다. GitHub OAuth 값은 직접 발급해 `.env`에 넣어야 로그인할 수 있다. 옵션은 `-SkipDocker`, `-SkipMigrate`. 코드 구조는 [최종 디렉터리 구조](docs/architecture/FINAL_DIRECTORY_TREE.md), 개발 환경 전체는 [개발 환경 설정](docs/development/DEVELOPMENT_SETUP.md)을 본다.

### 수동 실행

프로젝트 루트 PowerShell에서:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
if (!(Test-Path .env)) { Copy-Item .env.example .env }
docker compose up -d db redis
```

[.env.example](.env.example)에 따라 DB·Redis·GitHub OAuth·토큰 암호화 키·고유 세션 비밀키를 설정한 뒤 실행한다. 로컬 compose DB 예시는 다음과 같다.

```text
DATABASE_URL=postgresql://gitviz:gitviz@localhost:5432/gitviz
DATABASE_URL_ASYNC=postgresql+asyncpg://gitviz:gitviz@localhost:5432/gitviz
REDIS_URL=redis://localhost:6379/0
FRONTEND_URL=http://localhost:8000
```

```powershell
python -m alembic upgrade head
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

별도 가상환경 터미널에서 일반 분석 worker를 실행한다.

```powershell
python -m celery -A app.core.celery_app:celery worker --loglevel=info --pool=solo
```

접속 주소는 `http://localhost:8000`이다. 앱은 스키마를 자동 생성하지 않으며 새 분석·재분석에는 로그인이 필요하다. [설치·프로세스 안내](docs/operations/setup.md)와 [접근·스냅샷·backfill](docs/architecture/repository-access-and-snapshots.md)을 참조한다. Windows 데스크톱 앱 설치·업데이트 상태는 [Windows 데스크톱 안내](docs/operations/windows-desktop.md)를 따른다.

## AI 리뷰

앱 전용이다. 사용자가 앱 설정에 저장한 API key(Windows DPAPI)를 앱이 실행마다 보내고, 서버는 key도 코드 원문도 저장하지 않는다([AD-33](docs/architecture/ARCHITECTURE_DECISIONS.md)). 클라우드는 Anthropic·OpenAI·Gemini 사용자 BYOK를, 같은 PC 실행은 Ollama·LM Studio·KoboldCpp를 지원한다. 입력 고정 → 미리보기·동의 → 설명/리뷰 → 근거·피드백 흐름을 제공한다. 결과·인용·메모의 공유 읽기 조건은 [AI API](docs/contracts/ai-api.md)와 [미확정 공유 정책](docs/decisions/index.md#d01)을 확인한다.

화면은 `/pages/ai-review.html?repo=<id>`다(웹에서는 '앱에서만'으로 안내). 실행은 API 프로세스 안의 스레드에서 하므로 별도 AI worker가 없다. 설정은 [AI 운영](docs/operations/ai.md), 모델별 한도·로컬 조건은 [제공자 안내](docs/operations/providers.md)를 따른다. 비공개 저장소도 실행마다 보낼 코드를 확인하고 동의하면 클라우드 모델로 리뷰할 수 있다.

S12 인수인계·S13 보고서는 폐기했고 S14 자동 리뷰·S15 GitHub 댓글 게시는 보류다. [현재 범위](docs/product/current-scope.md)와 [후속 계획](docs/product/roadmap.md)을 구분한다. 실제 모델 품질을 구조 검증만으로 보장하지 않는다.

## 검증과 문서

```powershell
python -B docs/audit/check_documents.py
```

위 명령은 문서 링크·인벤토리·이동한 원본의 해시를 검사한다. 제품 검증은 [일관성·회귀 절차](docs/validation/protocols/document-consistency.md), [모델 평가 절차](docs/validation/protocols/ai-evaluation.md)를 따른다. DB 검사는 운영과 분리한 `gitviz_test` 이름의 DB가 필요하며 큐 통합은 전용 Redis DB를 사용한다.

| 목적 | 문서 |
|---|---|
| 지표 정의·분모·해석 | [분석 계약](docs/contracts/analytics.md) · [지표 정의](docs/contracts/metrics.md) |
| 수집·프론트 계약 | [수집](docs/contracts/collection.md) · [프론트](docs/contracts/frontend.md) |
| 정책 충돌·미확정 | [결정 목록](docs/decisions/index.md) |
| 현재 실행 결과·남은 검사 | [검증 상태](docs/validation/current-status.md) |
| 과거 기획·설계·수용·검증 원문 | [Historical 자료](docs/archive/index.md) |
| 이전→현재 경로·보존 근거 | [인벤토리](docs/audit/inventory.md) · [정리 기록](docs/audit/organization-plan.md) |
