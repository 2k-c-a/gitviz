$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$package = (Resolve-Path (Join-Path $root 'build-output\desktop\GitVizWorkspaceP03Verify-win32-x64')).Path
$evidence = Join-Path $root ('.validation\p03\package-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $evidence | Out-Null
$previousPath = $env:PATH
try {
    $env:PATH = Join-Path $env:SystemRoot 'System32'
    $process = Start-Process -FilePath (Join-Path $package 'GitVizWorkspaceP03Verify.exe') -WorkingDirectory $package -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $evidence 'stdout.log') -RedirectStandardError (Join-Path $evidence 'stderr.log')
    if (!$process.WaitForExit(60000)) { $process.Kill(); throw 'P03 package verification timed out; logs preserved.' }
    if ($process.ExitCode -ne 0) { throw 'P03 package verification failed; logs preserved.' }
    $agentHash = (Get-FileHash -LiteralPath (Join-Path $package 'resources\agent\gitviz-workspace-agent.exe')).Hash
    $originalHash = (Get-FileHash -LiteralPath (Join-Path $root 'build-output\agent\dist\agent\gitviz-workspace-agent.exe')).Hash
    if ($agentHash -ne $originalHash) { throw 'Bundled P02 agent hash mismatch.' }
    $record = [ordered]@{
        classification = 'development-host-only'
        status = 'passed'
        exitCode = $process.ExitCode
        pathRestricted = $true
        agentMatchesP02 = $true
        executableSha256 = (Get-FileHash -LiteralPath (Join-Path $package 'GitVizWorkspaceP03Verify.exe')).Hash
        asarSha256 = (Get-FileHash -LiteralPath (Join-Path $package 'resources\app.asar')).Hash
        agentSha256 = $agentHash
    }
    $record | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $evidence 'acceptance.json') -Encoding utf8
    Get-Content -LiteralPath (Join-Path $evidence 'stdout.log')
    Write-Output "Evidence: $evidence"
} finally { $env:PATH = $previousPath }
