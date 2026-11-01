<#
  GitViz 원클릭 준비/실행. 처음 clone한 뒤 프로젝트 어디서든 실행한다.

  데스크톱 앱 (Windows):
    pwsh tools/dev/bootstrap.ps1 -App              앱 빌드·패키징 (build-output\desktop\GitVizWorkspace-win32-x64\)
    pwsh tools/dev/bootstrap.ps1 -App -Run         빌드 후 앱 실행 (서버 없이: 프로젝트, AI 리뷰·리팩터링)
    pwsh tools/dev/bootstrap.ps1 -App -Run -Staging
                                                   로컬 HTTPS 서버(https://localhost:8443)도 띄우고 앱을 연결해 실행
                                                   (로그인, 저장소 탐색, 커뮤니티·채팅·음성). PostgreSQL 18, Redis,
                                                   .validation\staging\staging.env의 GitHub OAuth 값이 필요하다.
    하는 일: 도구 확인(Git, Python 3.12, Node 24.20+) -> desktop npm ci(필요할 때만) -> 작업 에이전트 빌드
             -> 앱 build/package -> (-Run이면) 실행.

  웹 서버 개발 (http://localhost:8000):
    pwsh tools/dev/bootstrap.ps1 -LocalDefaults -Run
    하는 일: 도구 확인 -> .venv 생성 -> requirements.txt 설치 -> .env 준비 -> docker compose db/redis 기동
             -> alembic upgrade head -> (-Run이면) uvicorn과 celery worker를 새 창으로 실행.

  하지 않는 일: 이미 있는 .env나 staging.env를 읽거나 수정하지 않는다. GitHub OAuth 값은 만들 수 없다(직접 발급).
#>
[CmdletBinding()]
param(
    [switch]$App,             # 데스크톱 앱을 빌드·패키징한다(웹 서버용 .env·Docker·마이그레이션 단계는 건너뛴다)
    [switch]$Staging,         # -App과 함께: 로컬 HTTPS 스테이징 서버를 준비·실행하고 앱을 그 서버에 연결한다
    [switch]$LocalDefaults,   # 웹 서버: .env를 이번 실행에서 새로 만든 경우에만 로컬 compose용 값과 임의 비밀키를 채운다
    [switch]$Run,             # 웹 서버: uvicorn(8000)과 celery worker를 새 창으로 실행 / -App: 빌드한 앱을 실행
    [switch]$SkipDocker,      # 웹 서버: docker compose 기동을 건너뛴다(DB/Redis를 직접 준비한 경우)
    [switch]$SkipMigrate      # 웹 서버: alembic upgrade head를 건너뛴다
)
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '../..')).Path
Set-Location -LiteralPath $repo

function Step([string]$Text) { Write-Host "==> $Text" -ForegroundColor Cyan }
function Fail([string]$Text, [int]$Code = 1) { Write-Host "중단: $Text" -ForegroundColor Red; exit $Code }
function Invoke-Checked([string]$What, [scriptblock]$Command) { & $Command; if ($LASTEXITCODE -ne 0) { Fail "$What 실패" } }

if ($Staging -and -not $App) { Fail '-Staging은 -App과 함께 쓴다(예: -App -Run -Staging).' }

Step '도구 확인'
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Fail 'Git이 없습니다.' }
$pythonCommand = $null
foreach ($candidate in @(@('py', '-3.12'), @('python'))) {
    if (-not (Get-Command $candidate[0] -ErrorAction SilentlyContinue)) { continue }
    $arguments = @($candidate | Select-Object -Skip 1) + @('-c', 'import sys; print("%d.%d" % sys.version_info[:2])')
    $version = & $candidate[0] @arguments 2>$null
    if ($LASTEXITCODE -eq 0 -and $version -eq '3.12') { $pythonCommand = $candidate; break }
}
if (-not $pythonCommand) { Fail 'Python 3.12가 필요합니다(py -3.12 또는 python).' }
function New-Venv([string]$Path) {
    & $pythonCommand[0] @(@($pythonCommand | Select-Object -Skip 1) + @('-m', 'venv', $Path))
    if ($LASTEXITCODE -ne 0) { Fail "$Path 생성 실패" }
}
if ($App) {
    if (-not (Get-Command node -ErrorAction SilentlyContinue) -or -not (Get-Command npm -ErrorAction SilentlyContinue)) { Fail 'Node.js 24.20 이상 25 미만(npm 포함)이 필요합니다.' }
    $nodeVersion = [version](& node -p 'process.versions.node')
    if ($nodeVersion -lt [version]'24.20.0' -or $nodeVersion.Major -ge 25) { Fail "Node.js 24.20 이상 25 미만이 필요합니다(현재 $nodeVersion)." }
} elseif (-not $SkipDocker -and -not (Get-Command docker -ErrorAction SilentlyContinue)) { Fail 'Docker가 없습니다. DB/Redis를 직접 준비했다면 -SkipDocker를 쓰세요.' }

