[CmdletBinding()]
param([ValidateSet('Prepare', 'Up', 'Worker', 'Down', 'Status')][string]$Mode = 'Status')
# Isolated staging stack for P03/N06 acceptance: own PostgreSQL cluster, own Redis, own secrets, HTTPS on loopback.
# Never touches the development database or .env. Secrets live only under .validation\staging (git-ignored).
$ErrorActionPreference = 'Stop'
$repo = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$root = Join-Path $repo '.validation\staging'
$pgBin = 'C:\Program Files\PostgreSQL\18\bin'
$python = Join-Path $repo 'venv\Scripts\python.exe'
$pgPort = 55440; $redisPort = 56390; $httpsPort = 8443
$origin = "https://localhost:$httpsPort"
$cluster = Join-Path $root 'pg'; $tls = Join-Path $root 'tls'; $logs = Join-Path $root 'logs'; $pids = Join-Path $root 'pids'
$generated = Join-Path $root 'generated.env'; $userEnv = Join-Path $root 'staging.env'
New-Item -ItemType Directory -Force $root, $tls, $logs, $pids | Out-Null

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
    $child = Get-CimInstance Win32_Process -Filter "ParentProcessId=$($process.Id)" | Select-Object -First 1
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
    }
}
function Find-Redis { (Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages" -Recurse -Filter redis-server.exe -ErrorAction SilentlyContinue | Select-Object -First 1).FullName }
function Pg([string[]]$Arguments) { & (Join-Path $pgBin 'pg_ctl.exe') @Arguments; if ($LASTEXITCODE -ne 0) { throw "pg_ctl $($Arguments -join ' ') failed." } }

switch ($Mode) {
    'Prepare' {
        foreach ($need in 'initdb.exe', 'pg_ctl.exe', 'createdb.exe') { if (-not (Test-Path (Join-Path $pgBin $need))) { throw 'Existing PostgreSQL 18 binaries required; nothing is installed.' } }
        $redis = Find-Redis; if (-not $redis) { throw 'redis-server.exe not found.' }
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
        if (-not (Is-Listening $redisPort)) { [void](Start-Clean $redis @('--port', "$redisPort", '--bind', '127.0.0.1', '--save', '""', '--appendonly', 'no') @{} 'redis') ; Start-Sleep 2 }
        $code = Start-Clean $python @('-m', 'alembic', 'upgrade', 'head') (Server-Environment) 'alembic' -Wait
        if ($code -ne 0) { throw "alembic upgrade failed (exit $code); see $logs\alembic.err.log" }
        if (-not (Test-Path (Join-Path $tls 'server.pem'))) {
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
