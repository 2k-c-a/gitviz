# Installed only in the disposable P01 guest. Invoked at standard-user logon.
$ErrorActionPreference='Stop'
$results='C:\P01\results'
if(Test-Path "$results\runner.json"){exit}
$record=@{status='failed';startedAt=(Get-Date).ToString('o')}
try{
 $admin=([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
 if($admin){throw 'Refusing administrator test run'}
 $record.account=([Security.Principal.WindowsIdentity]::GetCurrent()).Name
 $record.standardUser=$true
 $record.output=@(& 'C:\P01\clean-vm.ps1' -PackageDirectory 'C:\P01\Package' -AttestCleanVM)
 $evidence=@(Get-ChildItem -LiteralPath $env:TEMP -Directory -Filter 'GitVizP01Acceptance-*' | Sort-Object LastWriteTime -Descending | Select-Object -First 1)
 if($evidence.Count -ne 1){throw 'Missing acceptance evidence directory'}
 Copy-Item -LiteralPath (Join-Path $evidence[0].FullName 'acceptance.json') -Destination "$results\acceptance.json"
 Copy-Item -LiteralPath (Join-Path $evidence[0].FullName 'foundation.json') -Destination "$results\foundation.json"
 $record.status='passed'
}catch{$record.error=$_.Exception.Message}
finally{
 $record.finishedAt=(Get-Date).ToString('o')
 $record | ConvertTo-Json -Depth 4 | Set-Content "$results\runner.json" -Encoding utf8
}
# Leave the guest running so operators can inspect it and collect evidence live.
