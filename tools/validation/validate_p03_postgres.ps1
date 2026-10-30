param([string]$PostgresBin = 'C:\Program Files\PostgreSQL\18\bin', [switch]$CoreOnly)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$run = Join-Path $root ('.validation\p03\pg-' + [guid]::NewGuid().ToString('N'))
$port = 55433
if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) { throw 'Fixture port is in use.' }
foreach ($name in @('initdb.exe','pg_ctl.exe','createdb.exe')) {
    if (!(Test-Path -LiteralPath (Join-Path $PostgresBin $name))) { throw 'Existing PostgreSQL runtime is required.' }
}
New-Item -ItemType Directory -Path $run | Out-Null
$identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
& icacls.exe $run /inheritance:r /grant:r "${identity}:(OI)(CI)F" 'SYSTEM:(OI)(CI)F' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Fixture ACL failed.' }
$secret = [Convert]::ToBase64String([Security.Cryptography.RandomNumberGenerator]::GetBytes(32))
$passwordFile = Join-Path $run 'password.txt'
[IO.File]::WriteAllText($passwordFile, $secret)
$cluster = Join-Path $run 'data'
$started = $false
function Invoke-FixtureControl([string[]]$ControlArgs, [string]$LogName) {
    # Wait for pg_ctl itself, not its long-running PostgreSQL descendants.
    $control = Start-Process -FilePath (Join-Path $PostgresBin 'pg_ctl.exe') -ArgumentList $ControlArgs -PassThru -WindowStyle Hidden -RedirectStandardOutput (Join-Path $run "$LogName.log") -RedirectStandardError (Join-Path $run "$LogName-error.log")
    if (!$control.WaitForExit(60000)) { $control.Kill(); throw 'Fixture control timed out.' }
    if ($control.ExitCode -ne 0) { throw 'Fixture control failed; log preserved.' }
}
try {
    & (Join-Path $PostgresBin 'initdb.exe') -D $cluster -U gitviz_test --auth=scram-sha-256 --encoding=UTF8 --locale=C "--pwfile=$passwordFile" *> (Join-Path $run 'initdb.log')
    if ($LASTEXITCODE -ne 0) { throw 'Fixture initdb failed; log preserved.' }
    Remove-Item -LiteralPath $passwordFile
    Invoke-FixtureControl @('-D', "`"$cluster`"", '-l', "`"$(Join-Path $run 'postgres.log')`"", '-o', "`"-h 127.0.0.1 -p $port`"", '-w', 'start') 'start'
    $started = $true
    $env:PGPASSWORD = $secret
    & (Join-Path $PostgresBin 'createdb.exe') -h 127.0.0.1 -p $port -U gitviz_test gitviz_test_p03
    if ($LASTEXITCODE -ne 0) { throw 'Fixture database creation failed.' }
    $encoded = [Uri]::EscapeDataString($secret)
    $env:GITVIZ_TEST_DATABASE_URL = "postgresql://gitviz_test:${encoded}@127.0.0.1:${port}/gitviz_test_p03"
    # Pin fixture policy; inherited developer configuration must not change test expectations.
    $env:AI_DAILY_JOB_LIMIT = '30'
    $env:PYTHON_DOTENV_DISABLED = '1'
    & (Join-Path $root 'venv\Scripts\python.exe') -B (Join-Path $root 'scripts\test_desktop_auth.py') --postgres *> (Join-Path $run 'auth-tests.log')
    if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL auth tests failed; log preserved.' }
    & (Join-Path $root 'venv\Scripts\python.exe') -B (Join-Path $root 'scripts\test_desktop_migration.py') *> (Join-Path $run 'migration-tests.log')
    if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL migration tests failed; log preserved.' }
    if (!$CoreOnly) {
    & (Join-Path $root 'venv\Scripts\python.exe') -B (Join-Path $root 'scripts\test_ai_schema.py') *> (Join-Path $run 'ai-schema-tests.log')
    if ($LASTEXITCODE -ne 0) { throw 'Existing AI schema regression failed; log preserved.' }
    foreach ($scriptName in @('test_backend_improvements.py','test_analytics_calculations.py','test_metric_policy.py','test_repository_snapshots.py','test_lightweight_score.py','test_ai_input_api.py','test_ai_multi_provider_api.py','test_ai_api.py','test_ai_history_context.py')) {
        & (Join-Path $root 'venv\Scripts\python.exe') -X utf8 -B (Join-Path $root "scripts\$scriptName") *> (Join-Path $run "$scriptName.log")
        if ($LASTEXITCODE -ne 0) { throw "Existing regression failed: $scriptName; log preserved." }
    }
    }
    Write-Output 'PASS isolated PostgreSQL auth and additive migration'
    Write-Output "Evidence: $run"
} finally {
    if ($started -or (Test-Path -LiteralPath (Join-Path $cluster 'postmaster.pid'))) {
        Invoke-FixtureControl @('-D', "`"$cluster`"", '-m', 'fast', '-w', 'stop') 'stop'
    }
    Remove-Item Env:\PGPASSWORD,Env:\GITVIZ_TEST_DATABASE_URL -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath $passwordFile) { Remove-Item -LiteralPath $passwordFile }
    $secret = $null
}
