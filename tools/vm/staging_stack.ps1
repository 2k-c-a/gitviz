[CmdletBinding()]
param([ValidateSet('Prepare', 'Up', 'Worker', 'Down', 'Status')][string]$Mode = 'Status')
# Isolated staging stack for P03/N06 acceptance: own PostgreSQL cluster, own Redis, own secrets, HTTPS on loopback.
# Never touches the development database or .env. Secrets live only under .validation\staging (git-ignored).
$ErrorActionPreference = 'Stop'
$repo = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$root = Join-Path $repo '.validation\staging'
$python = Join-Path $repo '.venv\Scripts\python.exe'
# PostgreSQL and Redis stay below the Windows dynamic port range (49152-65535): Hyper-V/WSL/Docker reserve blocks there
# (netsh excludedportrange), and Redis on 56390 once landed in such a block (bind: Permission denied).
# 5440/6390 also avoid the dev compose ports 5432/6379.
$pgPort = 5440; $redisPort = 6390; $httpsPort = 8443
$origin = "https://localhost:$httpsPort"
$cluster = Join-Path $root 'pg'; $tls = Join-Path $root 'tls'; $logs = Join-Path $root 'logs'; $pids = Join-Path $root 'pids'
$generated = Join-Path $root 'generated.env'; $userEnv = Join-Path $root 'staging.env'
New-Item -ItemType Directory -Force $root, $tls, $logs, $pids | Out-Null
# PostgreSQL 16+ from Program Files: the version an existing cluster was made with, else the newest installed.
$pgBase = Join-Path $env:ProgramFiles 'PostgreSQL'
$pgVersionFile = Join-Path $cluster 'PG_VERSION'
$pgBin = if (Test-Path $pgVersionFile) { Join-Path $pgBase "$((Get-Content -LiteralPath $pgVersionFile -TotalCount 1).Trim())\bin" } else {
    Get-ChildItem $pgBase -Directory -ErrorAction SilentlyContinue | Where-Object { $_.Name -match '^\d+$' -and [int]$_.Name -ge 16 -and (Test-Path (Join-Path $_.FullName 'bin\initdb.exe')) } |
        Sort-Object { [int]$_.Name } -Descending | Select-Object -First 1 | ForEach-Object { Join-Path $_.FullName 'bin' }
}

function Read-EnvFile([string]$Path) {
    $values = @{}
    if (Test-Path -LiteralPath $Path) { foreach ($line in Get-Content -LiteralPath $Path) { if ($line -match '^\s*([A-Z][A-Z0-9_]*)=(.*)$') { $values[$Matches[1]] = $Matches[2].Trim() } } }
    return $values
}
function New-Hex([int]$Bytes) { -join ([Security.Cryptography.RandomNumberGenerator]::GetBytes($Bytes) | ForEach-Object { $_.ToString('x2') }) }
# Loopback only: the VM test's portproxy listens on the Default Switch address of the same port and is not a conflict.
function Is-Listening([int]$Port) { [bool](Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Where-Object { $_.LocalAddress -in '127.0.0.1', '::1', '0.0.0.0', '::' }) }

