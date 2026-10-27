[CmdletBinding()]
param([switch]$Elevated)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$directory = Join-Path $root '.validation\p01-vm'
$name = 'GitViz-P01-Clean-Win11'
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) {
    if ($Elevated) { throw 'Administrator token required.' }
    $arguments = '-NoProfile -ExecutionPolicy Bypass -File "{0}" -Elevated' -f $PSCommandPath
    $process = Start-Process "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -ArgumentList $arguments -Verb RunAs -WindowStyle Hidden -Wait -PassThru
    exit $process.ExitCode
}
New-Item -ItemType Directory -Path $directory -Force | Out-Null
$record = @{ status = 'failed'; vmName = $name }
try {
    Import-Module Hyper-V
    if (Get-VM -Name $name -ErrorAction SilentlyContinue) { throw 'Named VM already exists; inspect it before continuing.' }
    $iso = Join-Path $root '.validation\iso\Windows11_Client_x64_ko-kr_26300_9457.iso'
    if ((Get-FileHash -LiteralPath $iso -Algorithm SHA256).Hash -ne 'EC4C80AA16C1910562EA8EEA82B86CCF2012375E0492B180178BB605DE308292') { throw 'ISO hash mismatch.' }
    $disk = Join-Path $directory 'system.vhdx'
    if (Test-Path -LiteralPath $disk) { throw 'Disk already exists; preserving it.' }
    $vm = New-VM -Name $name -Generation 2 -MemoryStartupBytes 4GB -NewVHDPath $disk -NewVHDSizeBytes 80GB -Path $directory
    Set-VMProcessor -VM $vm -Count 2
    Set-VMKeyProtector -VM $vm -NewLocalKeyProtector
    Enable-VMTPM -VM $vm
    $dvd = Add-VMDvdDrive -VM $vm -Path $iso -Passthru
    Set-VMFirmware -VM $vm -EnableSecureBoot On -SecureBootTemplate MicrosoftWindows -FirstBootDevice $dvd
    # No switch, shared host folders, unattended account, EULA acceptance or host reboot.
    Start-VM -VM $vm
    $record.status = 'installer-boot-started'
    $record.vmId = $vm.Id.ToString()
    $record.memoryBytes = 4GB
    $record.processors = 2
    $record.diskMaximumBytes = 80GB
    $record.isoSHA256 = 'EC4C80AA16C1910562EA8EEA82B86CCF2012375E0492B180178BB605DE308292'
    $record.networkSwitch = @(Get-VMNetworkAdapter -VM $vm | ForEach-Object { $_.SwitchName })
    $record.state = (Get-VM -Id $vm.Id).State.ToString()
} catch {
    $record.error = $_.Exception.Message
} finally {
    $record | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $directory 'preparation.json') -Encoding utf8
}
if ($record.status -ne 'installer-boot-started') { exit 1 }
exit 0
