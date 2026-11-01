[CmdletBinding()]
param([Parameter(Mandatory)][string]$SetupExe, [string]$Label = 'run', [switch]$Elevated)
# Cross-account isolation on the clean P01 VM: two standard users (A = P01Tester, B = P01TesterB) each install and run the app and create data;
# each then tries to read the other's data/install folders. Checks the real file-system ACLs, not what the UI shows. Needs a UAC-elevated host token.
# Not covered: the app's own cryptographic separation (safeStorage/DPAPI wrapped key) is not exercised here.
# The checkpoint is restored at the end of the run, which also removes account B.
$ErrorActionPreference = 'Stop'
$repo = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$work = Join-Path $repo '.validation\p11-vm'
$p01 = Join-Path $repo '.validation\p01-vm'
$vmId = [guid]'25a434e8-1335-47ad-a6c1-cdec28192507'
$checkpointName = 'pre-p10-installer'
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) {
    if ($Elevated) { throw 'Administrator token required.' }
    $arguments = '-NoProfile -ExecutionPolicy Bypass -File "{0}" -SetupExe "{1}" -Label "{2}" -Elevated' -f $PSCommandPath, $SetupExe, $Label
    $process = Start-Process "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -ArgumentList $arguments -Verb RunAs -WindowStyle Hidden -Wait -PassThru
    exit $process.ExitCode
}
New-Item -ItemType Directory -Path $work -Force | Out-Null
Set-Content -LiteralPath (Join-Path $work "Isolation-$Label-started.txt") -Value (Get-Date).ToString('o')
$result = [ordered]@{ label = $Label; status = 'failed'; startedAt = (Get-Date).ToString('o') }

function Wait-Guest($credential, [int]$Seconds) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        try { $name = Invoke-Command -VMId $vmId -Credential $credential -ScriptBlock { $env:COMPUTERNAME } -ErrorAction Stop; if ($name -eq 'GITVIZ-P01') { return $true } } catch { Start-Sleep -Seconds 5 }
    }
    return $false
}

# Runs in the guest as the account under test: install, run once, create data, report.
$guestCreate = {
    param($Setup, $Role)
    $ErrorActionPreference = 'Continue'
    $steps = New-Object System.Collections.ArrayList
    function Step($name, $ok, $detail) { [void]$steps.Add([ordered]@{ step = $name; ok = [bool]$ok; detail = "$detail" }) }
    function Stop-App { Get-Process -Name GitVizWorkspace, gitviz-workspace-agent -ErrorAction SilentlyContinue | Where-Object { $_.SessionId -eq (Get-Process -Id $PID).SessionId } | Stop-Process -Force -ErrorAction SilentlyContinue; Start-Sleep -Seconds 3 }
    $install = Join-Path $env:LOCALAPPDATA 'GitVizWorkspace'
    $data = Join-Path $env:APPDATA 'gitviz-workspace\workspace'
    $p = Start-Process $Setup -ArgumentList '--silent' -PassThru
    $exited = $p.WaitForExit(240000); Start-Sleep -Seconds 12; Stop-App
    Step "$Role-install" ($exited -and $p.ExitCode -eq 0 -and (Test-Path (Join-Path $install 'Update.exe'))) "exited=$exited code=$($p.ExitCode) user=$env:USERNAME"
    $exe = Get-ChildItem -LiteralPath $install -Directory -Filter 'app-*' | Select-Object -First 1 | ForEach-Object { Join-Path $_.FullName 'GitVizWorkspace.exe' }
    $app = Start-Process $exe -PassThru; Start-Sleep -Seconds 25; $alive = -not $app.HasExited; Stop-App
    Step "$Role-launch" $alive "alive=$alive"
    Set-Content -LiteralPath (Join-Path $data "marker-$Role.txt") -Value "private data of $Role"
    $files = @(Get-ChildItem -LiteralPath $data -File -ErrorAction SilentlyContinue | ForEach-Object { $_.Name })
    Step "$Role-data-created" ($files -contains 'workspace.sqlite3' -and $files -contains "marker-$Role.txt") "files=[$($files -join ',')]"
    $key = Join-Path $data 'key.dpapi'
    [ordered]@{ user = $env:USERNAME; sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value; data = $data; install = $install
        keySha = $(if (Test-Path $key) { (Get-FileHash -LiteralPath $key -Algorithm SHA256).Hash } else { '' })
        dbSha = (Get-FileHash -LiteralPath (Join-Path $data 'workspace.sqlite3') -Algorithm SHA256 -ErrorAction SilentlyContinue).Hash
        dataAcl = @(& icacls.exe $data | ForEach-Object { [string]$_ }); steps = $steps } | ConvertTo-Json -Depth 6 -Compress
}

