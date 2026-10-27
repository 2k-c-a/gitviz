[CmdletBinding()]
param([ValidateSet('Prepare','Collect')][string]$Mode='Prepare',[switch]$Elevated)
$ErrorActionPreference='Stop'
$repo=Split-Path $PSScriptRoot -Parent
$work=Join-Path $repo '.validation\p01-vm'
$vmId=[guid]'25a434e8-1335-47ad-a6c1-cdec28192507'
$diskPath=Join-Path $work 'physical-full.vhdx'
$admin=([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if(!$admin){
 if($Elevated){throw 'Administrator token required'}
 $arguments='-NoProfile -ExecutionPolicy Bypass -File "{0}" -Mode {1} -Elevated' -f $PSCommandPath,$Mode
 $process=Start-Process "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -ArgumentList $arguments -Verb RunAs -WindowStyle Hidden -Wait -PassThru
 exit $process.ExitCode
}
$result=@{mode=$Mode;status='failed'}
$session=$null
try{
 Import-Module Hyper-V
 $vm=Get-VM -Id $vmId
 if($vm.Name -ne 'GitViz-P01-Clean-Win11' -or $vm.State -ne 'Running'){throw 'Expected running P01 VM'}
 $credential=Import-Clixml (Join-Path $work 'credentials\admin.xml')
 $session=New-PSSession -VMId $vmId -Credential $credential
 if($Mode -eq 'Prepare'){
  if(Test-Path -LiteralPath $diskPath){throw 'Preserving existing full-disk fixture; inspect before retrying'}
  New-VHD -Path $diskPath -SizeBytes 64MB -Dynamic | Out-Null
  Add-VMHardDiskDrive -VM $vm -Path $diskPath
  $exe=Join-Path $repo '.validation\full-disk-dist\p01-full-disk.exe'
  $hash=(Get-FileHash -LiteralPath $exe).Hash
  Copy-Item -LiteralPath $exe -Destination 'C:\P01\p01-full-disk.exe' -ToSession $session
  $result.guest=Invoke-Command -Session $session -ArgumentList $hash -ScriptBlock {
   param($ExpectedHash)
   $ErrorActionPreference='Stop'
   if($env:COMPUTERNAME -ne 'GITVIZ-P01'){throw 'Unexpected guest'}
   if((Get-FileHash 'C:\P01\p01-full-disk.exe').Hash -ne $ExpectedHash){throw 'Fixture transfer hash mismatch'}
   if(Get-Volume -DriveLetter F -ErrorAction SilentlyContinue){throw 'Drive F is already occupied'}
   Update-HostStorageCache
   $disks=@(Get-Disk | Where-Object {$_.Size -eq 64MB -and $_.PartitionStyle -eq 'RAW' -and !$_.IsBoot -and !$_.IsSystem})
   if($disks.Count -ne 1){throw 'Expected exactly one new RAW 64MiB guest disk'}
   $disk=$disks[0]
   $disk | Initialize-Disk -PartitionStyle GPT
   $partition=New-Partition -DiskNumber $disk.Number -UseMaximumSize -DriveLetter F
   $partition | Format-Volume -FileSystem NTFS -NewFileSystemLabel P01FULL -Confirm:$false | Out-Null
   $tester=Get-LocalUser P01Tester
   & icacls.exe 'F:\' /grant "*$($tester.SID.Value):(OI)(CI)M" *> $null
   if($LASTEXITCODE -ne 0){throw 'Fixture ACL failed'}
   [IO.File]::WriteAllText('F:\P01-DISPOSABLE.tag','GitViz-P01-physical-full-v1',[Text.UTF8Encoding]::new($false))
   $principal=New-ScheduledTaskPrincipal -UserId P01Tester -LogonType Interactive -RunLevel Limited
   $action=New-ScheduledTaskAction -Execute 'C:\P01\p01-full-disk.exe' -Argument 'F:\ C:\P01\results\physical-full.json'
   Register-ScheduledTask -TaskName GitVizP01PhysicalFull -Action $action -Principal $principal -Force | Out-Null
   Start-ScheduledTask -TaskName GitVizP01PhysicalFull
   @{diskNumber=$disk.Number;diskUniqueId=$disk.UniqueId;fixtureSha256=$ExpectedHash;volumeLabel='P01FULL';taskStarted=$true} | ConvertTo-Json -Compress
  }
  $result.status='physical-full-test-started'
 }else{
  $payload=Invoke-Command -Session $session -ScriptBlock {
   $ErrorActionPreference='Stop'
   $path='C:\P01\results\physical-full.json'
   if(!(Test-Path -LiteralPath $path)){throw 'Report not ready'}
   $json=[IO.File]::ReadAllText($path)
   $report=$json | ConvertFrom-Json
   if($report.status -eq 'passed'){Unregister-ScheduledTask -TaskName GitVizP01PhysicalFull -Confirm:$false}
   $json
  }
  [IO.File]::WriteAllText((Join-Path $work 'evidence\physical-full.json'),$payload,[Text.UTF8Encoding]::new($false))
  $result.acceptance=$payload | ConvertFrom-Json
  $result.status='collected'
  if($result.acceptance.status -eq 'passed'){
   $attached=@(Get-VMHardDiskDrive -VM $vm | Where-Object Path -eq $diskPath)
   if($attached.Count -ne 1){throw 'Unexpected fixture disk attachment'}
   $attached[0] | Remove-VMHardDiskDrive
   $result.fixtureDetached=$true
  }
 }
}catch{$result.error=$_.Exception.Message;$result.line=$_.InvocationInfo.ScriptLineNumber}
finally{
 if($session){Remove-PSSession $session}
 $result | ConvertTo-Json -Depth 7 | Set-Content (Join-Path $work "physical-full-$Mode.json") -Encoding utf8
}
if($result.status -eq 'failed'){exit 1}
