param(
    [Parameter(Mandatory=$true)][string]$PackageDirectory,
    [switch]$AttestCleanVM,
    [switch]$AllowDevelopmentHost
)
$ErrorActionPreference = 'Stop'
$package = (Resolve-Path -LiteralPath $PackageDirectory).Path
$executable = Join-Path $package 'GitVizWorkspaceVerify.exe'
if (!(Test-Path -LiteralPath $executable -PathType Leaf)) { throw 'Verification package is required.' }
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
$nodeFound = [bool](Get-Command node.exe -ErrorAction SilentlyContinue)
$pythonCommands = @(Get-Command python.exe,python3.exe -ErrorAction SilentlyContinue | Where-Object { $_.Source -notlike '*\Microsoft\WindowsApps\*' })
$pythonFound = $pythonCommands.Count -gt 0
$pythonRegistered = (Test-Path 'HKCU:\Software\Python\PythonCore') -or (Test-Path 'HKLM:\Software\Python\PythonCore')
$windows11x64 = [Environment]::OSVersion.Version.Build -ge 22000 -and $env:PROCESSOR_ARCHITECTURE -eq 'AMD64'
$clean = $AttestCleanVM -and !$admin -and !$nodeFound -and !$pythonFound -and !$pythonRegistered -and $windows11x64
if (!$clean -and !$AllowDevelopmentHost) { throw 'Clean VM attestation, standard user, x64 Windows and no Python/Node runtime are required. Development-host runs must be labelled explicitly.' }
$root = Join-Path ([IO.Path]::GetTempPath()) ('GitVizP01Acceptance-' + [guid]::NewGuid().ToString())
New-Item -ItemType Directory -Path $root | Out-Null
$resultFile = Join-Path $root 'foundation.json'
$info = New-Object System.Diagnostics.ProcessStartInfo
$info.FileName = $executable
$info.WorkingDirectory = $package
$info.UseShellExecute = $false
$info.CreateNoWindow = $true
$info.WindowStyle = [Diagnostics.ProcessWindowStyle]::Hidden
$info.RedirectStandardOutput = $true
$info.RedirectStandardError = $true
$info.EnvironmentVariables.Remove('ELECTRON_RUN_AS_NODE')
$info.EnvironmentVariables['GITVIZ_VERIFICATION_REPORT'] = $resultFile
# The verifier must use bundled runtimes even on an explicitly labelled developer host.
$info.EnvironmentVariables['PATH'] = Join-Path $env:SystemRoot 'System32'
$process = New-Object System.Diagnostics.Process
$process.StartInfo = $info
if (!$process.Start()) { throw 'Verification process did not start.' }
$stdout = $process.StandardOutput.ReadToEndAsync()
$stderr = $process.StandardError.ReadToEndAsync()
try {
    if (!$process.WaitForExit(50000)) { $process.Kill(); $process.WaitForExit(); throw 'Verification deadline exceeded.' }
    if ($process.ExitCode -ne 0) { throw ('Verification failed with exit ' + $process.ExitCode) }
    $foundation = Get-Content -LiteralPath $resultFile -Raw | ConvertFrom-Json
    if ($foundation.status -ne 'passed' -or !$foundation.packaged -or $foundation.checks.Count -ne 8) { throw 'Packaged foundation assertions did not pass.' }
    $record = [ordered]@{
        classification = $(if ($clean) { 'clean-vm' } else { 'development-host-only' })
        cleanVMAttested = [bool]$AttestCleanVM
        standardUser = !$admin
        nodeDetected = $nodeFound
        pythonDetected = ($pythonFound -or $pythonRegistered)
        operatingSystem = [Environment]::OSVersion.VersionString
        executableSha256 = (Get-FileHash -LiteralPath $executable -Algorithm SHA256).Hash
        asarSha256 = (Get-FileHash -LiteralPath (Join-Path $package 'resources\app.asar') -Algorithm SHA256).Hash
        agentSha256 = (Get-FileHash -LiteralPath (Join-Path $package 'resources\agent\gitviz-workspace-agent.exe') -Algorithm SHA256).Hash
        foundation = $foundation
    }
    $record | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $root 'acceptance.json') -Encoding UTF8
    Write-Output ('PASS ' + $record.classification)
    Write-Output ('Evidence: ' + (Join-Path $root 'acceptance.json'))
} finally {
    if (!$process.HasExited) { $process.Kill(); $process.WaitForExit() }
    $process.Dispose()
    # Preserve evidence. Do not log raw Electron stderr or remove caller directories.
    $outText = $stdout.GetAwaiter().GetResult()
    $errorText = $stderr.GetAwaiter().GetResult()
    $messages = @($errorText -split '\r?\n' | Where-Object { $_ -match '(?i)error|fatal|gpu|angle|vulkan' } | Select-Object -First 20 | ForEach-Object { ($_ -replace '[A-Za-z]:\\[^"\r\n]*','[fixture-path]') })
    @{stdoutLength=$outText.Length;stderrLength=$errorText.Length;messages=$messages} | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath (Join-Path $root 'diagnostic-summary.json') -Encoding utf8
}
