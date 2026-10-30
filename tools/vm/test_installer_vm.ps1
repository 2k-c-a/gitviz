[CmdletBinding()]
param([ValidateSet('Inspect', 'Run', 'Restore')][string]$Mode = 'Inspect', [string]$StageDirectory = '', [switch]$Elevated)
# Clean-VM installer acceptance helper for P10. Reuses the P01 VM and its DPAPI credentials; needs a UAC-elevated host token.
# Run: checkpoint -> install 0.7.0 -> launch -> upgrade to 0.7.1 -> uninstall as the standard user -> collect evidence -> restore checkpoint.
$ErrorActionPreference = 'Stop'
$repo = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$work = Join-Path $repo '.validation\p10-vm'
$p01 = Join-Path $repo '.validation\p01-vm'
$vmId = [guid]'25a434e8-1335-47ad-a6c1-cdec28192507'
$checkpointName = 'pre-p10-installer'
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) {
    if ($Elevated) { throw 'Administrator token required.' }
    $arguments = '-NoProfile -ExecutionPolicy Bypass -File "{0}" -Mode {1} -Elevated' -f $PSCommandPath, $Mode
    if ($StageDirectory) { $arguments += ' -StageDirectory "{0}"' -f $StageDirectory }
    $process = Start-Process "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -ArgumentList $arguments -Verb RunAs -WindowStyle Hidden -Wait -PassThru
    exit $process.ExitCode
}
New-Item -ItemType Directory -Path $work -Force | Out-Null
$result = [ordered]@{ mode = $Mode; status = 'failed'; startedAt = (Get-Date).ToString('o') }

function Wait-Guest($credential, [int]$Seconds) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        try { $name = Invoke-Command -VMId $vmId -Credential $credential -ScriptBlock { $env:COMPUTERNAME } -ErrorAction Stop; if ($name -eq 'GITVIZ-P01') { return $true } } catch { Start-Sleep -Seconds 5 }
    }
    return $false
}

