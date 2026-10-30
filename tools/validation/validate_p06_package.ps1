$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$package = (Resolve-Path (Join-Path $root 'build-output\desktop\GitVizWorkspaceP06Verify-win32-x64')).Path
$evidence = Join-Path $root ('.validation\p06\package-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $evidence | Out-Null
$previousPath = $env:PATH
try {
    $env:PATH = Join-Path $env:SystemRoot 'System32'
    $process = Start-Process -FilePath (Join-Path $package 'GitVizWorkspaceP06Verify.exe') -WorkingDirectory $package -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $evidence 'stdout.log') -RedirectStandardError (Join-Path $evidence 'stderr.log')
    if (!$process.WaitForExit(60000)) { $process.Kill(); throw 'P06 package verification timed out; logs preserved.' }
    if ($process.ExitCode -ne 0) { throw 'P06 package verification failed; logs preserved.' }
    $output = Get-Content -LiteralPath (Join-Path $evidence 'stdout.log') -Raw
    if (!$output.Contains('PASS P06 frozen agent native approval/apply')) { throw 'P06 acceptance marker missing.' }
    $agentHash = (Get-FileHash -LiteralPath (Join-Path $package 'resources\agent\gitviz-workspace-agent.exe')).Hash
    $originalHash = (Get-FileHash -LiteralPath (Join-Path $root 'build-output\agent\dist\agent\gitviz-workspace-agent.exe')).Hash
    if ($agentHash -ne $originalHash) { throw 'Bundled P06 agent hash mismatch.' }
    $admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    $record = [ordered]@{
        classification = 'development-host-only'
        status = 'passed'
        exitCode = $process.ExitCode
        pathRestricted = $true
        standardUser = !$admin
        agentMatchesP06 = $true
        localSchema = 5
        providerValidation = 'owned-loopback-fake-only'
        realModelCalls = 0
        crashRecovery = 'source-agent kill matrix separately verified; not repeated in package'
        fixtureTools = 'Owned loopback fake provider; installed Git fixture and installed Node path inspection; no project commands executed'
        executableSha256 = (Get-FileHash -LiteralPath (Join-Path $package 'GitVizWorkspaceP06Verify.exe')).Hash
        asarSha256 = (Get-FileHash -LiteralPath (Join-Path $package 'resources\app.asar')).Hash
        agentSha256 = $agentHash
    }
    $record | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $evidence 'acceptance.json') -Encoding utf8
    Write-Output $output
    Write-Output "Evidence: $evidence"
} finally { $env:PATH = $previousPath }
