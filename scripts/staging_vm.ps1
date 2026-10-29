[CmdletBinding()]
param([ValidateSet('Up', 'Capture', 'Close', 'Trust', 'Probe', 'Clone', 'Edit', 'Scroll', 'Down')][string]$Mode = 'Up', [string]$StageDirectory = '', [string]$Keys = '', [switch]$Elevated)
# Attaches the P01 clean VM to the host staging server (https://localhost:8443) for desktop OAuth / web+app acceptance.
# Host changes (portproxy + firewall rule) and guest changes (autologon, trust, portproxy, env var) are all undone by -Mode Down
# (stop VM, restore checkpoint, remove host rules). Needs a UAC-elevated host token.
$ErrorActionPreference = 'Stop'
$repo = Split-Path $PSScriptRoot -Parent
$work = Join-Path $repo '.validation\p10-vm'
$p01 = Join-Path $repo '.validation\p01-vm'
$staging = Join-Path $repo '.validation\staging'
$vmId = [guid]'25a434e8-1335-47ad-a6c1-cdec28192507'
$checkpointName = 'pre-p10-installer'
$ruleName = 'GitViz staging 8443 (Default Switch only)'
$statePath = Join-Path $work 'staging-state.json'
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) {
    if ($Elevated) { throw 'Administrator token required.' }
    $arguments = '-NoProfile -ExecutionPolicy Bypass -File "{0}" -Mode {1} -Elevated' -f $PSCommandPath, $Mode
    if ($StageDirectory) { $arguments += ' -StageDirectory "{0}"' -f $StageDirectory }
    if ($Keys) { $arguments += ' -Keys "{0}"' -f $Keys }
    $process = Start-Process "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -ArgumentList $arguments -Verb RunAs -WindowStyle Hidden -Wait -PassThru
    exit $process.ExitCode
}
New-Item -ItemType Directory -Path $work -Force | Out-Null
$result = [ordered]@{ mode = $Mode; status = 'failed'; startedAt = (Get-Date).ToString('o') }

function Wait-Guest($credential, [int]$Seconds) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        try { if ((Invoke-Command -VMId $vmId -Credential $credential -ScriptBlock { $env:COMPUTERNAME } -ErrorAction Stop) -eq 'GITVIZ-P01') { return $true } } catch { Start-Sleep -Seconds 5 }
    }
    return $false
}
# Console thumbnail (RGB565) -> PNG, so the desktop can be inspected without a console session.
function Save-VMScreen([string]$Path) {
    Add-Type -AssemblyName System.Drawing
    $service = Get-CimInstance -Namespace root\virtualization\v2 -ClassName Msvm_VirtualSystemManagementService
    $system = Get-CimInstance -Namespace root\virtualization\v2 -ClassName Msvm_ComputerSystem -Filter ("Name='{0}'" -f $vmId.ToString().ToUpper())
    $w = 1024; $h = 768
    $reply = Invoke-CimMethod -InputObject $service -MethodName GetVirtualSystemThumbnailImage -Arguments @{ TargetSystem = $system; WidthPixels = [uint16]$w; HeightPixels = [uint16]$h }
    if (-not $reply.ImageData) { return $false }
    $bitmap = New-Object System.Drawing.Bitmap($w, $h, [System.Drawing.Imaging.PixelFormat]::Format16bppRgb565)
    $rect = New-Object System.Drawing.Rectangle(0, 0, $w, $h)
    $data = $bitmap.LockBits($rect, [System.Drawing.Imaging.ImageLockMode]::WriteOnly, $bitmap.PixelFormat)
    [Runtime.InteropServices.Marshal]::Copy([byte[]]$reply.ImageData, 0, $data.Scan0, [Math]::Min($reply.ImageData.Length, $data.Stride * $h))
    $bitmap.UnlockBits($data); $bitmap.Save($Path, [System.Drawing.Imaging.ImageFormat]::Png); $bitmap.Dispose()
    return $true
}
# Runs a script in the standard user's interactive desktop through a one-shot scheduled task and returns its JSON output.
function Invoke-Interactive($credential, [string]$Name, [string]$Body) {
    Invoke-Command -VMId $vmId -Credential $admincred -ArgumentList $Name, $Body -ScriptBlock {
        param($Name, $Body)
        New-Item -ItemType Directory -Path 'C:\P10' -Force | Out-Null
        & icacls.exe 'C:\P10' /grant '*S-1-5-32-545:(OI)(CI)M' | Out-Null
        Set-Content -LiteralPath "C:\P10\$Name.ps1" -Value $Body -Encoding UTF8
        Remove-Item "C:\P10\$Name.json" -ErrorAction SilentlyContinue
        $principal = New-ScheduledTaskPrincipal -UserId 'GITVIZ-P01\P01Tester' -LogonType Interactive -RunLevel Limited
        $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -File C:\P10\$Name.ps1"
        Register-ScheduledTask -TaskName "GitViz$Name" -Action $action -Principal $principal -Force | Out-Null
        Start-ScheduledTask -TaskName "GitViz$Name"
        $deadline = (Get-Date).AddSeconds(60)
        while (-not (Test-Path "C:\P10\$Name.json") -and (Get-Date) -lt $deadline) { Start-Sleep -Seconds 2 }
        Unregister-ScheduledTask -TaskName "GitViz$Name" -Confirm:$false -ErrorAction SilentlyContinue
        if (Test-Path "C:\P10\$Name.json") { Get-Content "C:\P10\$Name.json" -Raw } else { '{"error":"no output"}' }
    }
}