$guestTest = {
    param($Root)
    $ErrorActionPreference = 'Continue'
    $steps = New-Object System.Collections.ArrayList
    function Step($name, $ok, $detail) { [void]$steps.Add([ordered]@{ step = $name; ok = [bool]$ok; detail = "$detail" }) }
    function Stop-App { Get-Process -Name GitVizWorkspace -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue; Start-Sleep -Seconds 2 }
    $install = Join-Path $env:LOCALAPPDATA 'GitVizWorkspace'
    $userData = $null
    $marker = $null
    function Shortcuts { @(Get-ChildItem -LiteralPath @($env:APPDATA, (Join-Path $env:USERPROFILE 'Desktop')) -Recurse -Filter 'GitVizWorkspace*.lnk' -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName }) }
    function Uninstall-Key { Test-Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\GitVizWorkspace' }
    $isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    $sac = (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\CI\Policy' -Name VerifiedAndReputablePolicyState -ErrorAction SilentlyContinue).VerifiedAndReputablePolicyState
    $nodeFound = @(Get-Command node.exe -ErrorAction SilentlyContinue | Where-Object { $_.Source -notlike '*\Microsoft\WindowsApps\*' }).Count
    $pythonFound = @(Get-Command python.exe, python3.exe -ErrorAction SilentlyContinue | Where-Object { $_.Source -notlike '*\Microsoft\WindowsApps\*' }).Count
    $appsAlias = @(Get-Command python.exe, python3.exe, node.exe -ErrorAction SilentlyContinue | Where-Object { $_.Source -like '*\Microsoft\WindowsApps\*' } | ForEach-Object { $_.Source })
    Step 'precheck' (-not $isAdmin -and $nodeFound -eq 0 -and $pythonFound -eq 0 -and -not (Test-Path $install)) ("user=$env:USERNAME admin=$isAdmin nodeFound=$nodeFound pythonFound=$pythonFound windowsAppsAliases=[$($appsAlias -join ';')] preInstalled=$(Test-Path $install) smartAppControlState=$sac")
    Step 'installer-signature' $true ((Get-AuthenticodeSignature (Join-Path $Root 'v070\GitVizWorkspaceSetup.exe')).Status.ToString())

    # 1) install 0.7.0 silently
    $p = Start-Process (Join-Path $Root 'v070\GitVizWorkspaceSetup.exe') -ArgumentList '--silent' -PassThru
    $exited = $p.WaitForExit(180000)
    Start-Sleep -Seconds 10
    $firstRun = @(Get-Process -Name GitVizWorkspace -ErrorAction SilentlyContinue).Count
    Stop-App
    Step 'setup-exit' ($exited -and $p.ExitCode -eq 0) ("exited=$exited exitCode=$($p.ExitCode) appProcessesAfterSetup=$firstRun")
    $exe070 = Join-Path $install 'app-0.7.0\GitVizWorkspace.exe'
    Step 'files-installed' ((Test-Path (Join-Path $install 'Update.exe')) -and (Test-Path $exe070) -and (Test-Path (Join-Path $install 'app-0.7.0\resources\agent'))) ("installRoot=$install")
    Step 'per-user-location' ($install.StartsWith($env:LOCALAPPDATA)) $install
    Step 'shortcuts-created' ((Shortcuts).Count -gt 0) ((Shortcuts) -join '; ')
    Step 'uninstall-entry' (Uninstall-Key) 'HKCU Uninstall\GitVizWorkspace'

    # 2) launch the installed app, verify it stays up with its agent, then close it
    $before = @(Get-Process -ErrorAction SilentlyContinue | ForEach-Object { $_.Id })
    $roamBefore = @(Get-ChildItem -LiteralPath $env:APPDATA -Directory -ErrorAction SilentlyContinue | ForEach-Object { $_.Name })
    $localBefore = @(Get-ChildItem -LiteralPath $env:LOCALAPPDATA -Directory -ErrorAction SilentlyContinue | ForEach-Object { $_.Name })
    $app = Start-Process $exe070 -PassThru
    Start-Sleep -Seconds 20
    $alive = -not $app.HasExited
    $children = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object { $_.ProcessId -notin $before -and $_.Name -match 'GitViz|agent' } | ForEach-Object { "$($_.Name)#$($_.ProcessId)<-$($_.ParentProcessId)" })
    Step 'app-launch' $alive ("alive=$alive newProcesses=" + ($children -join ','))
    $roamNew = @(Get-ChildItem -LiteralPath $env:APPDATA -Directory -ErrorAction SilentlyContinue | Where-Object { $_.Name -notin $roamBefore } | ForEach-Object { $_.FullName })
    $localNew = @(Get-ChildItem -LiteralPath $env:LOCALAPPDATA -Directory -ErrorAction SilentlyContinue | Where-Object { $_.Name -notin $localBefore } | ForEach-Object { $_.FullName })
    # Electron creates its userData directory even for the --squirrel-install launch, so match by name rather than "newly created".
    $gitvizRoaming = @(Get-ChildItem -LiteralPath $env:APPDATA -Directory -ErrorAction SilentlyContinue | Where-Object { $_.Name -match 'gitviz' } | ForEach-Object { $_.FullName })
    $userData = $gitvizRoaming | Select-Object -First 1
    $workspaceFiles = if ($userData) { @(Get-ChildItem -LiteralPath (Join-Path $userData 'workspace') -Recurse -File -ErrorAction SilentlyContinue | Select-Object -First 8 | ForEach-Object { $_.Name }) } else { @() }
    Step 'user-data-created' ([bool]$userData -and (Test-Path (Join-Path $userData 'workspace'))) ("userDataDir=$userData; existedBeforeLaunch=$($userData -and (Split-Path $userData -Leaf) -in $roamBefore); workspaceFiles=[$($workspaceFiles -join ';')]; newRoaming=[$($roamNew -join ';')]")
    if ($userData) { $marker = Join-Path $userData 'p10-marker.txt' }
    # Informational: a non-interactive session may have no window to close.
    $handle = $app.MainWindowHandle
    if ($alive) { [void]$app.CloseMainWindow(); Start-Sleep -Seconds 8 }
    $closedGracefully = $app.HasExited
    Step 'graceful-close-info' $true ("mainWindowHandle=$handle exitedAfterCloseMainWindow=$closedGracefully")
    # The meaningful check: killing only the parent must not leave the agent or helpers behind.
    if (-not $closedGracefully) { Stop-Process -Id $app.Id -Force -ErrorAction SilentlyContinue }
    $deadline = (Get-Date).AddSeconds(30)
    do { Start-Sleep -Seconds 2; $leftover = @(Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Id -notin $before -and $_.ProcessName -match 'GitViz|agent' }) } while ($leftover.Count -gt 0 -and (Get-Date) -lt $deadline)
    Step 'parent-exit-leaves-no-child-processes' ($leftover.Count -eq 0) ("parentKilled=$(-not $closedGracefully) leftover=" + (($leftover | ForEach-Object { "$($_.ProcessName)#$($_.Id)" }) -join ','))
    if ($leftover.Count) { $leftover | Stop-Process -Force -ErrorAction SilentlyContinue }
    if ($userData) { Set-Content -LiteralPath $marker -Value 'user data must survive upgrade and uninstall' }

    # 3) upgrade to 0.7.1
    $u = Start-Process (Join-Path $Root 'v071\GitVizWorkspaceSetup.exe') -ArgumentList '--silent' -PassThru
    $uExited = $u.WaitForExit(180000)
    Start-Sleep -Seconds 10
    Stop-App
    $dirs = @(Get-ChildItem -LiteralPath $install -Directory -Filter 'app-*' -ErrorAction SilentlyContinue | ForEach-Object { $_.Name })
    Step 'upgrade-exit' ($uExited -and $u.ExitCode -eq 0) ("exited=$uExited exitCode=$($u.ExitCode)")
    Step 'upgrade-installed-0.7.1' (Test-Path (Join-Path $install 'app-0.7.1\GitVizWorkspace.exe')) ("appDirs=" + ($dirs -join ','))
    Step 'shortcuts-after-upgrade' ((Shortcuts).Count -gt 0) ((Shortcuts) -join '; ')
    Step 'user-data-survives-upgrade' ($marker -and (Test-Path $marker)) "$marker"

    # 4) uninstall
    $un = Start-Process (Join-Path $install 'Update.exe') -ArgumentList '--uninstall', '-s' -PassThru
    $unExited = $un.WaitForExit(180000)
    Stop-App
    $deadline = (Get-Date).AddSeconds(90)
    do { Start-Sleep -Seconds 5; $left = @(Get-ChildItem -LiteralPath $install -Recurse -File -ErrorAction SilentlyContinue).Count } while ($left -gt 0 -and (Get-Date) -lt $deadline)
    Step 'uninstall-exit' ($unExited -and $un.ExitCode -eq 0) ("exited=$unExited exitCode=$($un.ExitCode)")
    $remaining = @(Get-ChildItem -LiteralPath $install -Recurse -File -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName })
    Step 'app-files-removed' (-not (Test-Path (Join-Path $install 'app-0.7.1\GitVizWorkspace.exe')) -and -not (Test-Path (Join-Path $install 'app-0.7.0\GitVizWorkspace.exe'))) ("remainingFiles=$($remaining.Count) " + (($remaining | Select-Object -First 5) -join '; '))
    Step 'shortcuts-removed' ((Shortcuts).Count -eq 0) ((Shortcuts) -join '; ')
    Step 'uninstall-entry-removed' (-not (Uninstall-Key)) 'HKCU Uninstall\GitVizWorkspace'
    Step 'user-data-survives-uninstall' ($marker -and (Test-Path $marker)) "$marker"
    [ordered]@{ account = "$env:COMPUTERNAME\$env:USERNAME"; finishedAt = (Get-Date).ToString('o'); passed = (@($steps | Where-Object { -not $_.ok }).Count -eq 0); steps = $steps } | ConvertTo-Json -Depth 5 -Compress
}