# Child processes get a minimal environment plus explicit values only, so no development setting can leak in.
function Start-Clean([string]$File, [string[]]$Arguments, [hashtable]$Extra, [string]$Name, [switch]$Wait) {
    $out = Join-Path $logs "$Name.out.log"; $err = Join-Path $logs "$Name.err.log"
    if ($Wait) {
        $info = [Diagnostics.ProcessStartInfo]::new($File)
        foreach ($argument in $Arguments) { $info.ArgumentList.Add($argument) }
        $info.RedirectStandardOutput = $true; $info.RedirectStandardError = $true
    } else {
        # Long-running child: cmd.exe redirects straight into the log files so they survive this script and keep filling.
        $info = [Diagnostics.ProcessStartInfo]::new((Join-Path $env:SystemRoot 'System32\cmd.exe'))
        $quoted = (@($File) + $Arguments | ForEach-Object { '"' + $_ + '"' }) -join ' '
        $info.Arguments = "/c `"$quoted > `"$out`" 2> `"$err`"`""
    }
    $info.WorkingDirectory = $repo; $info.UseShellExecute = $false; $info.CreateNoWindow = $true
    $info.Environment.Clear()
    foreach ($key in 'SystemRoot', 'windir', 'PATH', 'TEMP', 'TMP', 'USERPROFILE', 'LOCALAPPDATA', 'APPDATA') { $info.Environment[$key] = [Environment]::GetEnvironmentVariable($key) }
    $info.Environment['PYTHON_DOTENV_DISABLED'] = '1'; $info.Environment['PYTHONUTF8'] = '1'; $info.Environment['PYTHONDONTWRITEBYTECODE'] = '1'
    foreach ($key in $Extra.Keys) { $info.Environment[$key] = [string]$Extra[$key] }
    $process = [Diagnostics.Process]::Start($info)
    if ($Wait) {
        $copyOut = $process.StandardOutput.BaseStream.CopyToAsync([IO.File]::Create($out)); $copyErr = $process.StandardError.BaseStream.CopyToAsync([IO.File]::Create($err))
        $process.WaitForExit(); [void]$copyOut.Wait(5000); [void]$copyErr.Wait(5000); return $process.ExitCode
    }
    # Record the real child (not cmd.exe) so Down stops the server itself.
    Start-Sleep -Seconds 2
    # cmd.exe also owns a conhost.exe; recording that one would leave the real child running after Down.
    $child = Get-CimInstance Win32_Process -Filter "ParentProcessId=$($process.Id) AND Name<>'conhost.exe'" | Select-Object -First 1
    $id = if ($child) { $child.ProcessId } else { $process.Id }
    Set-Content -LiteralPath (Join-Path $pids "$Name.pid") -Value $id
    return $id
}
function Server-Environment {
    $g = Read-EnvFile $generated; $u = Read-EnvFile $userEnv
    $dbUrl = "postgresql://gitviz_staging:$($g.PG_PASSWORD)@127.0.0.1:$pgPort/gitviz_staging"
    return @{
        DATABASE_URL = $dbUrl; DATABASE_URL_ASYNC = $dbUrl.Replace('postgresql://', 'postgresql+asyncpg://'); REDIS_URL = "redis://127.0.0.1:$redisPort/0"
        SESSION_SECRET_KEY = $g.SESSION_SECRET_KEY; TOKEN_ENCRYPTION_KEY = $g.TOKEN_ENCRYPTION_KEY
        FRONTEND_URL = $origin; SESSION_COOKIE_SECURE = '1'; DESKTOP_AUTH_ENABLED = '1'; DESKTOP_PUBLIC_ORIGIN = $origin; DESKTOP_AUTH_RESPONSE_KEY = $g.DESKTOP_AUTH_RESPONSE_KEY
        GITHUB_CLIENT_ID = $u.GITHUB_CLIENT_ID; GITHUB_CLIENT_SECRET = $u.GITHUB_CLIENT_SECRET
        # Voice chat across networks (AD-28): public STUN unless staging.env says otherwise; TURN only when staging.env sets it.
        VOICE_STUN_URLS = $(if ($u.ContainsKey('VOICE_STUN_URLS')) { $u.VOICE_STUN_URLS } else { 'stun:stun.l.google.com:19302,stun:stun1.l.google.com:19302' })
        VOICE_TURN_URLS = $u.VOICE_TURN_URLS; VOICE_TURN_SECRET = $u.VOICE_TURN_SECRET
    }
}
# PATH first, then the MSI install (winget Redis.Redis), then portable winget packages.
function Find-Redis {
    $onPath = (Get-Command redis-server.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1).Source
    if ($onPath) { return $onPath }
    $msi = Join-Path $env:ProgramFiles 'Redis\redis-server.exe'
    if (Test-Path $msi) { return $msi }
    (Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages" -Recurse -Filter redis-server.exe -ErrorAction SilentlyContinue | Select-Object -First 1).FullName
}
function Pg([string[]]$Arguments) { & (Join-Path $pgBin 'pg_ctl.exe') @Arguments; if ($LASTEXITCODE -ne 0) { throw "pg_ctl $($Arguments -join ' ') failed." } }

switch ($Mode) {
    'Prepare' {
        foreach ($need in 'initdb.exe', 'pg_ctl.exe', 'createdb.exe') { if (-not $pgBin -or -not (Test-Path (Join-Path $pgBin $need))) { throw "PostgreSQL 16+ $need not found under $pgBase; nothing is installed." } }
        $redis = Find-Redis; if (-not $redis) { throw "redis-server.exe not found on PATH, under $env:ProgramFiles\Redis or in winget packages." }
        foreach ($port in $pgPort, $redisPort) { if ((Is-Listening $port) -and -not (Test-Path (Join-Path $cluster 'postmaster.pid'))) { throw "Port $port is already in use." } }
        if (-not (Test-Path $generated)) {
            @("PG_PASSWORD=$(New-Hex 16)", "SESSION_SECRET_KEY=$(New-Hex 32)", "TOKEN_ENCRYPTION_KEY=$(New-Hex 32)", "DESKTOP_AUTH_RESPONSE_KEY=$(New-Hex 32)") | Set-Content -LiteralPath $generated -Encoding ascii
        }
        $g = Read-EnvFile $generated
        if (-not (Test-Path (Join-Path $cluster 'PG_VERSION'))) {
            $pwFile = Join-Path $root 'pg.pw'; Set-Content -LiteralPath $pwFile -Value $g.PG_PASSWORD -Encoding ascii -NoNewline
            & (Join-Path $pgBin 'initdb.exe') -D $cluster -U gitviz_staging --auth=scram-sha-256 --encoding=UTF8 --locale=C "--pwfile=$pwFile" *> (Join-Path $logs 'initdb.log')
            Remove-Item -LiteralPath $pwFile -Force
            if ($LASTEXITCODE -ne 0) { throw 'initdb failed.' }
        }
        if (-not (Is-Listening $pgPort)) { Pg @('-D', $cluster, '-l', (Join-Path $logs 'postgres.log'), '-o', "-h 127.0.0.1 -p $pgPort", '-w', 'start') }
        $env:PGPASSWORD = $g.PG_PASSWORD
        try {
            $exists = & (Join-Path $pgBin 'psql.exe') -h 127.0.0.1 -p $pgPort -U gitviz_staging -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='gitviz_staging'"
            if (-not $exists) { & (Join-Path $pgBin 'createdb.exe') -h 127.0.0.1 -p $pgPort -U gitviz_staging gitviz_staging; if ($LASTEXITCODE -ne 0) { throw 'createdb failed.' } }
        } finally { Remove-Item Env:\PGPASSWORD -ErrorAction SilentlyContinue }
        if (-not (Is-Listening $redisPort)) {
            # Settings go in a config file: cmd.exe drops the empty argument of `--save ""`, and Redis then exits at startup.
            $redisConf = Join-Path $root 'redis.conf'
            @("port $redisPort", 'bind 127.0.0.1', 'save ""', 'appendonly no', "dir `"$($root.Replace('\', '/'))`"") | Set-Content -LiteralPath $redisConf -Encoding ascii
            [void](Start-Clean $redis @($redisConf) @{} 'redis')
            for ($i = 0; $i -lt 10 -and -not (Is-Listening $redisPort); $i++) { Start-Sleep 1 }
            if (-not (Is-Listening $redisPort)) { throw "Redis did not start on port $redisPort; see $logs\redis.out.log and redis.err.log" }
        }
        $code = Start-Clean $python @('-m', 'alembic', 'upgrade', 'head') (Server-Environment) 'alembic' -Wait
        if ($code -ne 0) { throw "alembic upgrade failed (exit $code); see $logs\alembic.err.log" }
        # The certificate lives 14 days; renew it when missing or within 2 days of expiry (apps built with it must be rebuilt).
        $cer = Join-Path $tls 'server.cer'
        $renew = -not (Test-Path (Join-Path $tls 'server.pem')) -or -not (Test-Path $cer) -or ([Security.Cryptography.X509Certificates.X509Certificate2]::new($cer).NotAfter -lt (Get-Date).AddDays(2))
        if ($renew) {
            $script = @'
import datetime, ipaddress, sys
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
out = sys.argv[1]
key = ec.generate_private_key(ec.SECP256R1())
name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'GitViz staging localhost')])
now = datetime.datetime.now(datetime.timezone.utc)
cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number())
    .not_valid_before(now - datetime.timedelta(minutes=5)).not_valid_after(now + datetime.timedelta(days=14))
    .add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost'), x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]), False)
    .add_extension(x509.BasicConstraints(ca=True, path_length=0), True)
    .add_extension(x509.KeyUsage(True, False, False, False, False, True, False, False, False), True)
    .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), False)
    .sign(key, hashes.SHA256()))