# Runs in the guest as the other account: tries to reach the first account's data. Every attempt must be refused by the file system.
$guestAttack = {
    param($Role, $TargetUser, $TargetRole)
    $ErrorActionPreference = 'Continue'
    $profile = "C:\Users\$TargetUser"
    $data = Join-Path $profile 'AppData\Roaming\gitviz-workspace\workspace'
    $install = Join-Path $profile 'AppData\Local\GitVizWorkspace'
    $attempts = New-Object System.Collections.ArrayList
    function Attempt($name, [scriptblock]$action) {
        $outcome = 'allowed'; $detail = ''
        try { $r = & $action; $detail = "$r" } catch { $outcome = 'denied'; $detail = $_.Exception.GetType().Name }
        [void]$attempts.Add([ordered]@{ attempt = $name; outcome = $outcome; detail = $detail })
    }
    Attempt "$Role lists $TargetUser profile" { Get-ChildItem -LiteralPath $profile -Force -ErrorAction Stop | Out-Null; 'listed' }
    Attempt "$Role lists $TargetUser data folder" { Get-ChildItem -LiteralPath $data -Force -ErrorAction Stop | Out-Null; 'listed' }
    Attempt "$Role reads $TargetUser marker file" { Get-Content -LiteralPath (Join-Path $data "marker-$TargetRole.txt") -ErrorAction Stop }
    Attempt "$Role reads $TargetUser database" { [IO.File]::ReadAllBytes((Join-Path $data 'workspace.sqlite3')).Length }
    Attempt "$Role reads $TargetUser key file" { [IO.File]::ReadAllBytes((Join-Path $data 'key.dpapi')).Length }
    Attempt "$Role copies $TargetUser database" { Copy-Item -LiteralPath (Join-Path $data 'workspace.sqlite3') -Destination (Join-Path $env:TEMP 'stolen.sqlite3') -ErrorAction Stop; 'copied' }
    Attempt "$Role writes into $TargetUser data folder" { Set-Content -LiteralPath (Join-Path $data 'planted.txt') -Value 'x' -ErrorAction Stop; 'written' }
    Attempt "$Role lists $TargetUser install folder" { Get-ChildItem -LiteralPath $install -Force -ErrorAction Stop | Out-Null; 'listed' }
    Attempt "$Role changes the ACL of $TargetUser data folder" { & icacls.exe $data /grant "${env:USERNAME}:(OI)(CI)F" 2>&1 | Out-String | ForEach-Object { if ($_ -match 'Successfully processed [1-9]') { 'granted' } else { throw 'icacls refused' } } }
    ConvertTo-Json -InputObject @($attempts) -Depth 4 -Compress
}

