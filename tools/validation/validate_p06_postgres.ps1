param([string]$PostgresBin = 'C:\Program Files\PostgreSQL\18\bin')
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$run = Join-Path $root ('.validation\p06\pg-' + [guid]::NewGuid().ToString('N'))
$port = 55436
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
    & (Join-Path $PostgresBin 'createdb.exe') -h 127.0.0.1 -p $port -U gitviz_test gitviz_test_p06
    if ($LASTEXITCODE -ne 0) { throw 'Fixture database creation failed.' }
    $encoded = [Uri]::EscapeDataString($secret)
    $env:GITVIZ_TEST_DATABASE_URL = "postgresql://gitviz_test:${encoded}@127.0.0.1:${port}/gitviz_test_p06"
    # Pin fixture policy; inherited developer configuration must not change test expectations.
    $env:AI_DAILY_JOB_LIMIT = '30'
    $env:PYTHON_DOTENV_DISABLED = '1'
    & (Join-Path $root 'venv\Scripts\python.exe') -X utf8 -B (Join-Path $root 'tests\server\test_ai_history_context.py') *> (Join-Path $run 'history-context-tests.log')
    if ($LASTEXITCODE -ne 0) { throw 'Existing S11 history regression failed; logs preserved.' }
    Write-Output 'PASS P06 isolated PostgreSQL existing S11 history regression'
    Write-Output "Evidence: $run"
} finally {
    if ($started -or (Test-Path -LiteralPath (Join-Path $cluster 'postmaster.pid'))) {
        Invoke-FixtureControl @('-D', "`"$cluster`"", '-m', 'fast', '-w', 'stop') 'stop'
    }
    Remove-Item Env:\PGPASSWORD,Env:\GITVIZ_TEST_DATABASE_URL -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath $passwordFile) { Remove-Item -LiteralPath $passwordFile }
    $secret = $null
}
