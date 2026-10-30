$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$package = (Resolve-Path (Join-Path $root 'desktop\out\GitVizWorkspaceP05Verify-win32-x64')).Path
$evidence = Join-Path $root ('.validation\p05\package-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $evidence | Out-Null
$previousPath = $env:PATH
try {
    $env:PATH = Join-Path $env:SystemRoot 'System32'
    $process = Start-Process -FilePath (Join-Path $package 'GitVizWorkspaceP05Verify.exe') -WorkingDirectory $package -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $evidence 'stdout.log') -RedirectStandardError (Join-Path $evidence 'stderr.log')
    if (!$process.WaitForExit(60000)) { $process.Kill(); throw 'P05 package verification timed out; logs preserved.' }
    if ($process.ExitCode -ne 0) { throw 'P05 package verification failed; logs preserved.' }
    $output = Get-Content -LiteralPath (Join-Path $evidence 'stdout.log') -Raw
    if (!$output.Contains('PASS P05 frozen agent preview/cloud block')) { throw 'P05 acceptance marker missing.' }
    if (!$output.Contains('kill/restart uncertain without resend')) { throw 'P05 crash recovery acceptance marker missing.' }
    $agentHash = (Get-FileHash -LiteralPath (Join-Path $package 'resources\agent\gitviz-workspace-agent.exe')).Hash
    $originalHash = (Get-FileHash -LiteralPath (Join-Path $root 'dist\workspace-agent\agent\gitviz-workspace-agent.exe')).Hash
    if ($agentHash -ne $originalHash) { throw 'Bundled P05 agent hash mismatch.' }
    $admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    $record = [ordered]@{
        classification = 'development-host-only'
        status = 'passed'
        exitCode = $process.ExitCode
        pathRestricted = $true
        standardUser = !$admin
        agentMatchesP05 = $true
        localSchema = 4
        providerValidation = 'owned-loopback-fake-only'
        realModelCalls = 0
        crashRecovery = 'kill/restart uncertain without resend'
        fixtureTools = 'Owned loopback fake provider; installed Git fixture and installed Node path inspection; no project commands executed'
        executableSha256 = (Get-FileHash -LiteralPath (Join-Path $package 'GitVizWorkspaceP05Verify.exe')).Hash
        asarSha256 = (Get-FileHash -LiteralPath (Join-Path $package 'resources\app.asar')).Hash
        agentSha256 = $agentHash
    }
    $record | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $evidence 'acceptance.json') -Encoding utf8
    Write-Output $output
    Write-Output "Evidence: $evidence"
} finally { $env:PATH = $previousPath }