$owned = $false   # only a VM this script restored itself is ever stopped or restored again
try {
    Import-Module Hyper-V
    $vm = Get-VM -Id $vmId
    if ($vm.Name -ne 'GitViz-P01-Clean-Win11') { throw 'Unexpected VM.' }
    if ($vm.State -ne 'Off') { throw 'VM is running (someone may be using it). Nothing was changed.' }
    $owned = $true
    if (-not (Test-Path -LiteralPath $SetupExe)) { throw 'SetupExe not found.' }
    $result.setup = [ordered]@{ path = $SetupExe; bytes = (Get-Item -LiteralPath $SetupExe).Length; sha256 = (Get-FileHash -LiteralPath $SetupExe).Hash }
    $admincred = Import-Clixml (Join-Path $p01 'credentials\admin.xml')
    $acred = Import-Clixml (Join-Path $p01 'credentials\tester.xml')
    $bName = 'P01TesterB'
    $bPass = -join ((48..57) + (65..90) + (97..122) | Get-Random -Count 20 | ForEach-Object { [char]$_ }) + 'aA1!'
    $bcred = New-Object pscredential($bName, (ConvertTo-SecureString $bPass -AsPlainText -Force))
    $snap = Get-VMSnapshot -VM (Get-VM -Id $vmId) -Name $checkpointName -ErrorAction Stop
    Restore-VMSnapshot -VMSnapshot $snap -Confirm:$false
    Start-VM -VM (Get-VM -Id $vmId)
    if (-not (Wait-Guest $admincred 300)) { throw 'Guest did not become reachable.' }
    $s = New-PSSession -VMId $vmId -Credential $admincred
    try {
        Invoke-Command -Session $s -ArgumentList $bName, $bPass -ScriptBlock {
            param($Name, $Pass)
            $ErrorActionPreference = 'Continue'
            New-Item -ItemType Directory -Path 'C:\P11\Installer' -Force | Out-Null; & icacls.exe 'C:\P11' /grant '*S-1-5-32-545:(OI)(CI)RX' | Out-Null
            New-LocalUser -Name $Name -Password (ConvertTo-SecureString $Pass -AsPlainText -Force) -AccountNeverExpires -PasswordNeverExpires | Out-Null
            Add-LocalGroupMember -SID 'S-1-5-32-545' -Member $Name
        }
        Copy-Item -LiteralPath $SetupExe -Destination 'C:\P11\Installer\GitVizWorkspaceSetup.exe' -ToSession $s -Force
        $result.administratorsGroup = @(Invoke-Command -Session $s -ScriptBlock { Get-LocalGroupMember -SID 'S-1-5-32-544' | ForEach-Object { [string]$_.Name } })
    } finally { Remove-PSSession $s }
    $setup = 'C:\P11\Installer\GitVizWorkspaceSetup.exe'
    $a = (Invoke-Command -VMId $vmId -Credential $acred -ScriptBlock $guestCreate -ArgumentList $setup, 'A') | ConvertFrom-Json
    $b = (Invoke-Command -VMId $vmId -Credential $bcred -ScriptBlock $guestCreate -ArgumentList $setup, 'B') | ConvertFrom-Json
    $result.accountA = $a; $result.accountB = $b
    $aToB = (Invoke-Command -VMId $vmId -Credential $acred -ScriptBlock $guestAttack -ArgumentList 'A', $bName, 'B') | ConvertFrom-Json
    $bToA = (Invoke-Command -VMId $vmId -Credential $bcred -ScriptBlock $guestAttack -ArgumentList 'B', 'P01Tester', 'A') | ConvertFrom-Json
    $result.attemptsAToB = $aToB; $result.attemptsBToA = $bToA
    # Each account's own data is intact and distinct.
    $result.distinct = [ordered]@{ keyDiffers = ($a.keySha -ne $b.keySha); dbDiffers = ($a.dbSha -ne $b.dbSha); sidDiffers = ($a.sid -ne $b.sid) }
    $own = Invoke-Command -VMId $vmId -Credential $acred -ScriptBlock { Get-ChildItem (Join-Path $env:APPDATA 'gitviz-workspace\workspace') -File | ForEach-Object { $_.Name } }
    $ownB = Invoke-Command -VMId $vmId -Credential $bcred -ScriptBlock { Get-ChildItem (Join-Path $env:APPDATA 'gitviz-workspace\workspace') -File | ForEach-Object { $_.Name } }
    $result.ownFolders = [ordered]@{ A = @($own); B = @($ownB); aSeesOnlyOwnMarker = (@($own) -contains 'marker-A.txt' -and @($own) -notcontains 'marker-B.txt'); bSeesOnlyOwnMarker = (@($ownB) -contains 'marker-B.txt' -and @($ownB) -notcontains 'marker-A.txt') }
    $allSteps = @($a.steps) + @($b.steps)
    $allDenied = (@($aToB | Where-Object { $_.outcome -ne 'denied' }).Count -eq 0) -and (@($bToA | Where-Object { $_.outcome -ne 'denied' }).Count -eq 0)
    $result.status = if (@($allSteps | Where-Object { -not $_.ok }).Count -eq 0 -and $allDenied -and $result.distinct.keyDiffers -and $result.distinct.sidDiffers -and $result.ownFolders.aSeesOnlyOwnMarker -and $result.ownFolders.bSeesOnlyOwnMarker) { 'passed' } else { 'failed-steps' }
} catch {
    $result.error = $_.Exception.Message
    $result.errorAt = [string]$_.InvocationInfo.PositionMessage
} finally {
    if ($owned) { try {
        $current = Get-VM -Id $vmId
        if ($current.State -ne 'Off') { Stop-VM -VM $current -TurnOff -Force }
        $snap = Get-VMSnapshot -VM (Get-VM -Id $vmId) -Name $checkpointName -ErrorAction SilentlyContinue
        if ($snap) { Restore-VMSnapshot -VMSnapshot $snap -Confirm:$false }
    } catch { $result.restoreError = $_.Exception.Message } }
    $result.finishedAt = (Get-Date).ToString('o')
    $result | ConvertTo-Json -Depth 9 | Set-Content -LiteralPath (Join-Path $work "Isolation-$Label-result.json") -Encoding utf8
}
if ($result.status -ne 'passed') { exit 1 }