$venvPython = Join-Path $repo '.venv/Scripts/python.exe'
if (-not $App -or $Staging) {
    Step '.venv 준비와 서버 의존성 설치'
    if (-not (Test-Path -LiteralPath $venvPython)) { New-Venv '.venv' }
    Invoke-Checked 'pip install' { & $venvPython -m pip install --disable-pip-version-check -r requirements.txt }
}

if ($App) {
    $desktop = Join-Path $repo 'desktop'
    $exe = Join-Path $repo 'build-output\desktop\GitVizWorkspace-win32-x64\GitVizWorkspace.exe'
    $stagingScript = Join-Path $repo 'tools\vm\staging_stack.ps1'
    $origin = 'https://localhost:8443'

    # 실행 중인 앱은 패키징 폴더의 파일을 잠근다.
    if (Get-Process GitVizWorkspace -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $exe }) { Fail 'GitViz 앱이 실행 중입니다. 앱을 닫고 다시 실행하세요.' }

    Step '앱 의존성 (desktop npm ci)'
    $installed = Join-Path $desktop 'node_modules\.package-lock.json'
    $lock = Join-Path $desktop 'package-lock.json'
    if (-not (Test-Path -LiteralPath $installed) -or (Get-Item -LiteralPath $lock).LastWriteTime -gt (Get-Item -LiteralPath $installed).LastWriteTime) {
        Invoke-Checked 'npm ci' { npm --prefix desktop ci --no-audit --no-fund }
    } else { Write-Host 'desktop\node_modules가 package-lock.json과 맞아 설치를 건너뜁니다.' }

    Step '작업 에이전트 빌드 (.validation\venv-workspace, PyInstaller)'
    $agentPython = Join-Path $repo '.validation\venv-workspace\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $agentPython)) { New-Venv '.validation\venv-workspace' }
    Invoke-Checked '에이전트 의존성 설치' { & $agentPython -m pip install --disable-pip-version-check --require-hashes --only-binary=:all: -r packaging/agent/requirements-workspace.lock }
    Invoke-Checked '에이전트 빌드 도구 설치' { & $agentPython -m pip install --disable-pip-version-check -r packaging/agent/requirements-workspace-build.txt }
    Invoke-Checked '에이전트 빌드' { & $agentPython tools/build/build_workspace_agent.py }

    Step '앱 빌드와 패키징'
    Invoke-Checked 'npm run build' { npm --prefix desktop run build }
    Invoke-Checked 'npm run package' { npm --prefix desktop run package }
    if (-not (Test-Path -LiteralPath $exe)) { Fail "패키징 결과가 없습니다: $exe" }
    Write-Host "앱: $exe" -ForegroundColor Green

    if ($Staging) {
        Step '로컬 HTTPS 스테이징 서버 (https://localhost:8443)'
        $listening = { [bool](Get-NetTCPConnection -LocalPort 8443 -State Listen -ErrorAction SilentlyContinue) }
        if (& $listening) { Write-Host '이미 실행 중입니다.' }
        else {
            Invoke-Checked '스테이징 준비(Prepare)' { pwsh -NoProfile -File $stagingScript -Mode Prepare }
            $userEnv = Join-Path $repo '.validation\staging\staging.env'
            if (-not (Test-Path -LiteralPath $userEnv) -or (Get-Item -LiteralPath $userEnv).Length -eq 0) {
                Fail "$userEnv 에 GITHUB_CLIENT_ID=와 GITHUB_CLIENT_SECRET=를 적고 다시 실행하세요(OAuth App 콜백: $origin/api/auth/callback)." 2
            }
            # 서버와 worker는 각자 새 창에서 계속 돈다. 끄려면: pwsh tools/vm/staging_stack.ps1 -Mode Down
            Start-Process pwsh -ArgumentList '-NoProfile', '-File', $stagingScript, '-Mode', 'Up'
            for ($i = 0; $i -lt 30 -and -not (& $listening); $i++) { Start-Sleep -Seconds 2 }
            if (-not (& $listening)) { Fail '스테이징 서버가 60초 안에 시작되지 않았습니다. .validation\staging\logs를 확인하세요.' }
            Start-Process pwsh -ArgumentList '-NoProfile', '-File', $stagingScript, '-Mode', 'Worker'
        }
    }

    if ($Run) {
        Step '앱 실행'
        if ($Staging) {
            # 빌드에 서버 주소를 넣지 않은 앱만 이 값을 쓴다. 자체 서명 인증서를 앱(main)이 신뢰하게 한다.
            $env:GITVIZ_SERVER_ORIGIN = $origin
            $env:NODE_EXTRA_CA_CERTS = Join-Path $repo '.validation\staging\tls\server.pem'
        }
        Start-Process -FilePath $exe
        if ($Staging) { Write-Host "앱을 $origin 에 연결해 실행했습니다. 오른쪽 위 로그인을 누르세요." -ForegroundColor Green }
    } else {
        Write-Host ''
        Write-Host '빌드 완료. 실행하려면 -Run을 붙여 다시 실행하거나 README의 "데스크톱 앱 실행" 명령을 따르세요.' -ForegroundColor Green
    }
    exit 0
}

