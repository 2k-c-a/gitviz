$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$package = (Resolve-Path -LiteralPath (Join-Path $root 'desktop/out/GitVizWorkspaceP08bVerify-win32-x64')).Path
$evidence = Join-Path $root ('.validation/p08b/package-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $evidence | Out-Null
$previousPath = $env:PATH
try {
    $env:PATH = Join-Path $env:SystemRoot 'System32'
    $process = Start-Process -FilePath (Join-Path $package 'GitVizWorkspaceP08bVerify.exe') -WorkingDirectory $package -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $evidence 'stdout.log') -RedirectStandardError (Join-Path $evidence 'stderr.log')
    if (!$process.WaitForExit(120000)) { $process.Kill(); throw 'P08b verification timed out; evidence retained.' }
    if ($process.ExitCode -ne 0) { throw 'P08b verification failed; evidence retained.' }
    $output = Get-Content -LiteralPath (Join-Path $evidence 'stdout.log') -Raw
    if (!$output.Contains('PASS P08b frozen agent/native cancel/account/identity/transition/permission/navigation/config')) { throw 'P08b marker missing.' }
    $source = (Get-FileHash -LiteralPath (Join-Path $root 'dist/workspace-agent/agent/gitviz-workspace-agent.exe')).Hash
    $bundled = (Get-FileHash -LiteralPath (Join-Path $package 'resources/agent/gitviz-workspace-agent.exe')).Hash
    if ($source -ne $bundled) { throw 'P08b agent hash mismatch.' }
    $admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    @{ status='passed'; classification='development-host-only'; localSchema=8; standardUser=(!$admin); pathRestricted=$true;
       agentSha256=$bundled; asarSha256=(Get-FileHash -LiteralPath (Join-Path $package 'resources/app.asar')).Hash;
       externalServiceCalls=$false; cleanVM=$false } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $evidence 'result.json') -Encoding utf8
} finally { $env:PATH = $previousPath; Write-Output "Evidence: $evidence" }
