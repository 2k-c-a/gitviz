[CmdletBinding()]
param([Parameter(Mandatory)][string]$SetupExe, [string]$Label = 'run', [string]$Sessions = 'Unattended,Interactive', [string]$Variants = 'Silent,Normal', [switch]$Elevated)
# Uninstall acceptance on the clean P01 VM. For every Session x Variant: restore checkpoint, install SetupExe as the standard user,
# launch once, write a marker into the user data, uninstall, and record what is left. Needs a UAC-elevated host token.
#   Session  Unattended = PowerShell Direct only (no desktop for the user); Interactive = temporary autologon, the user has a desktop.
#   Variant  Silent = registered UninstallString + -s;  Normal = registered UninstallString as the Apps list would run it.
# The checkpoint is restored at the end of every run (that also removes the temporary autologon).
$ErrorActionPreference = 'Stop'
$repo = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$work = Join-Path $repo '.validation\p11-vm'
$p01 = Join-Path $repo '.validation\p01-vm'
$vmId = [guid]'25a434e8-1335-47ad-a6c1-cdec28192507'
$checkpointName = 'pre-p10-installer'
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) {
    if ($Elevated) { throw 'Administrator token required.' }
    $arguments = '-NoProfile -ExecutionPolicy Bypass -File "{0}" -SetupExe "{1}" -Label "{2}" -Sessions "{3}" -Variants "{4}" -Elevated' -f $PSCommandPath, $SetupExe, $Label, $Sessions, $Variants
    $process = Start-Process "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -ArgumentList $arguments -Verb RunAs -WindowStyle Hidden -Wait -PassThru
    exit $process.ExitCode
}
New-Item -ItemType Directory -Path $work -Force | Out-Null
Set-Content -LiteralPath (Join-Path $work "Uninstall-$Label-started.txt") -Value (Get-Date).ToString('o')
$result = [ordered]@{ label = $Label; status = 'failed'; startedAt = (Get-Date).ToString('o'); runs = @() }

function Wait-Guest($credential, [int]$Seconds) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        try { $name = Invoke-Command -VMId $vmId -Credential $credential -ScriptBlock { $env:COMPUTERNAME } -ErrorAction Stop; if ($name -eq 'GITVIZ-P01') { return $true } } catch { Start-Sleep -Seconds 5 }
    }
    return $false
}