try {
    Import-Module Hyper-V
    $vm = Get-VM -Id $vmId
    if ($vm.Name -ne 'GitViz-P01-Clean-Win11') { throw 'Unexpected VM.' }
    $result.vmStateBefore = $vm.State.ToString()
    if ($Mode -eq 'Inspect') { $result.status = 'inspected' }
    elseif ($Mode -eq 'Restore') {
        if ($vm.State -ne 'Off') { Stop-VM -VM $vm -TurnOff -Force }
        $snap = Get-VMSnapshot -VM (Get-VM -Id $vmId) -Name $checkpointName -ErrorAction SilentlyContinue
        if (-not $snap) { throw 'Checkpoint not found; nothing to restore.' }
        Restore-VMSnapshot -VMSnapshot $snap -Confirm:$false
        $result.restoredCheckpoint = $true
        $result.vmStateAfter = (Get-VM -Id $vmId).State.ToString()
        $result.status = 'restored'
    } else {
        foreach ($f in 'v070\GitVizWorkspaceSetup.exe', 'v071\GitVizWorkspaceSetup.exe') { if (-not (Test-Path -LiteralPath (Join-Path $StageDirectory $f))) { throw "Missing $f in stage directory." } }
        $result.installerSha256 = [ordered]@{ v070 = (Get-FileHash (Join-Path $StageDirectory 'v070\GitVizWorkspaceSetup.exe')).Hash; v071 = (Get-FileHash (Join-Path $StageDirectory 'v071\GitVizWorkspaceSetup.exe')).Hash }
        if ($vm.State -ne 'Off') { throw 'VM must be Off before the test; run -Mode Restore first.' }
        $result.networkSwitches = @(Get-VMNetworkAdapter -VM $vm | ForEach-Object { "$($_.SwitchName)" })
        $admincred = Import-Clixml (Join-Path $p01 'credentials\admin.xml')
        $testercred = Import-Clixml (Join-Path $p01 'credentials\tester.xml')
        $existing = Get-VMSnapshot -VM $vm -Name $checkpointName -ErrorAction SilentlyContinue
        if ($existing) { Restore-VMSnapshot -VMSnapshot $existing -Confirm:$false; $result.checkpointReused = $true }
        else { Checkpoint-VM -VM $vm -SnapshotName $checkpointName }
        $vm = Get-VM -Id $vmId
        $result.checkpoint = $checkpointName
        try {
            Start-VM -VM $vm
            if (-not (Wait-Guest $admincred 300)) { throw 'Guest did not become reachable in 300 seconds.' }
            $session = New-PSSession -VMId $vmId -Credential $admincred
            try {
                Invoke-Command -Session $session -ScriptBlock { New-Item -ItemType Directory -Path 'C:\P10\Installer' -Force | Out-Null; & icacls.exe 'C:\P10' /grant '*S-1-5-32-545:(OI)(CI)RX' | Out-Null }
                Copy-Item -LiteralPath (Join-Path $StageDirectory 'v070') -Destination 'C:\P10\Installer\v070' -ToSession $session -Recurse -Force
                Copy-Item -LiteralPath (Join-Path $StageDirectory 'v071') -Destination 'C:\P10\Installer\v071' -ToSession $session -Recurse -Force
                $guestHash = Invoke-Command -Session $session -ScriptBlock { (Get-FileHash 'C:\P10\Installer\v070\GitVizWorkspaceSetup.exe').Hash }
                if ($guestHash -ne $result.installerSha256.v070) { throw 'Installer transfer integrity mismatch.' }
            } finally { Remove-PSSession $session }
            $json = Invoke-Command -VMId $vmId -Credential $testercred -ScriptBlock $guestTest -ArgumentList 'C:\P10\Installer'
            $result.guest = $json | ConvertFrom-Json
            $result.status = if ($result.guest.passed) { 'passed' } else { 'failed-steps' }
        } finally {
            $current = Get-VM -Id $vmId
            if ($current.State -ne 'Off') { Stop-VM -VM $current -TurnOff -Force }
            $snap = Get-VMSnapshot -VM (Get-VM -Id $vmId) -Name $checkpointName -ErrorAction SilentlyContinue
            if ($snap) { Restore-VMSnapshot -VMSnapshot $snap -Confirm:$false; $result.restoredCheckpoint = $true }
            $result.vmStateAfter = (Get-VM -Id $vmId).State.ToString()
        }
    }
} catch {
    $result.error = $_.Exception.Message
} finally {
    $result.finishedAt = (Get-Date).ToString('o')
    $result | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $work "$Mode-result.json") -Encoding utf8
}
if ($result.status -notin 'inspected', 'restored', 'passed') { exit 1 }
exit 0
