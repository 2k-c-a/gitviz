[CmdletBinding()]
param([Parameter(Mandatory)][string]$StageDirectory, [switch]$Elevated)
# Release acceptance run on the clean P01 VM (5th stage). Needs a UAC-elevated host token and a stage folder with
# v070\GitVizWorkspaceSetup.exe and v071\GitVizWorkspaceSetup.exe. Runs the same scenario for three standard users
# (plain name, Korean name with a space, name with special characters), injects failures for the first user,
# copies the user data out for host-side inspection, then restores the checkpoint. Nothing is installed on the host.
$ErrorActionPreference = 'Stop'
$repo = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$work = Join-Path $repo '.validation\p11-vm'
$p01 = Join-Path $repo '.validation\p01-vm'
$vmId = [guid]'25a434e8-1335-47ad-a6c1-cdec28192507'
$checkpointName = 'pre-p10-installer'
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) {
    if ($Elevated) { throw 'Administrator token required.' }
    $arguments = '-NoProfile -ExecutionPolicy Bypass -File "{0}" -StageDirectory "{1}" -Elevated' -f $PSCommandPath, $StageDirectory
    $process = Start-Process "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -ArgumentList $arguments -Verb RunAs -WindowStyle Hidden -Wait -PassThru
    exit $process.ExitCode
}
New-Item -ItemType Directory -Path $work -Force | Out-Null
$result = [ordered]@{ mode = 'Release'; status = 'failed'; startedAt = (Get-Date).ToString('o') }

function Wait-Guest($credential, [int]$Seconds) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        try { $name = Invoke-Command -VMId $vmId -Credential $credential -ScriptBlock { $env:COMPUTERNAME } -ErrorAction Stop; if ($name -eq 'GITVIZ-P01') { return $true } } catch { Start-Sleep -Seconds 5 }
    }
    return $false
}