Step '.env 준비'
$envPath = Join-Path $repo '.env'
$createdEnv = $false
if (-not (Test-Path -LiteralPath $envPath)) {
    Copy-Item -LiteralPath (Join-Path $repo '.env.example') -Destination $envPath
    $createdEnv = $true
    Write-Host '.env를 .env.example에서 새로 만들었습니다.'
} else {
    Write-Host '.env가 이미 있어 읽지도 수정하지도 않습니다.'
}
if ($createdEnv -and $LocalDefaults) {
    $key = & $venvPython -c "import secrets; print(secrets.token_hex(32))"
    $session = & $venvPython -c "import secrets; print(secrets.token_urlsafe(48))"
    $values = @{
        DATABASE_URL       = 'postgresql+psycopg2://gitviz:gitviz@localhost:5432/gitviz'
        DATABASE_URL_ASYNC = 'postgresql+asyncpg://gitviz:gitviz@localhost:5432/gitviz'
        REDIS_URL          = 'redis://localhost:6379/0'
        FRONTEND_URL       = 'http://localhost:8000'
        TOKEN_ENCRYPTION_KEY = $key
        SESSION_SECRET_KEY = $session
    }
    $lines = [IO.File]::ReadAllLines($envPath)
    for ($i = 0; $i -lt $lines.Length; $i++) {
        foreach ($name in $values.Keys) {
            if ($lines[$i].StartsWith($name + '=')) { $lines[$i] = $name + '=' + $values[$name] }
        }
    }
    [IO.File]::WriteAllLines($envPath, $lines, (New-Object Text.UTF8Encoding($false)))
    Write-Host '로컬 compose용 DB/Redis 주소와 임의 암호화·세션 키를 채웠습니다(이 PC 전용 값).'
    Write-Host 'GITHUB_CLIENT_ID / GITHUB_CLIENT_SECRET은 비어 있습니다. 로그인하려면 직접 발급해 .env에 넣으세요.' -ForegroundColor Yellow
} elseif ($createdEnv) {
    Write-Host '.env의 DB, Redis, GitHub OAuth, TOKEN_ENCRYPTION_KEY, SESSION_SECRET_KEY를 채운 뒤 다시 실행하세요.' -ForegroundColor Yellow
    Write-Host '로컬 compose DB로 바로 시작하려면 .env를 지우고 -LocalDefaults로 다시 실행하세요.'
    exit 2
}

if (-not $SkipDocker) {
    Step 'docker compose로 db, redis 기동'
    & docker compose up -d db redis
    if ($LASTEXITCODE -ne 0) { Fail 'docker compose up 실패(Docker Desktop이 실행 중인지 확인하세요).' }
    $ready = $false
    for ($i = 0; $i -lt 30 -and -not $ready; $i++) {
        & docker compose exec -T db pg_isready -U gitviz *> $null
        if ($LASTEXITCODE -eq 0) { $ready = $true } else { Start-Sleep -Seconds 2 }
    }
    if (-not $ready) { Fail 'PostgreSQL이 60초 안에 준비되지 않았습니다.' }
}

if (-not $SkipMigrate) {
    Step 'DB 마이그레이션 (alembic upgrade head)'
    & $venvPython -m alembic upgrade head
    if ($LASTEXITCODE -ne 0) { Fail 'alembic upgrade 실패. .env의 DATABASE_URL과 DB 상태를 확인하세요.' }
}

if ($Run) {
    Step '서버와 worker 실행 (새 창 2개)'
    $shell = (Get-Process -Id $PID).Path
    Start-Process $shell -ArgumentList '-NoExit', '-Command', "Set-Location -LiteralPath '$repo'; & '$venvPython' -m uvicorn app.main:app --host 127.0.0.1 --port 8000"
    Start-Process $shell -ArgumentList '-NoExit', '-Command', "Set-Location -LiteralPath '$repo'; & '$venvPython' -m celery -A app.core.celery_app:celery worker --loglevel=info --pool=solo"
    Write-Host '접속: http://localhost:8000'
} else {
    Write-Host ''
    Write-Host '준비 완료. 실행하려면 -Run을 붙여 다시 실행하거나 README의 "웹 서버 개발 실행" 명령을 따르세요.' -ForegroundColor Green
}