# Runs in the guest as the standard user.
$guestTest = {
    param($Setup, $Variant)
    $ErrorActionPreference = 'Continue'
    $steps = New-Object System.Collections.ArrayList
    function Step($name, $ok, $detail) { [void]$steps.Add([ordered]@{ step = $name; ok = [bool]$ok; detail = "$detail" }) }
    function Stop-App { Get-Process -Name GitVizWorkspace, gitviz-workspace-agent -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue; Start-Sleep -Seconds 3 }
    $install = Join-Path $env:LOCALAPPDATA 'GitVizWorkspace'
    $data = Join-Path $env:APPDATA 'gitviz-workspace\workspace'
    $cleanupLog = Join-Path $env:TEMP 'GitVizWorkspace-uninstall-cleanup.log'
    function Hashes($dir) { $h = [ordered]@{}; if (Test-Path -LiteralPath $dir) { foreach ($f in Get-ChildItem -LiteralPath $dir -File -ErrorAction SilentlyContinue | Sort-Object Name) { try { $h[$f.Name] = (Get-FileHash -LiteralPath $f.FullName -Algorithm SHA256 -ErrorAction Stop).Hash.Substring(0, 16) } catch { $h[$f.Name] = 'locked' } } }; $h }
    $explorerOwners = @(Get-CimInstance Win32_Process -Filter "Name='explorer.exe'" -ErrorAction SilentlyContinue | ForEach-Object { (Invoke-CimMethod -InputObject $_ -MethodName GetOwner -ErrorAction SilentlyContinue).User })
    $interactiveUser = $explorerOwners -contains $env:USERNAME
    Step 'session' $true "user=$env:USERNAME explorerOfThisUser=$interactiveUser sessionId=$((Get-Process -Id $PID).SessionId)"

    $p = Start-Process $Setup -ArgumentList '--silent' -PassThru
    $exited = $p.WaitForExit(240000); Start-Sleep -Seconds 12; Stop-App
    Step 'install' ($exited -and $p.ExitCode -eq 0 -and (Test-Path (Join-Path $install 'Update.exe'))) "exited=$exited code=$($p.ExitCode)"
    $exe = Get-ChildItem -LiteralPath $install -Directory -Filter 'app-*' | Select-Object -First 1 | ForEach-Object { Join-Path $_.FullName 'GitVizWorkspace.exe' }
    $app = Start-Process $exe -PassThru; Start-Sleep -Seconds 25; $alive = -not $app.HasExited; Stop-App
    Step 'launch' $alive "alive=$alive"
    Set-Content -LiteralPath (Join-Path $data 'p11-marker.txt') -Value 'user data must survive uninstall'
    $dataBefore = Hashes $data
    $otherBefore = @(Get-ChildItem -LiteralPath $env:LOCALAPPDATA -Directory -ErrorAction SilentlyContinue | Where-Object { $_.Name -ne 'GitVizWorkspace' } | ForEach-Object { $_.Name })
    $installFilesBefore = @(Get-ChildItem -LiteralPath $install -Recurse -File -Force -ErrorAction SilentlyContinue).Count
    if (Test-Path $cleanupLog) { Remove-Item -LiteralPath $cleanupLog -Force }

    $registered = (Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\GitVizWorkspace' -ErrorAction SilentlyContinue).UninstallString
    Step 'uninstall-entry' ([bool]$registered) "UninstallString=$registered"
    $m = [regex]::Match([string]$registered, '^"([^"]+)"\s*(.*)$')
    $updater = $m.Groups[1].Value; $uargs = $m.Groups[2].Value
    if ($Variant -eq 'Silent') { $uargs = ($uargs + ' -s').Trim() }
    $sw = [Diagnostics.Stopwatch]::StartNew()
    $un = Start-Process $updater -ArgumentList $uargs -PassThru
    $unExited = $un.WaitForExit(240000)
    Step 'uninstall-exit' ($unExited -and $un.ExitCode -eq 0) "args=[$uargs] exited=$unExited code=$($un.ExitCode) seconds=$([int]$sw.Elapsed.TotalSeconds)"
    $deadline = (Get-Date).AddSeconds(120); $seconds = 0
    do { Start-Sleep -Seconds 3; $left = @(Get-ChildItem -LiteralPath $install -Recurse -File -Force -ErrorAction SilentlyContinue).Count } while ($left -gt 0 -and (Get-Date) -lt $deadline)
    $remaining = @(Get-ChildItem -LiteralPath $install -Recurse -Force -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName.Substring($install.Length) })
    Step 'install-dir-files-removed' (@(Get-ChildItem -LiteralPath $install -Recurse -File -Force -ErrorAction SilentlyContinue).Count -eq 0) ("filesBefore=$installFilesBefore remaining=[" + ($remaining -join '; ') + "]")
    Step 'install-dir-itself-removed' (-not (Test-Path -LiteralPath $install)) "exists=$(Test-Path -LiteralPath $install)"
    $lnk = @(Get-ChildItem -LiteralPath @($env:APPDATA, (Join-Path $env:USERPROFILE 'Desktop')) -Recurse -Filter 'GitVizWorkspace*.lnk' -ErrorAction SilentlyContinue)
    Step 'shortcuts-removed' ($lnk.Count -eq 0) "count=$($lnk.Count)"
    Step 'uninstall-entry-removed' (-not (Test-Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\GitVizWorkspace')) ''
    $dataAfter = Hashes $data
    Step 'user-data-kept' ((Test-Path (Join-Path $data 'p11-marker.txt')) -and ($dataBefore['workspace.sqlite3'] -eq $dataAfter['workspace.sqlite3']) -and ($dataBefore['key.dpapi'] -eq $dataAfter['key.dpapi'])) "files=[$($dataAfter.Keys -join ',')]"
    $otherAfter = @(Get-ChildItem -LiteralPath $env:LOCALAPPDATA -Directory -ErrorAction SilentlyContinue | ForEach-Object { $_.Name })
    $lostOther = @($otherBefore | Where-Object { $_ -notin $otherAfter })
    Step 'other-app-folders-untouched' ($lostOther.Count -eq 0) "lost=[$($lostOther -join ',')]"
    $procs = @(Get-Process -Name GitVizWorkspace, gitviz-workspace-agent, Update, squirrel -ErrorAction SilentlyContinue | ForEach-Object { $_.ProcessName })
    Step 'no-leftover-processes' ($procs.Count -eq 0) "processes=[$($procs -join ',')]"
    $task = (& schtasks.exe /Query /TN GitVizWorkspaceUninstallCleanup 2>&1 | Out-String).Trim(); $taskExit = $LASTEXITCODE
    Step 'cleanup-task-removed' ($taskExit -ne 0) "queryExit=$taskExit"
    $log = if (Test-Path $cleanupLog) { @(Get-Content -LiteralPath $cleanupLog | ForEach-Object { [string]$_ }) } else { @('(no cleanup log file)') }
    $script = Test-Path (Join-Path $env:TEMP 'GitVizWorkspace-uninstall-cleanup.ps1')
    Step 'cleanup-script-removed' (-not $script) "exists=$script"
    [ordered]@{ user = "$env:COMPUTERNAME\$env:USERNAME"; variant = $Variant; interactive = $interactiveUser; cleanupLog = $log; remaining = $remaining
        passed = (@($steps | Where-Object { -not $_.ok }).Count -eq 0); steps = $steps } | ConvertTo-Json -Depth 6 -Compress
}

try {
    Import-Module Hyper-V
    $vm = Get-VM -Id $vmId
    if ($vm.Name -ne 'GitViz-P01-Clean-Win11') { throw 'Unexpected VM.' }
    if ($vm.State -ne 'Off') { throw 'VM must be Off before the test.' }
    if (-not (Test-Path -LiteralPath $SetupExe)) { throw 'SetupExe not found.' }
    $result.setup = [ordered]@{ path = $SetupExe; bytes = (Get-Item -LiteralPath $SetupExe).Length; sha256 = (Get-FileHash -LiteralPath $SetupExe).Hash }
    $admincred = Import-Clixml (Join-Path $p01 'credentials\admin.xml')
    $testercred = Import-Clixml (Join-Path $p01 'credentials\tester.xml')
    foreach ($session in ($Sessions -split ',')) {
        foreach ($variant in ($Variants -split ',')) {
            $run = [ordered]@{ session = $session; variant = $variant }
            try {
                $snap = Get-VMSnapshot -VM (Get-VM -Id $vmId) -Name $checkpointName -ErrorAction Stop
                Restore-VMSnapshot -VMSnapshot $snap -Confirm:$false
                Start-VM -VM (Get-VM -Id $vmId)
                if (-not (Wait-Guest $admincred 300)) { throw 'Guest did not become reachable.' }
                $s = New-PSSession -VMId $vmId -Credential $admincred
                try {
                    Invoke-Command -Session $s -ScriptBlock { New-Item -ItemType Directory -Path 'C:\P11\Installer' -Force | Out-Null; & icacls.exe 'C:\P11' /grant '*S-1-5-32-545:(OI)(CI)RX' | Out-Null }
                    Copy-Item -LiteralPath $SetupExe -Destination 'C:\P11\Installer\GitVizWorkspaceSetup.exe' -ToSession $s -Force
                } finally { Remove-PSSession $s }
                if ($session -eq 'Interactive') {
                    Invoke-Command -VMId $vmId -Credential $admincred -ArgumentList $testercred.GetNetworkCredential().Password -ScriptBlock {
                        param($Secret)
                        $key = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon'
                        Set-ItemProperty $key AutoAdminLogon '1'; Set-ItemProperty $key DefaultUserName 'P01Tester'; Set-ItemProperty $key DefaultDomainName $env:COMPUTERNAME; Set-ItemProperty $key DefaultPassword $Secret
                        Restart-Computer -Force
                    }
                    Start-Sleep -Seconds 30
                    if (-not (Wait-Guest $admincred 300)) { throw 'Guest did not return after the autologon restart.' }
                    $deadline = (Get-Date).AddSeconds(180); $users = @()
                    do {
                        Start-Sleep -Seconds 5
                        $users = @(Invoke-Command -VMId $vmId -Credential $admincred -ScriptBlock { Get-Process explorer -IncludeUserName -ErrorAction SilentlyContinue | ForEach-Object { [string]$_.UserName } })
                    } while (($users -notcontains 'GITVIZ-P01\P01Tester') -and (Get-Date) -lt $deadline)
                    if ($users -notcontains 'GITVIZ-P01\P01Tester') { throw 'P01Tester has no interactive desktop.' }
                }
                $json = Invoke-Command -VMId $vmId -Credential $testercred -ScriptBlock $guestTest -ArgumentList 'C:\P11\Installer\GitVizWorkspaceSetup.exe', $variant
                $run.guest = $json | ConvertFrom-Json
            } catch { $run.error = $_.Exception.Message }
            finally {
                $current = Get-VM -Id $vmId
                if ($current.State -ne 'Off') { Stop-VM -VM $current -TurnOff -Force }
                $snap = Get-VMSnapshot -VM (Get-VM -Id $vmId) -Name $checkpointName -ErrorAction SilentlyContinue
                if ($snap) { Restore-VMSnapshot -VMSnapshot $snap -Confirm:$false }
            }
            $result.runs += $run
        }
    }
    $result.status = if (@($result.runs | Where-Object { $_.guest -and $_.guest.passed }).Count -eq @($result.runs).Count) { 'passed' } else { 'failed-steps' }
} catch {
    $result.error = $_.Exception.Message
} finally {
    $result.finishedAt = (Get-Date).ToString('o')
    $result | ConvertTo-Json -Depth 9 | Set-Content -LiteralPath (Join-Path $work "Uninstall-$Label-result.json") -Encoding utf8
}
if ($result.status -ne 'passed') { exit 1 }
exit 0