# Runs inside the guest as a standard user. Returns one JSON document.
$userScenario = {
    param($Root, [bool]$Inject, [int]$Port)
    $ErrorActionPreference = 'Continue'
    $steps = New-Object System.Collections.ArrayList
    function Step($name, $ok, $detail) { [void]$steps.Add([ordered]@{ step = $name; ok = $ok; detail = "$detail" }) }
    function Stop-App { Get-Process -Name GitVizWorkspace -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue; Start-Sleep -Seconds 3 }
    $install = Join-Path $env:LOCALAPPDATA 'GitVizWorkspace'
    $roam = Join-Path $env:APPDATA 'gitviz-workspace'
    $data = Join-Path $roam 'workspace'
    function Hashes($dir) { $h = [ordered]@{}; if (Test-Path -LiteralPath $dir) { foreach ($f in Get-ChildItem -LiteralPath $dir -File -ErrorAction SilentlyContinue | Sort-Object Name) { try { $h[$f.Name] = (Get-FileHash -LiteralPath $f.FullName -Algorithm SHA256 -ErrorAction Stop).Hash.Substring(0, 16) } catch { $h[$f.Name] = 'locked' } } }; $h }
    function Cdp([int]$port, [string]$expr, [int]$wait = 40) {
        $deadline = (Get-Date).AddSeconds($wait); $page = $null
        while ((Get-Date) -lt $deadline -and -not $page) {
            try { $page = (Invoke-RestMethod "http://127.0.0.1:$port/json" -TimeoutSec 3) | Where-Object { $_.type -eq 'page' } | Select-Object -First 1 } catch { }
            if (-not $page) { Start-Sleep -Seconds 2 }
        }
        if (-not $page) { return 'NO_PAGE' }
        try {
            $ws = New-Object System.Net.WebSockets.ClientWebSocket
            $ws.ConnectAsync([Uri]$page.webSocketDebuggerUrl, [Threading.CancellationToken]::None).Wait(10000) | Out-Null
            $msg = @{ id = 1; method = 'Runtime.evaluate'; params = @{ expression = $expr; awaitPromise = $true; returnByValue = $true } } | ConvertTo-Json -Depth 6 -Compress
            $bytes = [Text.Encoding]::UTF8.GetBytes($msg)
            $ws.SendAsync([ArraySegment[byte]]$bytes, 'Text', $true, [Threading.CancellationToken]::None).Wait(10000) | Out-Null
            $buffer = New-Object byte[] 65536; $sb = New-Object Text.StringBuilder
            do { $r = $ws.ReceiveAsync([ArraySegment[byte]]$buffer, [Threading.CancellationToken]::None); if (-not $r.Wait(30000)) { return 'TIMEOUT' }; [void]$sb.Append([Text.Encoding]::UTF8.GetString($buffer, 0, $r.Result.Count)) } while (-not $r.Result.EndOfMessage)
            $ws.Dispose()
            $o = $sb.ToString() | ConvertFrom-Json
            if ($o.result.exceptionDetails) { return 'EXCEPTION: ' + $o.result.exceptionDetails.text + ' ' + $o.result.exceptionDetails.exception.description }
            return [string]$o.result.result.value
        } catch { return 'CDP_ERROR: ' + $_.Exception.Message }
    }
    $probe = "JSON.stringify({href:location.href,title:document.title,ready:document.readyState,body:(document.body&&document.body.innerText||'').slice(0,200),workspace:typeof window.workspace,desktop:typeof window.desktop,require:typeof require,process:typeof process})"
    $health = "(async()=>JSON.stringify(await window.workspace.getHealth()))()"
    # The agent needs a few seconds after the page loads; poll until it reports ready (or the budget ends).
    function Wait-Health([int]$port, [int]$sec = 60) {
        $end = (Get-Date).AddSeconds($sec); $h = ''
        do { $h = Cdp $port $health 20; if ($h -match '"state":"ready"') { break }; Start-Sleep -Seconds 3 } while ((Get-Date) -lt $end)
        $h
    }
    function Launch-Debug($exe, [int]$port) { Start-Process $exe -ArgumentList "--remote-debugging-port=$port" -PassThru }

    $isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    $devtools = @(Get-Command node.exe, python.exe, python3.exe, git.exe, npm.cmd, pip.exe -ErrorAction SilentlyContinue | Where-Object { $_.Source -notlike '*\Microsoft\WindowsApps\*' } | ForEach-Object { $_.Source })
    Step 'precheck' (-not $isAdmin -and $devtools.Count -eq 0 -and -not (Test-Path $install) -and -not (Test-Path $roam)) ("user=$env:USERNAME profile=$env:USERPROFILE admin=$isAdmin devtools=[$($devtools -join ';')] installExists=$(Test-Path $install) dataExists=$(Test-Path $roam)")
    $sig = (Get-AuthenticodeSignature (Join-Path $Root 'v070\GitVizWorkspaceSetup.exe')).Status.ToString()
    Step 'installer-signature-info' $true $sig

    # install 0.7.0
    $sw = [Diagnostics.Stopwatch]::StartNew()
    $p = Start-Process (Join-Path $Root 'v070\GitVizWorkspaceSetup.exe') -ArgumentList '--silent' -PassThru
    $exited = $p.WaitForExit(240000); Start-Sleep -Seconds 12; Stop-App
    Step 'install-exit' ($exited -and $p.ExitCode -eq 0) ("exited=$exited code=$($p.ExitCode) seconds=$([int]$sw.Elapsed.TotalSeconds)")
    $exe = Join-Path $install 'app-0.7.0\GitVizWorkspace.exe'
    Step 'files-installed' ((Test-Path $exe) -and (Test-Path (Join-Path $install 'Update.exe')) -and (Test-Path (Join-Path $install 'app-0.7.0\resources\agent\gitviz-workspace-agent.exe')) -and (Test-Path (Join-Path $install 'app-0.7.0\resources\app.asar'))) $install
    Step 'per-user-no-admin' ($install.StartsWith($env:LOCALAPPDATA)) $install
    $lnk = @(Get-ChildItem -LiteralPath @($env:APPDATA, (Join-Path $env:USERPROFILE 'Desktop')) -Recurse -Filter 'GitVizWorkspace*.lnk' -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName })
    Step 'shortcuts' ($lnk.Count -ge 1) ($lnk -join '; ')
    $uk = Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\GitVizWorkspace' -ErrorAction SilentlyContinue
    Step 'uninstall-entry' ([bool]$uk) ("DisplayName=$($uk.DisplayName) DisplayVersion=$($uk.DisplayVersion) UninstallString=$($uk.UninstallString)")
    $dataInInstall = @(Get-ChildItem -LiteralPath $install -Recurse -File -ErrorAction SilentlyContinue | Where-Object { $_.Name -match '\.(sqlite3?|db)(-wal|-shm)?$|^key\.dpapi$|^secret-check\.bin$|^writer\.lock$' } | ForEach-Object { $_.FullName })
    Step 'no-user-data-in-install-dir-after-install' ($dataInInstall.Count -eq 0) ("found=[$($dataInInstall -join ';')]")
    $installBytes = (Get-ChildItem -LiteralPath $install -Recurse -File -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum

    # first launch (as the shortcut would start it)
    $app = Start-Process $exe -PassThru; Start-Sleep -Seconds 25
    Step 'first-launch-alive' (-not $app.HasExited) ("alive=$(-not $app.HasExited) processes=" + @(Get-Process -Name GitVizWorkspace, gitviz-workspace-agent -ErrorAction SilentlyContinue).Count)
    $agentProc = @(Get-Process -Name gitviz-workspace-agent -ErrorAction SilentlyContinue).Count
    Step 'agent-process-running' ($agentProc -ge 1) "agentProcesses=$agentProc"
    Stop-App
    $files1 = Hashes $data
    Step 'user-data-created' ($files1.Contains('workspace.sqlite3') -and $files1.Contains('key.dpapi') -and $files1.Contains('secret-check.bin')) ("dataDir=$data files=[$($files1.Keys -join ',')]")
    $underInstall = $data.StartsWith($install)
    Step 'data-outside-install-dir' (-not $underInstall) "data=$data install=$install"
    $dataInInstall2 = @(Get-ChildItem -LiteralPath $install -Recurse -File -ErrorAction SilentlyContinue | Where-Object { $_.Name -match '\.(sqlite3?|db)(-wal|-shm)?$|^key\.dpapi$|^secret-check\.bin$' })
    Step 'no-user-data-in-install-dir-after-run' ($dataInInstall2.Count -eq 0) "found=$($dataInInstall2.Count)"
    $logs = @(Get-ChildItem -LiteralPath @($roam, (Join-Path $env:LOCALAPPDATA 'gitviz-workspace')) -Recurse -File -Include '*.log' -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName })
    Step 'log-files-info' $true ("logFiles=[$($logs -join ';')]")

    # second launch with the renderer inspected through the debugging port (test-only; the packaged exe accepts it)
    $app = Launch-Debug $exe $Port
    $pr = Cdp $Port $probe
    Step 'frontend-loaded' ($pr -match 'gitviz://app' -and $pr -match '"ready":"complete"') $pr
    Step 'renderer-isolated' ($pr -match '"require":"undefined"' -and $pr -match '"process":"undefined"' -and $pr -match '"workspace":"object"') $pr
    $h = Wait-Health $Port 60
    Step 'ipc-agent-db-health' ($h -match '"state":"ready"') $h
    Stop-App
    $files2 = Hashes $data
    $same = ($files1['key.dpapi'] -eq $files2['key.dpapi']) -and ($files1['secret-check.bin'] -eq $files2['secret-check.bin'])
    Step 'key-stable-across-restart' $same ("key1=$($files1['key.dpapi']) key2=$($files2['key.dpapi'])")
    Set-Content -LiteralPath (Join-Path $data 'p11-marker.txt') -Value 'user data must survive restart, upgrade and uninstall'

    if ($Inject) {
        # F1 key file missing -> app must not destroy anything
        $before = Hashes $data; $keyPath = Join-Path $data 'key.dpapi'; Move-Item -LiteralPath $keyPath -Destination "$keyPath.bak" -Force
        $app = Launch-Debug $exe $Port; $h = Wait-Health $Port 60; $alive = -not $app.HasExited; Stop-App
        $after = Hashes $data; Move-Item -LiteralPath "$keyPath.bak" -Destination $keyPath -Force -ErrorAction SilentlyContinue
        $dbSame = ($before['workspace.sqlite3'] -eq $after['workspace.sqlite3'])
        Step 'fail-key-missing-no-data-loss' ($dbSame -and (Test-Path $keyPath)) ("appAliveAtProbe=$alive health=$h dbUnchanged=$dbSame files=[$($after.Keys -join ',')]")
        # F2 agent executable missing
        $agentExe = Join-Path $install 'app-0.7.0\resources\agent\gitviz-workspace-agent.exe'; Move-Item -LiteralPath $agentExe -Destination "$agentExe.bak" -Force
        $before = Hashes $data; $app = Launch-Debug $exe $Port; $h = Wait-Health $Port 60; $alive = -not $app.HasExited; Stop-App
        Move-Item -LiteralPath "$agentExe.bak" -Destination $agentExe -Force; $after = Hashes $data
        Step 'fail-agent-missing-no-data-loss' ($before['workspace.sqlite3'] -eq $after['workspace.sqlite3']) ("appAliveAtProbe=$alive health=$h")
        # F3 data directory not writable
        $before = Hashes $data; & icacls.exe $data /deny "${env:USERNAME}:(OI)(CI)(WD,AD,DC)" | Out-Null
        $app = Launch-Debug $exe $Port; $h = Wait-Health $Port 60; $alive = -not $app.HasExited; Stop-App
        & icacls.exe $data /remove:d $env:USERNAME | Out-Null; $after = Hashes $data
        Step 'fail-data-dir-readonly-no-data-loss' (($before['workspace.sqlite3'] -eq $after['workspace.sqlite3']) -and (Test-Path (Join-Path $data 'key.dpapi'))) ("appAliveAtProbe=$alive health=$h")
        # F4 settings file missing
        $win = Join-Path $data 'window.json'; if (Test-Path $win) { Remove-Item -LiteralPath $win -Force }
        $app = Launch-Debug $exe $Port; $h = Wait-Health $Port 60; Stop-App
        Step 'fail-settings-missing-recovers' ($h -match '"state":"ready"') ("windowJsonRecreated=$(Test-Path $win) health=$h")
        # F5 abnormal termination while running
        $app = Launch-Debug $exe $Port; [void](Cdp $Port $probe 40); Stop-Process -Id $app.Id -Force; Start-Sleep -Seconds 5
        $left = @(Get-Process -Name gitviz-workspace-agent -ErrorAction SilentlyContinue).Count
        $app = Launch-Debug $exe $Port; $h = Wait-Health $Port 60; Stop-App
        Step 'fail-abnormal-kill-recovers' ($left -eq 0 -and $h -match '"state":"ready"') ("orphanAgents=$left healthAfter=$h")
    }

    # data checkpoint before the upgrade
    $beforeUpgrade = Hashes $data
    $u = Start-Process (Join-Path $Root 'v071\GitVizWorkspaceSetup.exe') -ArgumentList '--silent' -PassThru
    $uExited = $u.WaitForExit(240000); Start-Sleep -Seconds 12; Stop-App
    $dirs = @(Get-ChildItem -LiteralPath $install -Directory -Filter 'app-*' -ErrorAction SilentlyContinue | ForEach-Object { $_.Name })
    Step 'upgrade-exit' ($uExited -and $u.ExitCode -eq 0) ("exited=$uExited code=$($u.ExitCode) appDirs=[$($dirs -join ',')]")
    $exe71 = Join-Path $install 'app-0.7.1\GitVizWorkspace.exe'
    Step 'upgrade-installed' (Test-Path $exe71) $exe71
    $afterUpgrade = Hashes $data
    Step 'data-intact-after-upgrade-before-launch' (($beforeUpgrade['workspace.sqlite3'] -eq $afterUpgrade['workspace.sqlite3']) -and ($beforeUpgrade['key.dpapi'] -eq $afterUpgrade['key.dpapi']) -and (Test-Path (Join-Path $data 'p11-marker.txt'))) ("db=$($afterUpgrade['workspace.sqlite3'])")
    $app = Launch-Debug $exe71 $Port; $h = Wait-Health $Port 60; $pr = Cdp $Port $probe
    Step 'upgraded-app-runs-with-old-data' ($pr -match 'gitviz://app' -and $h -match '"state":"ready"') ("probe=$pr health=$h")
    Stop-App
    $afterUpgradeRun = Hashes $data
    Step 'key-unchanged-after-upgraded-run' ($beforeUpgrade['key.dpapi'] -eq $afterUpgradeRun['key.dpapi'] -and $beforeUpgrade['secret-check.bin'] -eq $afterUpgradeRun['secret-check.bin']) ''
    $lnk2 = @(Get-ChildItem -LiteralPath @($env:APPDATA, (Join-Path $env:USERPROFILE 'Desktop')) -Recurse -Filter 'GitVizWorkspace*.lnk' -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName })
    Step 'shortcuts-after-upgrade' ($lnk2.Count -ge 1) ($lnk2 -join '; ')

    # uninstall
    $un = Start-Process (Join-Path $install 'Update.exe') -ArgumentList '--uninstall', '-s' -PassThru
    $unExited = $un.WaitForExit(240000); Stop-App
    $deadline = (Get-Date).AddSeconds(90)
    do { Start-Sleep -Seconds 5; $left = @(Get-ChildItem -LiteralPath $install -Recurse -File -ErrorAction SilentlyContinue).Count } while ($left -gt 0 -and (Get-Date) -lt $deadline)
    $remaining = @(Get-ChildItem -LiteralPath $install -Recurse -File -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName.Substring($install.Length) })
    Step 'uninstall-exit' ($unExited -and $un.ExitCode -eq 0) ("exited=$unExited code=$($un.ExitCode)")
    Step 'uninstall-app-files-removed' ($remaining.Count -eq 0) ("remaining=$($remaining.Count) [" + (($remaining | Select-Object -First 6) -join '; ') + "]")
    $lnk3 = @(Get-ChildItem -LiteralPath @($env:APPDATA, (Join-Path $env:USERPROFILE 'Desktop')) -Recurse -Filter 'GitVizWorkspace*.lnk' -ErrorAction SilentlyContinue)
    Step 'uninstall-shortcuts-removed' ($lnk3.Count -eq 0) "count=$($lnk3.Count)"
    Step 'uninstall-entry-removed' (-not (Test-Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\GitVizWorkspace')) ''
    $afterUninstall = Hashes $data
    Step 'user-data-kept-after-uninstall' ((Test-Path (Join-Path $data 'p11-marker.txt')) -and ($afterUninstall['workspace.sqlite3'] -eq $afterUpgradeRun['workspace.sqlite3'])) ("files=[$($afterUninstall.Keys -join ',')]")
    [ordered]@{ user = "$env:COMPUTERNAME\$env:USERNAME"; profile = $env:USERPROFILE; localAppData = $env:LOCALAPPDATA; installBytes = $installBytes; dataFiles = $afterUninstall
        passed = (@($steps | Where-Object { -not $_.ok }).Count -eq 0); steps = $steps } | ConvertTo-Json -Depth 6 -Compress
}

function New-Pass { -join ((48..57) + (65..90) + (97..122) | Get-Random -Count 20 | ForEach-Object { [char]$_ }) + 'aA1!' }
$korean = [string]::new([char[]](0xD14C, 0xC2A4, 0xD2B8, 0x20, 0xC0AC, 0xC6A9, 0xC790))      # "<test> <user>" with a space
$users = @(
    @{ name = 'P01Tester'; tag = 'plain'; inject = $true; port = 9333; credential = $null },
    @{ name = $korean; tag = 'korean-space'; inject = $false; port = 9334; credential = $null },
    @{ name = 'qa&(x)'; tag = 'special-chars'; inject = $false; port = 9335; credential = $null }
)
try {
    Import-Module Hyper-V
    $vm = Get-VM -Id $vmId
    if ($vm.Name -ne 'GitViz-P01-Clean-Win11') { throw 'Unexpected VM.' }
    if ($vm.State -ne 'Off') { throw 'VM must be Off before the test.' }
    foreach ($f in 'v070', 'v071') { if (-not (Test-Path -LiteralPath (Join-Path $StageDirectory "$f\GitVizWorkspaceSetup.exe"))) { throw "Missing $f in stage directory." } }
    $result.installerSha256 = [ordered]@{ v070 = (Get-FileHash (Join-Path $StageDirectory 'v070\GitVizWorkspaceSetup.exe')).Hash; v071 = (Get-FileHash (Join-Path $StageDirectory 'v071\GitVizWorkspaceSetup.exe')).Hash }
    $admincred = Import-Clixml (Join-Path $p01 'credentials\admin.xml')
    $testercred = Import-Clixml (Join-Path $p01 'credentials\tester.xml')
    $users[0].credential = $testercred
    $existing = Get-VMSnapshot -VM $vm -Name $checkpointName -ErrorAction SilentlyContinue
    if (-not $existing) { throw 'Checkpoint pre-p10-installer not found.' }
    Restore-VMSnapshot -VMSnapshot $existing -Confirm:$false
    $evidence = Join-Path $work ('evidence-' + (Get-Date -Format 'yyyyMMdd-HHmmss')); New-Item -ItemType Directory -Path $evidence -Force | Out-Null
    $result.evidence = $evidence
    try {
        Start-VM -VM (Get-VM -Id $vmId)
        if (-not (Wait-Guest $admincred 300)) { throw 'Guest did not become reachable in 300 seconds.' }
        $session = New-PSSession -VMId $vmId -Credential $admincred
        try {
            $result.guestInfo = Invoke-Command -Session $session -ScriptBlock {
                $os = Get-CimInstance Win32_OperatingSystem
                $sac = (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\CI\Policy' -Name VerifiedAndReputablePolicyState -ErrorAction SilentlyContinue).VerifiedAndReputablePolicyState
                [ordered]@{ caption = $os.Caption; version = $os.Version; build = $os.BuildNumber; architecture = $os.OSArchitecture; locale = (Get-Culture).Name; uiLanguage = (Get-UICulture).Name
                    smartAppControlState = $sac; uac = (Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System' -Name EnableLUA -ErrorAction SilentlyContinue).EnableLUA
                    devTools = @(Get-Command node.exe, python.exe, git.exe -ErrorAction SilentlyContinue | Where-Object { $_.Source -notlike '*\Microsoft\WindowsApps\*' } | ForEach-Object { $_.Source }) }
            }
            Invoke-Command -Session $session -ScriptBlock { New-Item -ItemType Directory -Path 'C:\P11\Installer' -Force | Out-Null; & icacls.exe 'C:\P11' /grant '*S-1-5-32-545:(OI)(CI)RX' | Out-Null }
            Copy-Item -LiteralPath (Join-Path $StageDirectory 'v070') -Destination 'C:\P11\Installer\v070' -ToSession $session -Recurse -Force
            Copy-Item -LiteralPath (Join-Path $StageDirectory 'v071') -Destination 'C:\P11\Installer\v071' -ToSession $session -Recurse -Force
            $gh = Invoke-Command -Session $session -ScriptBlock { (Get-FileHash 'C:\P11\Installer\v070\GitVizWorkspaceSetup.exe').Hash }
            if ($gh -ne $result.installerSha256.v070) { throw 'Installer transfer integrity mismatch.' }
            foreach ($u in $users[1..2]) {
                $pw = New-Pass; $u.password = $pw
                Invoke-Command -Session $session -ScriptBlock { param($n, $p) New-LocalUser -Name $n -Password (ConvertTo-SecureString $p -AsPlainText -Force) -PasswordNeverExpires -AccountNeverExpires | Out-Null; Add-LocalGroupMember -SID 'S-1-5-32-545' -Member $n } -ArgumentList $u.name, $pw
                $u.credential = New-Object System.Management.Automation.PSCredential(".\$($u.name)", (ConvertTo-SecureString $pw -AsPlainText -Force))
            }
        } finally { Remove-PSSession $session }
        $result.users = @()
        foreach ($u in $users) {
            $entry = [ordered]@{ tag = $u.tag; name = $u.name }
            try {
                $json = Invoke-Command -VMId $vmId -Credential $u.credential -ScriptBlock $userScenario -ArgumentList 'C:\P11\Installer', $u.inject, $u.port
                $entry.guest = $json | ConvertFrom-Json
            } catch { $entry.error = $_.Exception.Message }
            # copy the user's data out for host-side inspection (admin can read other profiles)
            try {
                $session = New-PSSession -VMId $vmId -Credential $admincred
                $profileDir = Invoke-Command -Session $session -ScriptBlock { param($n) (Get-CimInstance Win32_UserProfile | Where-Object { $_.LocalPath -like "*\$n" } | Select-Object -First 1).LocalPath } -ArgumentList $u.name
                $entry.profileDir = $profileDir
                if ($profileDir) {
                    $dest = Join-Path $evidence $u.tag; New-Item -ItemType Directory -Path $dest -Force | Out-Null
                    $src = Join-Path $profileDir 'AppData\Roaming\gitviz-workspace\workspace'
                    if (Invoke-Command -Session $session -ScriptBlock { param($s) Test-Path -LiteralPath $s } -ArgumentList $src) { Copy-Item -LiteralPath $src -Destination $dest -FromSession $session -Recurse -Force }
                    $entry.otherUserAccess = Invoke-Command -VMId $vmId -Credential $users[0].credential -ScriptBlock { param($p) try { [bool](Get-ChildItem -LiteralPath $p -ErrorAction Stop) } catch { 'denied: ' + $_.Exception.GetType().Name } } -ArgumentList (Join-Path $profileDir 'AppData\Roaming\gitviz-workspace') -ErrorAction SilentlyContinue
                }
                Remove-PSSession $session
            } catch { $entry.copyError = $_.Exception.Message }
            $result.users += $entry
        }
        $result.status = if (@($result.users | Where-Object { $_.guest -and $_.guest.passed }).Count -eq $users.Count) { 'passed' } else { 'failed-steps' }
    } finally {
        $current = Get-VM -Id $vmId
        if ($current.State -ne 'Off') { Stop-VM -VM $current -TurnOff -Force }
        $snap = Get-VMSnapshot -VM (Get-VM -Id $vmId) -Name $checkpointName -ErrorAction SilentlyContinue
        if ($snap) { Restore-VMSnapshot -VMSnapshot $snap -Confirm:$false; $result.restoredCheckpoint = $true }
        $result.vmStateAfter = (Get-VM -Id $vmId).State.ToString()
    }
} catch {
    $result.error = $_.Exception.Message
} finally {
    $result.finishedAt = (Get-Date).ToString('o')
    $result | ConvertTo-Json -Depth 9 | Set-Content -LiteralPath (Join-Path $work 'Release-result.json') -Encoding utf8
}
if ($result.status -ne 'passed') { exit 1 }
exit 0