try {
    Import-Module Hyper-V
    $vm = Get-VM -Id $vmId
    if ($vm.Name -ne 'GitViz-P01-Clean-Win11') { throw 'Unexpected VM.' }
    $admincred = Import-Clixml (Join-Path $p01 'credentials\admin.xml')
    $testercred = Import-Clixml (Join-Path $p01 'credentials\tester.xml')
    $result.vmStateBefore = $vm.State.ToString()

    if ($Mode -eq 'Down') {
        if ((Get-VM -Id $vmId).State -ne 'Off') { Stop-VM -VM (Get-VM -Id $vmId) -TurnOff -Force }
        $snap = Get-VMSnapshot -VM (Get-VM -Id $vmId) -Name $checkpointName -ErrorAction SilentlyContinue
        if ($snap) { Restore-VMSnapshot -VMSnapshot $snap -Confirm:$false; $result.restoredCheckpoint = $true }
        if (Test-Path $statePath) {
            $state = Get-Content $statePath -Raw | ConvertFrom-Json
            & netsh.exe interface portproxy delete v4tov4 listenaddress=$($state.hostIp) listenport=8443 | Out-Null
            $result.portproxyRemoved = $true
        }
        Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue | Remove-NetFirewallRule
        $result.firewallRuleRemoved = -not (Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue)
        Remove-Item $statePath -ErrorAction SilentlyContinue
        $result.vmStateAfter = (Get-VM -Id $vmId).State.ToString()
        $result.status = 'down'
    }
    elseif ($Mode -eq 'Capture') {
        $result.screenshot = Save-VMScreen (Join-Path $work 'staging-capture.png')
        $result.windows = (Invoke-Interactive $testercred 'window' 'Get-Process GitVizWorkspace -ErrorAction SilentlyContinue | Select-Object Id,SessionId,MainWindowTitle,@{n="Handle";e={[int64]$_.MainWindowHandle}} | ConvertTo-Json -Compress | Set-Content C:\P10\window.json -Encoding UTF8; if (-not (Test-Path C:\P10\window.json) -or (Get-Item C:\P10\window.json).Length -eq 0) { "[]" | Set-Content C:\P10\window.json }') | ConvertFrom-Json
        $result.status = 'captured'
    }
    elseif ($Mode -eq 'Trust') {
        # The app's main process uses Node's own CA list (not the Windows store), so the self-signed staging cert must be given explicitly.
        $session = New-PSSession -VMId $vmId -Credential $admincred
        try { Copy-Item -LiteralPath (Join-Path $staging 'tls\server.pem') -Destination 'C:\P10\server.pem' -ToSession $session -Force } finally { Remove-PSSession $session }
        $result.env = Invoke-Command -VMId $vmId -Credential $testercred -ScriptBlock {
            [Environment]::SetEnvironmentVariable('NODE_EXTRA_CA_CERTS', 'C:\P10\server.pem', 'User')
            @{ NODE_EXTRA_CA_CERTS = [Environment]::GetEnvironmentVariable('NODE_EXTRA_CA_CERTS', 'User') } | ConvertTo-Json -Compress
        } | ConvertFrom-Json
        $restart = 'Get-Process GitVizWorkspace -EA SilentlyContinue | Stop-Process -Force; Start-Sleep -Seconds 3; $env:NODE_EXTRA_CA_CERTS = [Environment]::GetEnvironmentVariable("NODE_EXTRA_CA_CERTS","User"); $env:GITVIZ_SERVER_ORIGIN = [Environment]::GetEnvironmentVariable("GITVIZ_SERVER_ORIGIN","User"); $exe = Get-ChildItem (Join-Path $env:LOCALAPPDATA "GitVizWorkspace") -Directory -Filter "app-*" | Sort-Object Name | Select-Object -Last 1 | ForEach-Object { Join-Path $_.FullName "GitVizWorkspace.exe" }; Start-Process $exe; @{ restarted = $true; exe = $exe; ca = $env:NODE_EXTRA_CA_CERTS } | ConvertTo-Json -Compress | Set-Content C:\P10\restart.json -Encoding UTF8'
        $result.restart = (Invoke-Interactive $testercred 'restart' $restart) | ConvertFrom-Json
        Start-Sleep -Seconds 20
        $result.screenshot = Save-VMScreen (Join-Path $work 'staging-after-trust.png')
        $result.status = 'trusted'
    }
    elseif ($Mode -eq 'Clone') {
        # N06 needs a local project whose HEAD equals the server snapshot target commit.
        $result.clone = Invoke-Command -VMId $vmId -Credential $testercred -ScriptBlock {
            $o = [ordered]@{}
            $git = (Get-Command git.exe -ErrorAction SilentlyContinue).Source
            if (-not $git) { $git = @('C:\Program Files\Git\cmd\git.exe', (Join-Path $env:LOCALAPPDATA 'Programs\Git\cmd\git.exe')) | Where-Object { Test-Path $_ } | Select-Object -First 1 }
            $o.git = $git
            if ($git) {
                $o.version = (& $git --version)
                $dest = Join-Path $env:USERPROFILE 'Documents\20240636'
                if (-not (Test-Path $dest)) { & $git clone --quiet https://github.com/2k-c-a/20240636 $dest 2>&1 | Out-Null }
                $o.dest = $dest
                $o.head = (& $git -C $dest rev-parse HEAD)
                $o.branch = (& $git -C $dest rev-parse --abbrev-ref HEAD)
                $o.remote = (& $git -C $dest remote get-url origin)
            } else {
                $o.downloads = @(Get-ChildItem (Join-Path $env:USERPROFILE 'Downloads') -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Name)
            }
            $o
        }
        $result.status = 'cloned'
    }
    elseif ($Mode -eq 'Scroll') {
        # Sends keys (SendKeys syntax, e.g. {END} or {PGUP}) to the app window in the tester's session, then screenshots.
        $keysArg = if ($Keys) { $Keys } else { '{END}' }
        $body = '$p = Get-Process GitVizWorkspace -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1; $r = [ordered]@{ found = [bool]$p }; if ($p) { Add-Type -AssemblyName Microsoft.VisualBasic, System.Windows.Forms; [Microsoft.VisualBasic.Interaction]::AppActivate($p.Id); Start-Sleep -Milliseconds 800; [System.Windows.Forms.SendKeys]::SendWait(''' + ($keysArg -replace "'", "''") + '''); Start-Sleep -Seconds 1 }; $r | ConvertTo-Json -Compress | Set-Content C:\P10\scroll.json -Encoding UTF8'
        $result.scroll = (Invoke-Interactive $testercred 'scroll' $body) | ConvertFrom-Json
        Start-Sleep -Seconds 2
        $result.screenshot = Save-VMScreen (Join-Path $work 'staging-scroll.png')
        $result.status = 'scrolled'
    }
    elseif ($Mode -eq 'Edit') {
        # The fixed-input capture needs an uncommitted change; append one line to a tracked file of the throwaway clone (HEAD unchanged).
        $result.edit = Invoke-Command -VMId $vmId -Credential $testercred -ScriptBlock {
            $git = Join-Path $env:LOCALAPPDATA 'Programs\Git\cmd\git.exe'; $dest = Join-Path $env:USERPROFILE 'Documents\20240636'
            $files = @(& $git -C $dest ls-files)
            # Undo an earlier docs-only test edit; the parser reads Python and JS/TS only.
            & $git -C $dest checkout -- DESIGN.md 2>$null
            $target = $files | Where-Object { $_ -like 'backend/app/services/*.py' -and $_ -notlike '*__init__.py' } | Select-Object -First 1
            if ($target) { Add-Content -LiteralPath (Join-Path $dest $target) -Value "`n# gitviz staging test edit" }
            # The server snapshot's co-change pairs are among html files; touching one lets the history section show a real pair.
            $pairFile = 'frontend/html/login.html'
            if ($files -contains $pairFile -and -not (@(& $git -C $dest status --porcelain) -match 'login\.html')) { Add-Content -LiteralPath (Join-Path $dest $pairFile) -Value "`n<!-- gitviz staging test edit -->" }
            [ordered]@{ tracked = $files; edited = $target; head = (& $git -C $dest rev-parse HEAD); status = @(& $git -C $dest status --porcelain) }
        }
        $result.status = 'edited'
    }
    elseif ($Mode -eq 'Probe') {
        # Same runtime and fetch as the app's main process (ELECTRON_RUN_AS_NODE), to expose the error cause the UI hides.
        $js = @'
const body = JSON.stringify({ challenge: 'a'.repeat(64), deviceLabel: 'probe' });
const out = {};
(async () => {
  for (const host of ['localhost', '127.0.0.1']) {
    try {
      const r = await fetch(`https://${host}:8443/api/desktop/v1/auth/transactions`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body, signal: AbortSignal.timeout(15000) });
      out[host] = { status: r.status };
    } catch (e) { out[host] = { error: String(e.message), cause: e.cause ? { code: e.cause.code, message: String(e.cause.message) } : null }; }
  }
  require('fs').writeFileSync('C:\\P10\\probe.json', JSON.stringify(out));
})();
'@
        $result.probe = Invoke-Command -VMId $vmId -Credential $testercred -ArgumentList $js -ScriptBlock {
            param($Js)
            Set-Content C:\P10\probe.js $Js -Encoding UTF8
            $exe = Get-ChildItem (Join-Path $env:LOCALAPPDATA 'GitVizWorkspace') -Recurse -Filter GitVizWorkspace.exe | Select-Object -First 1
            $o = [ordered]@{ exe = $exe.FullName; extraCa = [Environment]::GetEnvironmentVariable('NODE_EXTRA_CA_CERTS', 'User') }
            $env:ELECTRON_RUN_AS_NODE = '1'
            foreach ($variant in 'withCa', 'withoutCa') {
                if ($variant -eq 'withCa') { $env:NODE_EXTRA_CA_CERTS = 'C:\P10\server.pem' } else { Remove-Item Env:NODE_EXTRA_CA_CERTS -ErrorAction SilentlyContinue }
                Remove-Item C:\P10\probe.json -ErrorAction SilentlyContinue
                $p = Start-Process $exe.FullName -ArgumentList 'C:\P10\probe.js' -PassThru -WindowStyle Hidden
                [void]$p.WaitForExit(40000)
                $o[$variant] = if (Test-Path C:\P10\probe.json) { Get-Content C:\P10\probe.json -Raw | ConvertFrom-Json } else { 'no output' }
            }
            $o.portproxy = (& netsh.exe interface portproxy show v4tov4) -join "`n"
            $o.tcp = @{ v4 = (Test-NetConnection 127.0.0.1 -Port 8443 -WarningAction SilentlyContinue).TcpTestSucceeded; v6 = (Test-NetConnection ::1 -Port 8443 -WarningAction SilentlyContinue).TcpTestSucceeded }
            $o
        }
        $result.status = 'probed'
    }
    elseif ($Mode -eq 'Close') {
        $result.screenshotBefore = Save-VMScreen (Join-Path $work 'staging-before-close.png')
        $body = '$p = Get-Process GitVizWorkspace -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1; $r = [ordered]@{ foundWindowedProcess = [bool]$p }; if ($p) { $r.title = $p.MainWindowTitle; [void]$p.CloseMainWindow(); $r.exitedGracefully = $p.WaitForExit(15000); Start-Sleep -Seconds 5; $r.remaining = @(Get-Process GitVizWorkspace,gitviz-workspace-agent -ErrorAction SilentlyContinue | ForEach-Object { $_.ProcessName + "#" + $_.Id }) }; $r | ConvertTo-Json -Compress | Set-Content C:\P10\close.json -Encoding UTF8'
        $result.close = (Invoke-Interactive $testercred 'close' $body) | ConvertFrom-Json
        $result.screenshotAfter = Save-VMScreen (Join-Path $work 'staging-after-close.png')
        $result.status = 'closed-checked'
    }
    else {
        if (-not (Get-NetTCPConnection -LocalPort 8443 -State Listen -ErrorAction SilentlyContinue)) { throw 'Staging server is not listening on 8443; run scripts/staging_stack.ps1 -Mode Up first.' }
        $cer = Join-Path $staging 'tls\server.cer'
        if (-not (Test-Path $cer)) { throw 'Staging certificate missing.' }
        if (-not $StageDirectory -or -not (Test-Path (Join-Path $StageDirectory 'v070\GitVizWorkspaceSetup.exe'))) { throw 'StageDirectory with v070 installer required.' }
        if ($vm.State -ne 'Off') { throw 'VM must be Off; run -Mode Down first.' }
        $snap = Get-VMSnapshot -VM $vm -Name $checkpointName -ErrorAction SilentlyContinue
        if (-not $snap) { throw 'Checkpoint pre-p10-installer is required as the clean baseline.' }
        Restore-VMSnapshot -VMSnapshot $snap -Confirm:$false
        $vm = Get-VM -Id $vmId
        $switch = Get-VMSwitch -Name 'Default Switch' -ErrorAction SilentlyContinue
        if (-not $switch) { throw 'Hyper-V Default Switch not found.' }
        Get-VMNetworkAdapter -VM $vm | Connect-VMNetworkAdapter -SwitchName 'Default Switch'
        $hostIp = (Get-NetIPAddress -InterfaceAlias 'vEthernet (Default Switch)' -AddressFamily IPv4 -ErrorAction Stop | Select-Object -First 1).IPAddress
        $result.hostIp = $hostIp
        @{ hostIp = $hostIp } | ConvertTo-Json | Set-Content $statePath
        & netsh.exe interface portproxy add v4tov4 listenaddress=$hostIp listenport=8443 connectaddress=127.0.0.1 connectport=8443 | Out-Null
        if (-not (Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue)) {
            New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8443 -InterfaceAlias 'vEthernet (Default Switch)' | Out-Null
        }
        Start-VM -VM $vm
        if (-not (Wait-Guest $admincred 300)) { throw 'Guest did not become reachable.' }
        $session = New-PSSession -VMId $vmId -Credential $admincred
        try {
            Invoke-Command -Session $session -ScriptBlock { New-Item -ItemType Directory -Path 'C:\P10\Installer' -Force | Out-Null; & icacls.exe 'C:\P10' /grant '*S-1-5-32-545:(OI)(CI)M' | Out-Null }
            Copy-Item -LiteralPath $cer -Destination 'C:\P10\server.cer' -ToSession $session -Force
            Copy-Item -LiteralPath (Join-Path $StageDirectory 'v070') -Destination 'C:\P10\Installer\v070' -ToSession $session -Recurse -Force
            $result.guestSetup = Invoke-Command -Session $session -ArgumentList $hostIp -ScriptBlock {
                param($HostIp)
                $out = [ordered]@{}
                $import = Import-Certificate -FilePath 'C:\P10\server.cer' -CertStoreLocation Cert:\LocalMachine\Root
                $out.trustedCertThumbprint = $import.Thumbprint
                & netsh.exe interface portproxy add v4tov4 listenaddress=127.0.0.1 listenport=8443 connectaddress=$HostIp connectport=8443 | Out-Null
                Set-Service iphlpsvc -StartupType Automatic -ErrorAction SilentlyContinue; Start-Service iphlpsvc -ErrorAction SilentlyContinue
                $deadline = (Get-Date).AddSeconds(90); $net = $null
                do { $net = Test-NetConnection -ComputerName github.com -Port 443 -WarningAction SilentlyContinue; if (-not $net.TcpTestSucceeded) { Start-Sleep -Seconds 5 } } while (-not $net.TcpTestSucceeded -and (Get-Date) -lt $deadline)
                $out.githubReachable = [bool]$net.TcpTestSucceeded
                $probe = Test-NetConnection -ComputerName $HostIp -Port 8443 -WarningAction SilentlyContinue
                $out.hostServerReachable = [bool]$probe.TcpTestSucceeded
                $out | ConvertTo-Json -Compress
            } | ConvertFrom-Json
        } finally { Remove-PSSession $session }
        $result.install = Invoke-Command -VMId $vmId -Credential $testercred -ScriptBlock {
            $p = Start-Process 'C:\P10\Installer\v070\GitVizWorkspaceSetup.exe' -ArgumentList '--silent' -PassThru
            $done = $p.WaitForExit(180000); Start-Sleep -Seconds 8
            Get-Process GitVizWorkspace -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
            [Environment]::SetEnvironmentVariable('GITVIZ_SERVER_ORIGIN', 'https://localhost:8443', 'User')
            @{ exited = $done; exitCode = $p.ExitCode; installed = (Test-Path (Join-Path $env:LOCALAPPDATA 'GitVizWorkspace\Update.exe')) } | ConvertTo-Json -Compress
        } | ConvertFrom-Json
        # Temporary autologon so the standard user has an interactive desktop; removed with the checkpoint restore.
        Invoke-Command -VMId $vmId -Credential $admincred -ArgumentList $testercred.GetNetworkCredential().Password -ScriptBlock {
            param($Secret)
            $key = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon'
            Set-ItemProperty $key AutoAdminLogon '1'; Set-ItemProperty $key DefaultUserName 'P01Tester'; Set-ItemProperty $key DefaultDomainName $env:COMPUTERNAME; Set-ItemProperty $key DefaultPassword $Secret
            Restart-Computer -Force
        }
        Start-Sleep -Seconds 30
        if (-not (Wait-Guest $admincred 300)) { throw 'Guest did not return after restart.' }
        $deadline = (Get-Date).AddSeconds(180); $interactive = @()
        do {
            Start-Sleep -Seconds 5
            $interactive = @(Invoke-Command -VMId $vmId -Credential $admincred -ScriptBlock { Get-Process explorer -IncludeUserName -ErrorAction SilentlyContinue | ForEach-Object { [string]$_.UserName } })
        } while (($interactive -notcontains 'GITVIZ-P01\P01Tester') -and (Get-Date) -lt $deadline)
        $result.interactiveExplorerUsers = $interactive
        $launch = 'Start-Process (Join-Path $env:LOCALAPPDATA "GitVizWorkspace\Update.exe") -ArgumentList "--processStart","GitVizWorkspace.exe"; Start-Sleep -Seconds 2; @{ launched = $true; origin = [Environment]::GetEnvironmentVariable("GITVIZ_SERVER_ORIGIN","User") } | ConvertTo-Json -Compress | Set-Content C:\P10\launch.json -Encoding UTF8'
        $result.launch = (Invoke-Interactive $testercred 'launch' $launch) | ConvertFrom-Json
        Start-Sleep -Seconds 25
        $result.screenshot = Save-VMScreen (Join-Path $work 'staging-after-launch.png')
        $result.windows = (Invoke-Interactive $testercred 'window' 'Get-Process GitVizWorkspace -ErrorAction SilentlyContinue | Select-Object Id,SessionId,MainWindowTitle,@{n="Handle";e={[int64]$_.MainWindowHandle}} | ConvertTo-Json -Compress | Set-Content C:\P10\window.json -Encoding UTF8; if (-not (Test-Path C:\P10\window.json) -or (Get-Item C:\P10\window.json).Length -eq 0) { "[]" | Set-Content C:\P10\window.json }') | ConvertFrom-Json
        $result.status = 'up'
    }
} catch {
    $result.error = $_.Exception.Message
} finally {
    $result.finishedAt = (Get-Date).ToString('o')
    $result | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $work "staging-$Mode-result.json") -Encoding utf8
}
if ($result.status -notin 'up', 'down', 'captured', 'closed-checked', 'trusted', 'probed', 'cloned', 'edited', 'scrolled') { exit 1 }
exit 0
