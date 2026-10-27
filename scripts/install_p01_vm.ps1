[CmdletBinding()]
param([ValidateSet('Inspect','InspectDisk','RepairBoot','Install','Finish','Collect','CollectLive','RunLive','Diagnose','Finalize')][string]$Mode='Inspect',[switch]$Elevated,[switch]$BootRepair)
$ErrorActionPreference='Stop'
$repo=Split-Path $PSScriptRoot -Parent
$work=Join-Path $repo '.validation\p01-vm'
$iso=Join-Path $repo '.validation\iso\Windows11_Client_x64_ko-kr_26300_9457.iso'
$vhd=Join-Path $work 'system.vhdx'
$vmId=[guid]'25a434e8-1335-47ad-a6c1-cdec28192507'
$admin=([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if(!$admin){
 if($Elevated){throw 'Administrator token required'}
 $argsLine='-NoProfile -ExecutionPolicy Bypass -File "{0}" -Mode {1} -Elevated' -f $PSCommandPath,$Mode
 if($BootRepair){$argsLine+=' -BootRepair'}
 $p=Start-Process "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -ArgumentList $argsLine -Verb RunAs -WindowStyle Hidden -Wait -PassThru
 exit $p.ExitCode
}
$result=@{mode=$Mode;status='failed'}
$mountedIso=$false
$mountedDisk=$false
try{
 Import-Module Hyper-V
 $vm=Get-VM -Id $vmId
 if($vm.Name -ne 'GitViz-P01-Clean-Win11'){throw 'Unexpected VM'}
 $attached=@(Get-VMHardDiskDrive -VM $vm)
 $result.attachedPaths=@($attached | ForEach-Object {$_.Path})
 $result.expectedPath=$vhd
 if($attached.Count -ne 1){throw 'Unexpected attached disk count'}
 $vhd=[IO.Path]::GetFullPath($attached[0].Path)
 $chain=$vhd
 for($depth=0;$depth -lt 8;$depth++){
  if(!$chain.StartsWith([IO.Path]::GetFullPath($work)+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'Disk outside VM workspace'}
  $details=Get-VHD -Path $chain
  if(!$details.ParentPath){break}
  $chain=[IO.Path]::GetFullPath($details.ParentPath)
 }
 if($chain -ne $result.expectedPath){throw 'Unexpected VHD parent chain'}
 if($Mode -eq 'Finalize'){
  $credential=Import-Clixml (Join-Path $work 'credentials\admin.xml')
  $result.guest=Invoke-Command -VMId $vmId -Credential $credential -ScriptBlock {
   $ErrorActionPreference='Stop'
   $report=Get-Content 'C:\P01\results\acceptance.json' -Raw | ConvertFrom-Json
   if($report.classification -ne 'clean-vm' -or $report.foundation.status -ne 'passed'){throw 'Passing report required'}
   if(Get-ScheduledTask -TaskName GitVizP01Acceptance -ErrorAction SilentlyContinue){Unregister-ScheduledTask -TaskName GitVizP01Acceptance -Confirm:$false}
   $tester=Get-LocalUser P01Tester
   $administrators=@(Get-LocalGroupMember -SID 'S-1-5-32-544' | ForEach-Object {$_.SID.Value})
   if($tester.SID.Value -in $administrators){throw 'Tester unexpectedly belongs to Administrators'}
   @{standardAccount='P01Tester';enabled=$tester.Enabled;administratorMember=$false;automaticTaskRemoved=$true} | ConvertTo-Json -Compress
  }
  foreach($dvd in Get-VMDvdDrive -VM $vm){if($dvd.Path){Set-VMDvdDrive -VMDvdDrive $dvd -Path $null}}
  $result.vmState=(Get-VM -Id $vmId).State.ToString()
  $result.status='finalized'
 } elseif($Mode -eq 'Diagnose'){
  $credential=Import-Clixml (Join-Path $work 'credentials\admin.xml')
  $result.guest=Invoke-Command -VMId $vmId -Credential $credential -ScriptBlock {
   $data=@{signature=(Get-AuthenticodeSignature 'C:\P01\Package\GitVizWorkspaceVerify.exe').Status.ToString()}
   $data.codeIntegrity=@(Get-WinEvent -FilterHashtable @{LogName='Microsoft-Windows-CodeIntegrity/Operational';StartTime=(Get-Date).AddMinutes(-20)} -ErrorAction SilentlyContinue | Where-Object {$_.Message -match 'GitViz|C:\\P01\\'} | Select-Object -First 8 Id,Message)
   $data.profileReports=@(Get-ChildItem 'C:\Users\P01Tester\AppData\Local\Temp' -Directory -Filter 'GitVizP01Acceptance-*' | ForEach-Object {Get-ChildItem -LiteralPath $_.FullName | Select-Object Name,Length})
   $data | ConvertTo-Json -Depth 5 -Compress
  }
  $result.status='diagnosed'
 } elseif($Mode -eq 'RunLive'){
  $credential=Import-Clixml (Join-Path $work 'credentials\admin.xml')
  $session=New-PSSession -VMId $vmId -Credential $credential
  try{
   Copy-Item -LiteralPath (Join-Path $repo 'desktop\tests\run-clean-vm.ps1') -Destination 'C:\P01\run-clean-vm.ps1' -ToSession $session -Force
   Copy-Item -LiteralPath (Join-Path $repo 'desktop\tests\clean-vm.ps1') -Destination 'C:\P01\clean-vm.ps1' -ToSession $session -Force
   $asar=Join-Path $repo 'desktop\out\GitVizWorkspaceVerify-win32-x64\resources\app.asar'
   Copy-Item -LiteralPath $asar -Destination 'C:\P01\Package\resources\app.asar' -ToSession $session -Force
   Copy-Item -LiteralPath (Join-Path $repo 'desktop\out\GitVizWorkspaceVerify-win32-x64\GitVizWorkspaceVerify.exe') -Destination 'C:\P01\Package\GitVizWorkspaceVerify.exe' -ToSession $session -Force
   $asarHash=(Get-FileHash -LiteralPath $asar).Hash
   $result.guest=Invoke-Command -Session $session -ArgumentList $asarHash -ScriptBlock {
    param($ExpectedAsarHash)
    $ErrorActionPreference='Stop'
    if($env:COMPUTERNAME -ne 'GITVIZ-P01'){throw 'Unexpected guest'}
    if((Get-FileHash 'C:\P01\Package\resources\app.asar').Hash -ne $ExpectedAsarHash){throw 'ASAR transfer integrity mismatch'}
    $tester=Get-LocalUser -Name P01Tester
    $explorer=@(Get-Process explorer -IncludeUserName | Select-Object UserName)
    & icacls.exe 'C:\P01\results' /grant "*$($tester.SID.Value):(OI)(CI)M" *> $null
    if($LASTEXITCODE -ne 0){throw 'Result ACL repair failed'}
    if(Test-Path 'C:\P01\results\runner.json'){Move-Item -LiteralPath 'C:\P01\results\runner.json' -Destination ('C:\P01\results\runner-'+(Get-Date -Format yyyyMMdd-HHmmss)+'.json')}
    $principal=New-ScheduledTaskPrincipal -UserId P01Tester -LogonType Interactive -RunLevel Limited
    $action=New-ScheduledTaskAction -Execute 'powershell.exe' -Argument '-NoProfile -ExecutionPolicy Bypass -File C:\P01\run-clean-vm.ps1'
    Register-ScheduledTask -TaskName GitVizP01Acceptance -Action $action -Principal $principal -Force | Out-Null
    Start-ScheduledTask -TaskName GitVizP01Acceptance
    @{interactiveUsers=$explorer;runnerStarted=$true;standardAccountSid=$tester.SID.Value;resultsACL=(Get-Acl 'C:\P01\results').Sddl} | ConvertTo-Json -Depth 4 -Compress
   }
  }finally{Remove-PSSession $session}
  $result.status='standard-user-task-started'
 } elseif($Mode -eq 'CollectLive'){
  $credential=Import-Clixml (Join-Path $work 'credentials\admin.xml')
  $guest=Invoke-Command -VMId $vmId -Credential $credential -ScriptBlock {
   $ErrorActionPreference='Stop'
   if($env:COMPUTERNAME -ne 'GITVIZ-P01'){throw 'Unexpected guest'}
   $payload=@{}
   $task=Get-ScheduledTask -TaskName GitVizP01Acceptance -ErrorAction SilentlyContinue
   $taskInfo=if($task){Get-ScheduledTaskInfo -TaskName GitVizP01Acceptance}else{$null}
   $payload['diagnostics.json']=@{taskState=if($task){$task.State.ToString()}else{'absent'};taskResult=if($taskInfo){$taskInfo.LastTaskResult}else{$null};files=@(Get-ChildItem 'C:\P01\results' | Select-Object Name,Length);profiles=@(Get-ChildItem 'C:\Users\P01Tester\AppData\Local\Temp' -Directory -Filter 'GitVizP01Acceptance-*' -ErrorAction SilentlyContinue | Select-Object Name)} | ConvertTo-Json -Depth 4
   $latest=Get-ChildItem 'C:\Users\P01Tester\AppData\Local\Temp' -Directory -Filter 'GitVizP01Acceptance-*' | Sort-Object LastWriteTime -Descending | Select-Object -First 1
   if($latest -and (Test-Path (Join-Path $latest.FullName 'diagnostic-summary.json'))){$payload['diagnostic-summary.json']=[IO.File]::ReadAllText((Join-Path $latest.FullName 'diagnostic-summary.json'))}
   foreach($file in 'runner.json','acceptance.json','foundation.json','boot-repair-status.txt','boot-repair.log'){
    $path=Join-Path 'C:\P01\results' $file
    if(Test-Path -LiteralPath $path){$payload[$file]=[IO.File]::ReadAllText($path)}
   }
   Remove-ItemProperty -LiteralPath 'HKLM:\Software\Microsoft\Windows\CurrentVersion\Run' -Name GitVizP01Verification -ErrorAction SilentlyContinue
   Set-ItemProperty -LiteralPath 'HKLM:\Software\Microsoft\Windows NT\CurrentVersion\Winlogon' -Name AutoAdminLogon -Value '0'
   Remove-ItemProperty -LiteralPath 'HKLM:\Software\Microsoft\Windows NT\CurrentVersion\Winlogon' -Name DefaultPassword -ErrorAction SilentlyContinue
   $payload | ConvertTo-Json -Compress
  }
  $payload=$guest | ConvertFrom-Json
  $evidence=Join-Path $work 'evidence'
  New-Item -ItemType Directory -Path $evidence -Force | Out-Null
  foreach($property in $payload.PSObject.Properties){
   if($property.Name -notin @('runner.json','acceptance.json','foundation.json','boot-repair-status.txt','boot-repair.log','diagnostics.json','diagnostic-summary.json')){throw 'Unexpected evidence filename'}
   [IO.File]::WriteAllText((Join-Path $evidence $property.Name),$property.Value,[Text.UTF8Encoding]::new($false))
  }
  if(!$payload.'runner.json'){throw 'No guest runner result'}
  $result.runner=$payload.'runner.json' | ConvertFrom-Json
  $result.automaticRunnerRemoved=$true
  $result.status='collected-live'
 } elseif($Mode -eq 'Inspect'){
  $di=Mount-DiskImage -ImagePath $iso -PassThru
  $mountedIso=$true
  $drive=($di | Get-Volume).DriveLetter
  $image=Get-ChildItem "${drive}:\sources\install.*" | Where-Object {$_.Extension -in '.wim','.esd'} | Select-Object -First 1
  $result.images=@(Get-WindowsImage -ImagePath $image.FullName | Select-Object ImageIndex,ImageName,ImageDescription)
  $result.vmState=$vm.State.ToString()
  $result.vhd=Get-VHD -Path $vhd | Select-Object VhdType,Size,FileSize
  $result.status='inspected'
 } elseif($Mode -eq 'RepairBoot'){
  if($vm.State -ne 'Off'){Stop-VM -VM $vm -TurnOff -Confirm:$false}
  $di=Mount-DiskImage -ImagePath $iso -PassThru
  $mountedIso=$true
  $drive=($di | Get-Volume).DriveLetter
  & (Join-Path $PSScriptRoot 'p01_boot_repair_iso.ps1') -Directory $work -SourceIsoRoot "${drive}:\"
  $dvds=@(Get-VMDvdDrive -VM $vm | Sort-Object ControllerLocation)
  Set-VMDvdDrive -VMDvdDrive $dvds[0] -Path (Join-Path $work 'boot-repair-full.iso')
  foreach($extra in ($dvds | Select-Object -Skip 1)){Set-VMDvdDrive -VMDvdDrive $extra -Path $null}
  Set-VMFirmware -VM $vm -FirstBootDevice $dvds[0]
  Start-VM -VM $vm
  $result.status='official-winpe-boot-started'
 } elseif($Mode -eq 'InspectDisk'){
  if($vm.State -ne 'Off'){throw 'VM must be off'}
  $mounted=Mount-VHD -Path $vhd -PassThru
  $mountedDisk=$true
  $result.partitions=@(Get-Partition -DiskNumber $mounted.DiskNumber | Select-Object PartitionNumber,Type,DriveLetter,AccessPaths,Size)
  $result.status='disk-inspected'
 } elseif($Mode -in 'Install','Finish'){
  if($Mode -eq 'Install'){
  if(Test-Path (Join-Path $work 'installation-started.json')){throw 'Installation was already attempted; inspect before retrying'}
  if((Get-VHD -Path $vhd).FileSize -gt 16MB){throw 'Expected untouched blank VM disk'}
  if((Get-VMNetworkAdapter -VM $vm | Where-Object SwitchName)){throw 'VM must remain disconnected'}
  if($vm.State -ne 'Off'){Stop-VM -VM $vm -TurnOff -Confirm:$false}
  # Stopping the VM may merge its automatic checkpoint and change the leaf path.
  $currentDisk=@(Get-VMHardDiskDrive -VM $vm)
  if($currentDisk.Count -ne 1){throw 'Unexpected disk after VM stop'}
  $vhd=[IO.Path]::GetFullPath($currentDisk[0].Path)
  if($vhd -ne $result.expectedPath){throw 'Expected merged original P01 VHDX after stop; inspect checkpoint state'}
  $di=Mount-DiskImage -ImagePath $iso -PassThru
  $mountedIso=$true
  $drive=($di | Get-Volume).DriveLetter
  $image=Get-ChildItem "${drive}:\sources\install.*" | Where-Object {$_.Extension -in '.wim','.esd'} | Select-Object -First 1
  if((Get-WindowsImage -ImagePath $image.FullName -Index 1).ImageName -ne 'Windows 11 Home'){throw 'Unexpected image index'}
  $mounted=Mount-VHD -Path $vhd -PassThru
  $mountedDisk=$true
  $disk=Get-Disk -Number $mounted.DiskNumber
  if($disk.PartitionStyle -ne 'RAW' -or $disk.Size -ne 80GB){throw 'Only the blank 80GiB P01 virtual disk may be initialized'}
  @{vmId=$vmId;diskNumber=$disk.Number;diskUniqueId=$disk.UniqueId;vhd=$vhd;image='Windows 11 Home';startedAt=(Get-Date).ToString('o')} | ConvertTo-Json | Set-Content (Join-Path $work 'installation-started.json') -Encoding utf8
  $disk | Initialize-Disk -PartitionStyle GPT
  $efi=New-Partition -DiskNumber $disk.Number -Size 260MB -GptType '{c12a7328-f81f-11d2-ba4b-00a0c93ec93b}' -AssignDriveLetter
  $efi | Format-Volume -FileSystem FAT32 -NewFileSystemLabel P01System -Confirm:$false | Out-Null
  New-Partition -DiskNumber $disk.Number -Size 16MB -GptType '{e3c9e316-0b5c-4db8-817d-f92df00215ae}' | Out-Null
  $windows=New-Partition -DiskNumber $disk.Number -UseMaximumSize -AssignDriveLetter
  $windows | Format-Volume -FileSystem NTFS -NewFileSystemLabel P01Windows -Confirm:$false | Out-Null
  $destination="$($windows.DriveLetter):\"
  & "$env:SystemRoot\System32\dism.exe" /Apply-Image "/ImageFile:$($image.FullName)" /Index:1 "/ApplyDir:$destination" /CheckIntegrity /Verify /English *> (Join-Path $work 'apply-image.log')
  if($LASTEXITCODE -ne 0){throw "DISM Apply-Image failed: $LASTEXITCODE"}
  }else{
   if($vm.State -ne 'Off'){throw 'VM must be off for finishing installation'}
   $mounted=Mount-VHD -Path $vhd -PassThru
   $mountedDisk=$true
   $efi=Get-Partition -DiskNumber $mounted.DiskNumber | Where-Object Type -eq 'System'
   $windows=Get-Partition -DiskNumber $mounted.DiskNumber | Where-Object Type -eq 'Basic'
   if(!$efi.DriveLetter){$efi | Add-PartitionAccessPath -AssignDriveLetter; $efi=Get-Partition -DiskNumber $mounted.DiskNumber -PartitionNumber $efi.PartitionNumber}
   if(!$windows.DriveLetter){$windows | Add-PartitionAccessPath -AssignDriveLetter; $windows=Get-Partition -DiskNumber $mounted.DiskNumber -PartitionNumber $windows.PartitionNumber}
   $destination="$($windows.DriveLetter):\"
   if(!(Test-Path "${destination}Windows\System32\config\SYSTEM")){throw 'Applied Windows image missing'}
  }
  if(!$BootRepair){
  $bootArgs='"{0}Windows" /s {1}: /f UEFI /c /v' -f $destination,$efi.DriveLetter
  $boot=Start-Process "$env:SystemRoot\System32\bcdboot.exe" -ArgumentList $bootArgs -WindowStyle Hidden -Wait -PassThru -RedirectStandardOutput (Join-Path $work 'bcdboot.log') -RedirectStandardError (Join-Path $work 'bcdboot-error.log')
  if($boot.ExitCode -ne 0){throw "BCDBoot failed: $($boot.ExitCode)"}
  }
  $secretDir=Join-Path $work 'credentials'
  New-Item -ItemType Directory -Path $secretDir -Force | Out-Null
  $sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
  & icacls.exe $secretDir /inheritance:r /grant:r "*$($sid):(OI)(CI)F" '*S-1-5-18:(OI)(CI)F' *> $null
  if($LASTEXITCODE -ne 0){throw 'Credential directory ACL failed'}
  function New-FixturePassword {
   $bytes=New-Object byte[] 24
   $rng=[Security.Cryptography.RandomNumberGenerator]::Create()
   try{$rng.GetBytes($bytes)}finally{$rng.Dispose()}
   'P01!'+[Convert]::ToBase64String($bytes)
  }
  $adminPassword=New-FixturePassword
  $testerPassword=New-FixturePassword
  [pscredential]::new('P01Admin',(ConvertTo-SecureString $adminPassword -AsPlainText -Force)) | Export-Clixml (Join-Path $secretDir 'admin.xml')
  [pscredential]::new('P01Tester',(ConvertTo-SecureString $testerPassword -AsPlainText -Force)) | Export-Clixml (Join-Path $secretDir 'tester.xml')
  $answer=@"
<?xml version="1.0" encoding="utf-8"?>
<unattend xmlns="urn:schemas-microsoft-com:unattend" xmlns:wcm="http://schemas.microsoft.com/WMIConfig/2002/State">
 <settings pass="specialize"><component name="Microsoft-Windows-Shell-Setup" processorArchitecture="amd64" publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS"><ComputerName>GITVIZ-P01</ComputerName><TimeZone>Korea Standard Time</TimeZone></component><component name="Microsoft-Windows-Deployment" processorArchitecture="amd64" publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS"><RunSynchronous><RunSynchronousCommand wcm:action="add"><Order>1</Order><Path>cmd.exe /c C:\P01\setup-guest.cmd</Path></RunSynchronousCommand></RunSynchronous></component></settings>
 <settings pass="oobeSystem"><component name="Microsoft-Windows-Shell-Setup" processorArchitecture="amd64" publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS">
  <OOBE><HideEULAPage>true</HideEULAPage><HideOnlineAccountScreens>true</HideOnlineAccountScreens><HideWirelessSetupInOOBE>true</HideWirelessSetupInOOBE><ProtectYourPC>3</ProtectYourPC></OOBE>
  <UserAccounts><LocalAccounts>
   <LocalAccount wcm:action="add"><Name>P01Admin</Name><Group>Administrators</Group><Password><Value>$adminPassword</Value><PlainText>true</PlainText></Password></LocalAccount>
   <LocalAccount wcm:action="add"><Name>P01Tester</Name><Group>Users</Group><Password><Value>$testerPassword</Value><PlainText>true</PlainText></Password></LocalAccount>
  </LocalAccounts></UserAccounts>
  <AutoLogon><Enabled>true</Enabled><LogonCount>1</LogonCount><Username>P01Tester</Username><Domain>.</Domain><Password><Value>$testerPassword</Value><PlainText>true</PlainText></Password></AutoLogon>
 </component><component name="Microsoft-Windows-International-Core" processorArchitecture="amd64" publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS"><InputLocale>0412:00000412</InputLocale><SystemLocale>ko-KR</SystemLocale><UILanguage>ko-KR</UILanguage><UserLocale>ko-KR</UserLocale></component></settings>
</unattend>
"@
  [xml]$validatedAnswer=$answer
  New-Item -ItemType Directory -Path "${destination}Windows\Panther" -Force | Out-Null
  [IO.File]::WriteAllText("${destination}Windows\Panther\unattend.xml",$answer,[Text.UTF8Encoding]::new($false))
  $stage="${destination}P01"
  New-Item -ItemType Directory -Path $stage,"$stage\results" -Force | Out-Null
  & robocopy.exe (Join-Path $repo 'desktop\out\GitVizWorkspaceVerify-win32-x64') "$stage\Package" /E /R:0 /W:0 /NFL /NDL /NJH /NJS *> (Join-Path $work 'package-copy.log')
  if($LASTEXITCODE -ge 8){throw 'Verification package copy failed'}
  Copy-Item -LiteralPath (Join-Path $repo 'desktop\tests\clean-vm.ps1') -Destination "$stage\clean-vm.ps1"
  Copy-Item -LiteralPath (Join-Path $repo 'desktop\tests\run-clean-vm.ps1') -Destination "$stage\run-clean-vm.ps1"
  & icacls.exe "$stage\results" /grant '*S-1-5-32-545:(OI)(CI)M' *> $null
  if($LASTEXITCODE -ne 0){throw 'Guest result ACL failed'}
  '@echo off', 'reg add HKLM\Software\Microsoft\Windows\CurrentVersion\Run /v GitVizP01Verification /t REG_SZ /d "powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\P01\run-clean-vm.ps1" /f', 'exit /b %errorlevel%' | Set-Content "$stage\setup-guest.cmd" -Encoding ascii
  Dismount-VHD -Path $vhd
  $mountedDisk=$false
  $hardDrive=Get-VMHardDiskDrive -VM $vm
  if($BootRepair){
   & (Join-Path $PSScriptRoot 'p01_boot_repair_iso.ps1') -Directory $work
   $repairIso=Join-Path $work 'boot-repair.iso'
   Add-VMDvdDrive -VM $vm -Path $repairIso | Out-Null
   $setupDvd=Get-VMDvdDrive -VM $vm | Where-Object Path -eq $iso
   Set-VMFirmware -VM $vm -FirstBootDevice $setupDvd
  }else{Set-VMFirmware -VM $vm -FirstBootDevice $hardDrive}
  Start-VM -VM $vm
  $result.status='windows-first-boot-started'
  $result.edition='Windows 11 Home'
  $result.standardAccount='P01Tester'
  $result.noNetwork=$true
 } else {
  $result.vmState=$vm.State.ToString()
  if($vm.State -ne 'Off'){throw 'Guest must shut down before collecting evidence'}
  $mounted=Mount-VHD -Path $vhd -ReadOnly -PassThru
  $mountedDisk=$true
  $windows=Get-Partition -DiskNumber $mounted.DiskNumber | Where-Object {$_.Type -eq 'Basic'} | Select-Object -First 1
  if(!$windows.DriveLetter){$windows | Add-PartitionAccessPath -AssignDriveLetter; $windows=Get-Partition -DiskNumber $mounted.DiskNumber -PartitionNumber $windows.PartitionNumber}
  $source="$($windows.DriveLetter):\P01\results"
  $evidence=Join-Path $work 'evidence'
  New-Item -ItemType Directory -Path $evidence -Force | Out-Null
  foreach($file in 'runner.json','acceptance.json','foundation.json'){
   if(Test-Path "$source\$file"){Copy-Item -LiteralPath "$source\$file" -Destination (Join-Path $evidence $file) -Force}
  }
  if(!(Test-Path (Join-Path $evidence 'runner.json'))){throw 'No guest runner result'}
  $result.runner=Get-Content (Join-Path $evidence 'runner.json') -Raw | ConvertFrom-Json
  $result.status='collected'
 }
}catch{$result.error=$_.Exception.Message;$result.errorLine=$_.InvocationInfo.ScriptLineNumber}
finally{
 if($mountedDisk){Dismount-VHD -Path $vhd}
 if($mountedIso){Dismount-DiskImage -ImagePath $iso | Out-Null}
 $result | ConvertTo-Json -Depth 6 | Set-Content (Join-Path $work "$Mode-result.json") -Encoding utf8
}
if($result.status -eq 'failed'){exit 1}
