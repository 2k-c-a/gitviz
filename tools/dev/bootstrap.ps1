<#
  GitViz 개발 환경 원클릭 준비/실행 (웹 서버 + Celery).
  처음 clone한 뒤 프로젝트 어디서든 실행한다:  pwsh tools/dev/bootstrap.ps1 -LocalDefaults -Run

  하는 일: 도구 확인 -> .venv 생성 -> requirements.txt 설치 -> .env 준비 -> docker compose db/redis 기동
           -> alembic upgrade head -> (-Run이면) uvicorn과 celery worker를 새 창으로 실행.
  하지 않는 일: 이미 있는 .env를 읽거나 수정하지 않는다. GitHub OAuth 값은 만들 수 없다(로그인은 직접 발급한 값이 필요).
  데스크톱 앱 개발 실행은 docs/development/DEVELOPMENT_SETUP.md를 따른다.
#>
[CmdletBinding()]
param(
    [switch]$LocalDefaults,   # .env를 이번 실행에서 새로 만든 경우에만 로컬 compose용 값과 임의 비밀키를 채운다
    [switch]$Run,             # 준비 후 uvicorn(8000)과 celery worker를 새 창으로 실행
    [switch]$SkipDocker,      # docker compose 기동을 건너뛴다(DB/Redis를 직접 준비한 경우)
    [switch]$SkipMigrate      # alembic upgrade head를 건너뛴다
)
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '../..')).Path
Set-Location -LiteralPath $repo

function Step([string]$Text) { Write-Host "==> $Text" -ForegroundColor Cyan }
function Fail([string]$Text, [int]$Code = 1) { Write-Host "중단: $Text" -ForegroundColor Red; exit $Code }

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
if (-not $SkipDocker -and -not (Get-Command docker -ErrorAction SilentlyContinue)) { Fail 'Docker가 없습니다. DB/Redis를 직접 준비했다면 -SkipDocker를 쓰세요.' }

Step '.venv 준비와 의존성 설치'
$venvPython = Join-Path $repo '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $venvPython)) {
    & $pythonCommand[0] @(@($pythonCommand | Select-Object -Skip 1) + @('-m', 'venv', '.venv'))
    if ($LASTEXITCODE -ne 0) { Fail '.venv 생성 실패' }
}
& $venvPython -m pip install --disable-pip-version-check -r requirements.txt
if ($LASTEXITCODE -ne 0) { Fail 'pip install 실패' }

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
    Write-Host '준비 완료. 실행하려면 -Run을 붙여 다시 실행하거나 README의 "로컬 실행" 명령을 따르세요.' -ForegroundColor Green
}
