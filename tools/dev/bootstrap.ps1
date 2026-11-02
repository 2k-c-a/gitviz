<#
  GitViz 원클릭 준비/실행. 처음 clone한 뒤 프로젝트 어디서든 실행한다.

  데스크톱 앱 (Windows):
    pwsh tools/dev/bootstrap.ps1 -App              앱 빌드·패키징 (build-output\desktop\GitVizWorkspace-win32-x64\)
    pwsh tools/dev/bootstrap.ps1 -App -Run         빌드 후 앱 실행 (서버 없이: 프로젝트, AI 리뷰·리팩터링)
    pwsh tools/dev/bootstrap.ps1 -App -Staging     로컬 HTTPS 서버(https://localhost:8443)를 준비·실행하고, 그 서버 주소와
                                                   인증서를 넣어 앱을 빌드한다. 이후 탐색기에서 exe를 더블클릭해도 로그인,
                                                   저장소 탐색, 커뮤니티·채팅·음성·공동 편집이 된다. -Run을 붙이면 실행까지.
                                                   PostgreSQL 18, Redis, .validation\staging\staging.env의 GitHub OAuth 값이 필요하다.
    pwsh tools/dev/bootstrap.ps1 -Staging          서버만 켠다(재부팅 뒤 등). 빌드하지 않는다. -Run을 붙이면 앱도 실행.
                                                   서버 인증서(14일)가 갱신돼 앱을 다시 빌드해야 하면 알려 준다.
    하는 일: 도구 확인(Git, Python 3.12, Node 24.20+) -> desktop npm ci(필요할 때만) -> 작업 에이전트 빌드
             -> (-Staging이면) 서버 준비·실행 -> 앱 build/package -> (-Run이면) 실행.

  웹 서버 개발 (http://localhost:8000):
    pwsh tools/dev/bootstrap.ps1 -LocalDefaults -Run
    하는 일: 도구 확인 -> .venv 생성 -> requirements.txt 설치 -> .env 준비 -> docker compose db/redis 기동
             -> alembic upgrade head -> (-Run이면) uvicorn과 celery worker를 새 창으로 실행.

  하지 않는 일: 이미 있는 .env나 staging.env를 읽거나 수정하지 않는다. GitHub OAuth 값은 만들 수 없다(직접 발급).
#>
[CmdletBinding()]
param(
    [switch]$App,             # 데스크톱 앱을 빌드·패키징한다(웹 서버용 .env·Docker·마이그레이션 단계는 건너뛴다)
    [switch]$Staging,         # 로컬 HTTPS 스테이징 서버를 준비·실행한다. -App과 함께면 그 서버 주소·인증서를 넣어 앱을 빌드한다
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
} elseif (-not $Staging -and -not $SkipDocker -and -not (Get-Command docker -ErrorAction SilentlyContinue)) { Fail 'Docker가 없습니다. DB/Redis를 직접 준비했다면 -SkipDocker를 쓰세요.' }

$venvPython = Join-Path $repo '.venv/Scripts/python.exe'
if (-not $App -or $Staging) {
    Step '.venv 준비와 서버 의존성 설치'
    if (-not (Test-Path -LiteralPath $venvPython)) { New-Venv '.venv' }
    Invoke-Checked 'pip install' { & $venvPython -m pip install --disable-pip-version-check -r requirements.txt }
}

if ($App -or $Staging) {
    $desktop = Join-Path $repo 'desktop'
    $exe = Join-Path $repo 'build-output\desktop\GitVizWorkspace-win32-x64\GitVizWorkspace.exe'
    $stagingScript = Join-Path $repo 'tools\vm\staging_stack.ps1'
    $origin = 'https://localhost:8443'
    $stagingDir = Join-Path $repo '.validation\staging'
    $certificatePem = Join-Path $stagingDir 'tls\server.pem'
    $certificateCer = Join-Path $stagingDir 'tls\server.cer'
    # 앱에 넣은 인증서의 해시. 서버 인증서가 바뀌면(14일마다 갱신) 앱을 다시 빌드해야 한다.
    $builtWith = Join-Path $stagingDir 'app-certificate.sha256'

    if ($App) {
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
    }

    if ($Staging) {
        # 앱보다 먼저 서버를 준비한다: 앱 빌드에 이 서버의 인증서를 넣는다.
        Step '로컬 HTTPS 스테이징 서버 (https://localhost:8443)'
        $listening = { [bool](Get-NetTCPConnection -LocalPort 8443 -State Listen -ErrorAction SilentlyContinue) }
        $expiring = (Test-Path -LiteralPath $certificateCer) -and ([Security.Cryptography.X509Certificates.X509Certificate2]::new($certificateCer).NotAfter -lt (Get-Date).AddDays(2))
        if ($expiring -and (& $listening)) {
            Write-Host '서버 인증서가 곧 만료돼 서버를 내리고 새 인증서로 다시 띄웁니다.' -ForegroundColor Yellow
            Invoke-Checked '스테이징 중지(Down)' { pwsh -NoProfile -File $stagingScript -Mode Down | Out-Null }
        }
        if (& $listening) { Write-Host '이미 실행 중입니다.' }
        else {
            Invoke-Checked '스테이징 준비(Prepare)' { pwsh -NoProfile -File $stagingScript -Mode Prepare }
            $userEnv = Join-Path $stagingDir 'staging.env'
            if (-not (Test-Path -LiteralPath $userEnv) -or (Get-Item -LiteralPath $userEnv).Length -eq 0) {
                Fail "$userEnv 에 GITHUB_CLIENT_ID=와 GITHUB_CLIENT_SECRET=를 적고 다시 실행하세요(OAuth App 콜백: $origin/api/auth/callback)." 2
            }
            # 재부팅 등으로 끝난 프로세스의 기록만 지운다(살아 있으면 그대로 둔다).
            foreach ($name in 'server', 'worker') {
                $file = Join-Path $stagingDir "pids\$name.pid"
                if ((Test-Path -LiteralPath $file) -and -not (Get-Process -Id ([int](Get-Content -LiteralPath $file)) -ErrorAction SilentlyContinue)) { Remove-Item -LiteralPath $file -Force }
            }
            # 서버와 worker는 각자 새 창에서 계속 돈다. 끄려면: pwsh tools/vm/staging_stack.ps1 -Mode Down
            Start-Process pwsh -ArgumentList '-NoProfile', '-File', $stagingScript, '-Mode', 'Up'
            for ($i = 0; $i -lt 30 -and -not (& $listening); $i++) { Start-Sleep -Seconds 2 }
            if (-not (& $listening)) { Fail '스테이징 서버가 60초 안에 시작되지 않았습니다. .validation\staging\logs를 확인하세요.' }
            Start-Process pwsh -ArgumentList '-NoProfile', '-File', $stagingScript, '-Mode', 'Worker'
        }
    }

    if ($App) {
        Step '앱 빌드와 패키징'
        if ($Staging) {
            # 서버 주소와 자체 서명 인증서를 앱에 넣는다: 탐색기에서 더블클릭해도 이 서버에 연결된다.
            $env:GITVIZ_BUILD_SERVER_ORIGIN = $origin
            $env:GITVIZ_BUILD_SERVER_CA = $certificatePem
        } else { Remove-Item Env:GITVIZ_BUILD_SERVER_CA -ErrorAction SilentlyContinue }
        try {
            Invoke-Checked 'npm run build' { npm --prefix desktop run build }
            Invoke-Checked 'npm run package' { npm --prefix desktop run package }
        } finally {
            if ($Staging) { Remove-Item Env:GITVIZ_BUILD_SERVER_ORIGIN, Env:GITVIZ_BUILD_SERVER_CA -ErrorAction SilentlyContinue }
        }
        if (-not (Test-Path -LiteralPath $exe)) { Fail "패키징 결과가 없습니다: $exe" }
        if ($Staging) { Set-Content -LiteralPath $builtWith -Value (Get-FileHash -LiteralPath $certificatePem -Algorithm SHA256).Hash }
        else { Remove-Item -LiteralPath $builtWith -Force -ErrorAction SilentlyContinue }
        Write-Host "앱: $exe" -ForegroundColor Green
    } elseif (-not (Test-Path -LiteralPath $exe)) {
        Write-Host '앱이 아직 없습니다. 처음 한 번은 -App -Staging으로 빌드하세요.' -ForegroundColor Yellow
    } elseif (-not (Test-Path -LiteralPath $builtWith)) {
        Write-Host '지금 앱은 서버 주소 없이 빌드됐습니다. 더블클릭으로 쓰려면 -App -Staging으로 다시 빌드하세요.' -ForegroundColor Yellow
    } elseif ((Get-Content -LiteralPath $builtWith) -ne (Get-FileHash -LiteralPath $certificatePem -Algorithm SHA256).Hash) {
        Write-Host '서버 인증서가 갱신돼 지금 앱은 서버에 연결되지 않습니다. -App -Staging으로 다시 빌드하세요.' -ForegroundColor Yellow
    }

    if ($Run) {
        Step '앱 실행'
        if (-not (Test-Path -LiteralPath $exe)) { Fail "앱이 없습니다: $exe (-App으로 빌드하세요)" }
        if ($Staging) {
            # 서버 주소 없이 빌드한 앱만 이 값을 쓴다(-App -Staging으로 빌드한 앱은 둘 다 필요 없다).
            $env:GITVIZ_SERVER_ORIGIN = $origin
            $env:NODE_EXTRA_CA_CERTS = $certificatePem
        }
        Start-Process -FilePath $exe
        if ($Staging) { Write-Host "앱을 $origin 에 연결해 실행했습니다. 오른쪽 위 로그인을 누르세요." -ForegroundColor Green }
    } elseif ($Staging) {
        Write-Host ''
        Write-Host "준비 완료. 서버가 켜져 있는 동안 탐색기에서 $exe 를 더블클릭하면 됩니다." -ForegroundColor Green
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