open(out + '/server.pem', 'wb').write(cert.public_bytes(serialization.Encoding.PEM))
open(out + '/server.key', 'wb').write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
open(out + '/server.cer', 'wb').write(cert.public_bytes(serialization.Encoding.DER))
print(cert.fingerprint(hashes.SHA256()).hex())
'@
            $script | & $python -I - $tls
            if ($LASTEXITCODE -ne 0) { throw 'Certificate generation failed.' }
        }
        $filled = (Get-Item -LiteralPath $userEnv -ErrorAction SilentlyContinue).Length -gt 0
        [ordered]@{ prepared = $true; postgresPort = $pgPort; redisPort = $redisPort; origin = $origin; certificate = (Join-Path $tls 'server.cer'); stagingEnvHasContent = $filled } | ConvertTo-Json
    }
    'Up' {
        $u = Read-EnvFile $userEnv
        if (-not $u.GITHUB_CLIENT_ID -or -not $u.GITHUB_CLIENT_SECRET) { throw "Fill GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET in $userEnv first." }
        if (Is-Listening $httpsPort) { throw "Port $httpsPort is already in use." }
        $id = Start-Clean $python @('-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', "$httpsPort", '--ssl-keyfile', (Join-Path $tls 'server.key'), '--ssl-certfile', (Join-Path $tls 'server.pem')) (Server-Environment) 'server'
        Start-Sleep 6
        # The venv launcher spawns the real interpreter; record whichever process owns the loopback listener.
        $owner = Get-NetTCPConnection -LocalPort $httpsPort -State Listen -ErrorAction SilentlyContinue | Where-Object LocalAddress -eq '127.0.0.1' | Select-Object -First 1
        if ($owner) { $id = $owner.OwningProcess; Set-Content -LiteralPath (Join-Path $pids 'server.pid') -Value $id }
        [ordered]@{ serverPid = $id; listening = (Is-Listening $httpsPort); origin = $origin } | ConvertTo-Json
    }
    'Worker' {
        # Analysis jobs are Celery tasks on the staging Redis; same isolated environment as the server.
        if (Test-Path (Join-Path $pids 'worker.pid')) { throw 'Worker already recorded; run Down first.' }
        $id = Start-Clean $python @('-m', 'celery', '-A', 'app.core.celery_app:celery', 'worker', '--loglevel=info', '--pool=solo') (Server-Environment) 'worker'
        [ordered]@{ workerLauncherPid = $id; log = (Join-Path $logs 'worker.err.log') } | ConvertTo-Json
    }
    'Down' {
        foreach ($name in 'server', 'worker', 'redis') {
            $file = Join-Path $pids "$name.pid"
            # taskkill /T also ends the interpreter behind a venv launcher.
            if (Test-Path $file) { $id = [int](Get-Content $file); & taskkill.exe /PID $id /T /F *> $null; Remove-Item $file -Force }
        }
        if (Test-Path (Join-Path $cluster 'postmaster.pid')) { Pg @('-D', $cluster, '-m', 'fast', '-w', 'stop') }
        [ordered]@{ stopped = $true } | ConvertTo-Json
    }
    'Status' {
        [ordered]@{ postgres = (Is-Listening $pgPort); redis = (Is-Listening $redisPort); https = (Is-Listening $httpsPort); prepared = (Test-Path (Join-Path $cluster 'PG_VERSION'));
            stagingEnvHasContent = ((Get-Item -LiteralPath $userEnv -ErrorAction SilentlyContinue).Length -gt 0) } | ConvertTo-Json
    }
}
