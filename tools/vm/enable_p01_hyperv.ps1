# Uses only installed Windows servicing manifests; DISM verifies their catalogs.
# This unsupported Home workaround changes Windows features, never reboots.
[CmdletBinding()]
param([switch]$Elevated)
$ErrorActionPreference = 'Stop'
$repo = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$logRoot = Join-Path $repo '.validation\hyperv'
New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$admin = ([Security.Principal.WindowsPrincipal]$identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) {
    if ($Elevated) { throw 'Elevation did not produce an administrator token.' }
    $arguments = '-NoProfile -ExecutionPolicy Bypass -File "{0}" -Elevated' -f $PSCommandPath
    $process = Start-Process -FilePath "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -ArgumentList $arguments -Verb RunAs -WindowStyle Hidden -PassThru -Wait
    exit $process.ExitCode
}
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$transcript = Join-Path $logRoot "$stamp-transcript.txt"
Start-Transcript -Path $transcript | Out-Null
$result = @{ status = 'failed'; rebootRequired = $false; operations = @() }
try {
    if (-not [Environment]::Is64BitProcess) { throw '64-bit PowerShell is required.' }
    $os = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion'
    if ([int]$os.CurrentBuild -lt 22000) { throw 'Windows 11 is required.' }
    $result.edition = $os.EditionID
    $result.build = "$($os.CurrentBuild).$($os.UBR)"
    $dism = Join-Path $env:SystemRoot 'System32\dism.exe'
    function Invoke-Servicing([string[]]$DismArguments, [string]$Label) {
        $log = Join-Path $logRoot "$stamp-$Label.txt"
        & $dism @DismArguments 2>&1 | Out-File -LiteralPath $log -Encoding utf8
        $code = $LASTEXITCODE
        $result.operations += @{ label = $Label; exitCode = $code }
        if ($code -eq 3010) { $result.rebootRequired = $true }
        elseif ($code -ne 0) { throw "DISM $Label failed: $code. See $log" }
    }
    Invoke-Servicing @('/Online', '/Get-Features', '/Format:Table', '/English') 'features-before'
    Invoke-Servicing @('/Online', '/Get-Packages', '/Format:Table', '/English') 'packages-before'
    $pending = Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending'
    if ($pending) { throw 'Windows servicing already requires a restart. Restart manually before retrying.' }
    $packageRoot = Join-Path $env:SystemRoot 'servicing\Packages'
    $manifests = foreach ($file in Get-ChildItem -LiteralPath $packageRoot -Filter '*Hyper-V*.mum' -File) {
        [xml]$xml = Get-Content -LiteralPath $file.FullName -Raw -Encoding UTF8
        $id = $xml.assembly.assemblyIdentity
        if ($id.processorArchitecture -ne 'amd64' -or $id.publicKeyToken -ne '31bf3856ad364e35' -or $id.name -notlike 'Microsoft-Hyper-V-*') { continue }
        $catalog = [IO.Path]::ChangeExtension($file.FullName, '.cat')
        if (-not (Test-Path -LiteralPath $catalog)) { throw "Missing catalog: $catalog" }
        [pscustomobject]@{ Group = "$($id.name)|$($id.language)"; Version = [version]$id.version; Path = $file.FullName }
    }
    $selected = @($manifests | Group-Object Group | ForEach-Object { $_.Group | Sort-Object Version -Descending | Select-Object -First 1 } | Sort-Object Group)
    if ($selected.Count -eq 0) { throw 'No local Microsoft Hyper-V manifests found.' }
    $selected | Select-Object Group, Version, Path | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $logRoot "$stamp-manifests.json") -Encoding utf8
    $index = 0
    foreach ($manifest in $selected) {
        $index++
        Invoke-Servicing @('/Online', '/Add-Package', "/PackagePath:$($manifest.Path)", '/NoRestart', '/English') "package-$index"
    }
    Invoke-Servicing @('/Online', '/Enable-Feature', '/FeatureName:Microsoft-Hyper-V-All', '/All', '/NoRestart', '/English') 'enable'
    Invoke-Servicing @('/Online', '/Get-FeatureInfo', '/FeatureName:Microsoft-Hyper-V-All', '/English') 'feature-after'
    $result.status = 'servicing-completed'
    $result.rebootRequired = $true
} catch {
    $result.error = $_.Exception.Message
    Write-Output $result.error
} finally {
    $result | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $logRoot "$stamp-result.json") -Encoding utf8
    Stop-Transcript | Out-Null
}
if ($result.status -ne 'servicing-completed') { exit 1 }
exit 0
