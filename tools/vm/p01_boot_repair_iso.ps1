param([Parameter(Mandatory=$true)][string]$Directory,[string]$SourceIsoRoot)
$ErrorActionPreference='Stop'
$stage=Join-Path $Directory 'boot-repair-source'
New-Item -ItemType Directory -Path $stage -Force | Out-Null
@'
<?xml version="1.0" encoding="utf-8"?>
<unattend xmlns="urn:schemas-microsoft-com:unattend" xmlns:wcm="http://schemas.microsoft.com/WMIConfig/2002/State">
<settings pass="windowsPE"><component name="Microsoft-Windows-Setup" processorArchitecture="amd64" publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS"><RunSynchronous><RunSynchronousCommand wcm:action="add"><Order>1</Order><Path>cmd.exe /c for %d in (C D E F G H I J K L M N O P Q R S T U V W X Y Z) do if exist %d:\p01-repair.cmd call %d:\p01-repair.cmd</Path><Description>Prepare P01 guest boot files</Description></RunSynchronousCommand></RunSynchronous></component></settings>
</unattend>
'@ | Set-Content (Join-Path $stage 'autounattend.xml') -Encoding utf8
@'
@echo off
diskpart /s %~dp0partitions.txt > X:\p01-diskpart.log 2>&1
if not exist W:\P01\run-clean-vm.ps1 exit /b 12
bcdboot W:\Windows /s S: /f UEFI /c /v > W:\P01\results\boot-repair.log 2>&1
if errorlevel 1 exit /b 13
echo passed>W:\P01\results\boot-repair-status.txt
wpeutil reboot
'@ | Set-Content (Join-Path $stage 'p01-repair.cmd') -Encoding ascii
@'
select disk 0
select partition 2
assign letter=S
select partition 4
assign letter=W
exit
'@ | Set-Content (Join-Path $stage 'partitions.txt') -Encoding ascii
$builder=New-Object -ComObject IMAPI2FS.MsftFileSystemImage
$builder.FileSystemsToCreate=4
$builder.FreeMediaBlocks=2147483647
$builder.VolumeName='P01REPAIR'
if($SourceIsoRoot){
 $builder.Root.AddTree($SourceIsoRoot,$false)
 $boot=New-Object -ComObject IMAPI2FS.BootOptions
 $boot.PlatformId=0xEF
 $boot.Emulation=0
 $stream=New-Object -ComObject ADODB.Stream
 $stream.Type=1
 $stream.Open()
 $stream.LoadFromFile((Join-Path $SourceIsoRoot 'efi\microsoft\boot\efisys_noprompt.bin'))
 $boot.AssignBootImage($stream)
 $builder.BootImageOptions=$boot
}
$builder.Root.AddTree($stage,$false)
$image=$builder.CreateResultImage()
Add-Type -TypeDefinition @'
using System;
using System.IO;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;
public static class P01IsoWriter {
 public static void Write(object source,string target) {
  var stream=(IStream)source; var buffer=new byte[65536]; var count=Marshal.AllocHGlobal(4);
  try { using(var output=new FileStream(target,FileMode.CreateNew,FileAccess.Write)) {
   while(true){stream.Read(buffer,buffer.Length,count); int read=Marshal.ReadInt32(count); if(read==0)break; output.Write(buffer,0,read);}
  }} finally {Marshal.FreeHGlobal(count);}
 }
}
'@
$name=if($SourceIsoRoot){'boot-repair-full.iso'}else{'boot-repair.iso'}
[P01IsoWriter]::Write($image.ImageStream,(Join-Path $Directory $name))
