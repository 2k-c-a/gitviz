#Requires -Version 7.0
param([string]$PostgresBin = 'C:\Program Files\PostgreSQL\18\bin')
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$run = Join-Path $root ('.validation/p09/services-' + [guid]::NewGuid().ToString('N'))
$pgPort = 55439; $redisPort = 56389
foreach ($port in @($pgPort, $redisPort)) {
    if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) { throw 'Fixture port is already in use.' }
}
foreach ($name in @('initdb.exe', 'pg_ctl.exe', 'createdb.exe')) {
    if (!(Test-Path -LiteralPath (Join-Path $PostgresBin $name) -PathType Leaf)) { throw 'Existing PostgreSQL18 binaries required; no installation is performed.' }
}
& docker.exe image inspect redis:7 *> $null
if ($LASTEXITCODE -ne 0) { throw 'Existing redis:7 image required; no pull or installation is performed.' }
New-Item -ItemType Directory -Path $run | Out-Null
$owner = [Security.Principal.WindowsIdentity]::GetCurrent().Name
& icacls.exe $run /inheritance:r /grant:r "${owner}:(OI)(CI)F" 'SYSTEM:(OI)(CI)F' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Fixture ACL failed.' }
$secret = [Convert]::ToBase64String([Security.Cryptography.RandomNumberGenerator]::GetBytes(32))
$passwordFile = Join-Path $run 'password.txt'
[IO.File]::WriteAllText($passwordFile, $secret)
$cluster = Join-Path $run 'data'
$database = 'gitviz_test_p09_' + [guid]::NewGuid().ToString('N')
$containerName = 'gitviz-p09-' + [guid]::NewGuid().ToString('N')
$containerId = $null; $started = $false
$previousPassword = $env:PGPASSWORD
$summary = [Collections.Generic.List[object]]::new()
function Control([string[]]$Arguments, [string]$Name) {
    $control = Start-Process -FilePath (Join-Path $PostgresBin 'pg_ctl.exe') -ArgumentList $Arguments -PassThru -WindowStyle Hidden -RedirectStandardOutput (Join-Path $run "$Name.log") -RedirectStandardError (Join-Path $run "$Name-error.log")
    if (!$control.WaitForExit(60000)) { $control.Kill(); throw 'Fixture control timed out.' }
    if ($control.ExitCode -ne 0) { throw 'Fixture control failed; evidence retained.' }
}
function Test-Step([string]$Script, [string[]]$Arguments = @()) {
    $info = [Diagnostics.ProcessStartInfo]::new()
    $info.FileName = Join-Path $root 'venv/Scripts/python.exe'; $info.WorkingDirectory = $root
    $info.UseShellExecute = $false; $info.CreateNoWindow = $true; $info.RedirectStandardOutput = $true; $info.RedirectStandardError = $true
    foreach ($name in @($info.Environment.Keys)) {
        if ($name -match '^(AI_|GITVIZ_|DATABASE_|CELERY_|REDIS_|GITHUB_TOKEN$|OPENAI_API_KEY$|ANTHROPIC_API_KEY$|GEMINI_API_KEY$|PYTHONPATH$)') { $info.Environment.Remove($name) | Out-Null }
    }
    $encoded = [Uri]::EscapeDataString($secret)
    $info.Environment['GITVIZ_TEST_DATABASE_URL'] = "postgresql://gitviz_test:${encoded}@127.0.0.1:${pgPort}/$database"
    $info.Environment['GITVIZ_TEST_REDIS_URL'] = "redis://127.0.0.1:${redisPort}/0"
    $info.Environment['REDIS_URL'] = $info.Environment['GITVIZ_TEST_REDIS_URL']
    # Server modules build their engine at import time; point them only at the owned fixture database.
    $info.Environment['DATABASE_URL'] = $info.Environment['GITVIZ_TEST_DATABASE_URL']
    $info.Environment['DATABASE_URL_ASYNC'] = $info.Environment['GITVIZ_TEST_DATABASE_URL'].Replace('postgresql://', 'postgresql+asyncpg://')
    $info.Environment['PYTHON_DOTENV_DISABLED'] = '1'; $info.Environment['PYTHONUTF8'] = '1'; $info.Environment['PYTHONDONTWRITEBYTECODE'] = '1'
    $info.Environment['HTTP_PROXY'] = 'http://127.0.0.1:9'; $info.Environment['HTTPS_PROXY'] = 'http://127.0.0.1:9'; $info.Environment['NO_PROXY'] = 'localhost,127.0.0.1,::1'
    foreach ($item in @('-X', 'utf8', '-B', "scripts/$Script") + $Arguments) { $info.ArgumentList.Add($item) }
    $process = [Diagnostics.Process]::Start($info)
    $stdout = $process.StandardOutput.ReadToEndAsync(); $stderr = $process.StandardError.ReadToEndAsync()
    $timedOut = !$process.WaitForExit(900000)
    if ($timedOut) { $process.Kill($true); $process.WaitForExit() }
    $code = if ($timedOut) { 124 } else { $process.ExitCode }
    $log = "STDOUT:`n$($stdout.GetAwaiter().GetResult())`nSTDERR:`n$($stderr.GetAwaiter().GetResult())"
    $log | Set-Content -LiteralPath (Join-Path $run "$Script.log") -Encoding utf8
    $process.Dispose()
    $summary.Add(@{ script=$Script; exit=$code })
    ConvertTo-Json -InputObject $summary | Set-Content -LiteralPath (Join-Path $run 'summary.json') -Encoding utf8
    if ($code -ne 0) { throw "Fixture test failed: $Script; evidence retained." }
}
try {
    & (Join-Path $PostgresBin 'initdb.exe') -D $cluster -U gitviz_test --auth=scram-sha-256 --encoding=UTF8 --locale=C "--pwfile=$passwordFile" *> (Join-Path $run 'initdb.log')
    if ($LASTEXITCODE -ne 0) { throw 'Fixture initdb failed.' }
    Remove-Item -LiteralPath $passwordFile
    Control @('-D', "`"$cluster`"", '-l', "`"$(Join-Path $run 'postgres.log')`"", '-o', "`"-h 127.0.0.1 -p $pgPort`"", '-w', 'start') 'start'
    $started = $true
    $env:PGPASSWORD = $secret
    & (Join-Path $PostgresBin 'createdb.exe') -h 127.0.0.1 -p $pgPort -U gitviz_test $database
    if ($LASTEXITCODE -ne 0) { throw 'Fixture database creation failed.' }
    $id = & docker.exe run -d --pull=never --name $containerName --label gitviz.fixture=p09 -p "127.0.0.1:${redisPort}:6379" redis:7 redis-server --save '' --appendonly no
    if ($LASTEXITCODE -ne 0 -or $id -notmatch '^[0-9a-f]{64}$') { throw 'Owned fixture Redis start failed.' }
    $containerId = $id.Trim()
    Test-Step 'test_desktop_auth.py' @('--postgres')
    foreach ($script in @('test_desktop_migration.py', 'test_backend_improvements.py', 'test_repository_snapshots.py', 'test_ai_history_context.py', 'test_ai_queue_integration.py', 'verify_ai_mvp_integration.py')) { Test-Step $script }
    @{ status='passed'; postgresPort=$pgPort; redisPort=$redisPort; database=$database; scripts=$summary.Count; productionData=$false; externalProviders=$false } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $run 'result.json') -Encoding utf8
} finally {
    $cleanupFailed = $false
    if ($containerId) {
        & docker.exe logs $containerId *> (Join-Path $run 'redis.log')
        & docker.exe stop --time 10 $containerId *> (Join-Path $run 'redis-stop.log')
        if ($LASTEXITCODE -ne 0) { $cleanupFailed = $true }
    }
    if ($started -or (Test-Path -LiteralPath (Join-Path $cluster 'postmaster.pid'))) {
        try { Control @('-D', "`"$cluster`"", '-m', 'fast', '-w', 'stop') 'stop' } catch { $cleanupFailed = $true }
    }
    $env:PGPASSWORD = $previousPassword
    if (Test-Path -LiteralPath $passwordFile) { Remove-Item -LiteralPath $passwordFile }
    $secret = $null
    @{ stopped=(!$cleanupFailed); retainedContainer=$containerId; cluster=$cluster } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $run 'cleanup.json') -Encoding utf8
    Write-Output "Evidence: $run"
    if ($cleanupFailed) { throw 'Owned fixture service shutdown needs attention; see cleanup logs.' }
}
