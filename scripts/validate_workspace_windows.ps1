#Requires -Version 7.0
param([string]$Python = '', [string]$Node = '', [string]$EvidenceDirectory = '')
$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') { throw 'Windows fixture runner required.' }
$repo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
if (!$Python) { $Python = Join-Path $repo '.validation/venv-workspace/Scripts/python.exe' }
if (!$Node) { $Node = (Get-Command node.exe).Source }
$Python = (Resolve-Path -LiteralPath $Python).Path
$Node = (Resolve-Path -LiteralPath $Node).Path
$npm = Join-Path (Split-Path $Node) 'node_modules/npm/bin/npm-cli.js'
$electron = Join-Path $repo 'desktop/node_modules/electron/dist/electron.exe'
foreach ($path in @($npm, $electron)) { if (!(Test-Path -LiteralPath $path -PathType Leaf)) { throw 'Existing dependencies required; no installation is performed.' } }
$temporary = Join-Path ([IO.Path]::GetTempPath()) ('gitviz-windows-' + [guid]::NewGuid().ToString('N'))
if (([IO.Path]::GetFullPath($temporary) + '\').StartsWith($repo.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Fixture TEMP must be outside repository.' }
New-Item -ItemType Directory -Path $temporary | Out-Null
if (!$EvidenceDirectory) { $EvidenceDirectory = Join-Path $temporary 'evidence' }
$evidence = [IO.Path]::GetFullPath($EvidenceDirectory)
if (Test-Path -LiteralPath $evidence) { throw 'Evidence directory must be new.' }
New-Item -ItemType Directory -Path $evidence | Out-Null
$summary = [Collections.Generic.List[object]]::new()
$completed = $false
function Save-State([string]$Label) {
    & git -C $repo rev-parse HEAD | Set-Content -LiteralPath (Join-Path $evidence "$Label-head.txt") -Encoding utf8
    if ($LASTEXITCODE -ne 0) { throw 'Git state read failed.' }
    $status = @(& git -C $repo status --porcelain=v1 -uall)
    if ($LASTEXITCODE -ne 0) { throw 'Git state read failed.' }
    # Set-Content writes no file for an empty pipeline, which a clean CI checkout produces.
    [IO.File]::WriteAllText((Join-Path $evidence "$Label-status.txt"), (($status | ForEach-Object { "$_`n" }) -join ''), [Text.UTF8Encoding]::new($false))
}
function Run-Step([string]$Name, [string]$Executable, [string[]]$Arguments, [int]$Timeout = 600, [string]$WorkingDirectory = $repo) {
    $info = [Diagnostics.ProcessStartInfo]::new()
    $info.FileName = $Executable; $info.WorkingDirectory = $WorkingDirectory
    $info.UseShellExecute = $false; $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true; $info.RedirectStandardError = $true
    $info.StandardOutputEncoding = [Text.Encoding]::UTF8; $info.StandardErrorEncoding = [Text.Encoding]::UTF8
    foreach ($argument in $Arguments) { $info.ArgumentList.Add($argument) }
    foreach ($key in @('PYTHONPATH', 'ELECTRON_RUN_AS_NODE', 'DATABASE_URL', 'DATABASE_URL_ASYNC', 'GITVIZ_SERVER_ORIGIN', 'GITVIZ_TEST_DATABASE_URL', 'GITVIZ_TEST_REDIS_URL')) { $info.Environment.Remove($key) | Out-Null }
    foreach ($item in @{ TEMP=$temporary; TMP=$temporary; PYTHON_DOTENV_DISABLED='1'; PYTHONDONTWRITEBYTECODE='1'; PYTHONUTF8='1'; PYTHONIOENCODING='utf-8'; HTTP_PROXY='http://127.0.0.1:9'; HTTPS_PROXY='http://127.0.0.1:9'; NO_PROXY='localhost,127.0.0.1,::1'; GIT_OPTIONAL_LOCKS='0' }.GetEnumerator()) { $info.Environment[$item.Key] = $item.Value }
    $started = Get-Date
    $process = [Diagnostics.Process]::Start($info)
    $stdout = $process.StandardOutput.ReadToEndAsync(); $stderr = $process.StandardError.ReadToEndAsync()
    $timedOut = !$process.WaitForExit($Timeout * 1000)
    if ($timedOut) { $process.Kill($true) }
    $process.WaitForExit()
    $exitCode = if ($timedOut) { 124 } else { $process.ExitCode }
    "CMD: $Executable $($Arguments -join ' ')`nSTDOUT:`n$($stdout.GetAwaiter().GetResult())`nSTDERR:`n$($stderr.GetAwaiter().GetResult())" | Set-Content -LiteralPath (Join-Path $evidence "$Name.log") -Encoding utf8
    $process.Dispose()
    $summary.Add([ordered]@{ step=$Name; exit=$exitCode; seconds=[Math]::Round(((Get-Date)-$started).TotalSeconds, 3) })
    ConvertTo-Json -InputObject $summary -Depth 5 | Set-Content -LiteralPath (Join-Path $evidence 'summary.json') -Encoding utf8
    if ($exitCode -ne 0) { throw "Fixture step failed: $Name (exit $exitCode); evidence retained." }
}
Save-State 'before'
try {
    foreach ($script in @('lint', 'typecheck', 'typecheck:agent')) { Run-Step $script $Node @($npm, '--prefix', 'desktop', 'run', $script) }
    Run-Step 'ruff' $Python @('-X', 'utf8', '-B', '-m', 'ruff', 'check', '--no-cache', '--config', 'ruff-workspace.toml', 'app/workspace', 'tests/workspace', 'tools/build/build_workspace_agent.py')
    Run-Step 'desktop-build' $Node @($npm, '--prefix', 'desktop', 'run', 'build')
    Run-Step 'agent-freeze' $Python @('-X', 'utf8', '-B', 'tools/build/build_workspace_agent.py') 1200
    Run-Step 'host-unit' $Node @($npm, '--prefix', 'desktop', 'test')
    Run-Step 'workspace-unit' $Python @('-X', 'utf8', '-B', '-m', 'unittest', 'discover', '-s', 'tests/workspace', '-p', 'test_*.py', '-v') 2400
    Run-Step 'web-navigation' $Node @('scripts/test_workspace_navigation.cjs')
    Run-Step 'web-regression' $Node @('scripts/test_viz_modules.cjs')
    foreach ($name in @('remote-workspace', 'workspace', 'inbox', 'projects', 'changes', 'review-preview', 'review', 'refactor', 'validation', 'auth', 'remote-compat', 'foundation')) {
        Run-Step "electron-$name" $electron @("dist/tests/$name.e2e.js") 420 (Join-Path $repo 'desktop')
    }
    Run-Step 'documents' $Python @('-X', 'utf8', '-B', 'docs/audit/check_documents.py')
    $completed = $true
} finally {
    Save-State 'after'
    $changed = [IO.File]::ReadAllText((Join-Path $evidence 'before-status.txt')) -cne [IO.File]::ReadAllText((Join-Path $evidence 'after-status.txt'))
    @{ finished=(Get-Date).ToString('o'); status=$(if ($completed -and !$changed) { 'passed' } else { 'failed' }); steps=$summary.Count; failed=@($summary | Where-Object exit -ne 0).Count; statusChanged=$changed } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $evidence 'done.json') -Encoding utf8
    Write-Output "Evidence: $evidence"
}
if ($changed) { throw 'Repository status changed during validation; inspect evidence.' }
