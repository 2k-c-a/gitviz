# Development-only end to end check of the signature path with a throwaway self-signed certificate.
# The certificate lives in Cert:\CurrentUser\My only for this run; no trust store is modified.
$ErrorActionPreference = 'Stop'
$verify = Join-Path $PSScriptRoot 'install-update.ps1'
$work = Join-Path ([IO.Path]::GetTempPath()) ('GitVizSelfSigned-' + [guid]::NewGuid().ToString())
New-Item -ItemType Directory -Path $work | Out-Null
$cert = New-SelfSignedCertificate -Type CodeSigningCert -Subject 'CN=GitViz Test Signing (self-signed)' -CertStoreLocation Cert:\CurrentUser\My -NotAfter (Get-Date).AddDays(7)
$failures = @()
function Expect($name, $expected, $arguments) {
    & pwsh -NoProfile -File $verify @arguments | Out-Null
    if ($LASTEXITCODE -ne $expected) { $script:failures += "$name expected $expected got $LASTEXITCODE" }
    "{0}: exit {1}" -f $name, $LASTEXITCODE
}
try {
    $installer = Join-Path $work 'GitVizSetup.exe'
    Copy-Item (Join-Path $env:WINDIR 'System32\whoami.exe') $installer
    $stream = [IO.File]::Open($installer, 'Append'); $stream.Write([byte[]](0,0,0,0,0,0,0,0), 0, 8); $stream.Close()   # break the OS catalog match
    Set-AuthenticodeSignature -FilePath $installer -Certificate $cert -HashAlgorithm SHA256 | Out-Null
    $manifest = Join-Path $work 'manifest.json'
    @{ version = '0.8.0'; assetName = 'GitVizSetup.exe'; sha256 = (Get-FileHash $installer -Algorithm SHA256).Hash.ToLowerInvariant(); size = (Get-Item $installer).Length; url = 'x' } | ConvertTo-Json | Set-Content $manifest
    $tampered = Join-Path $work 'tampered.exe'
    $bytes = [IO.File]::ReadAllBytes($installer); $bytes[200] = $bytes[200] -bxor 1; [IO.File]::WriteAllBytes($tampered, $bytes)
    $tamperedManifest = Join-Path $work 'tampered.json'
    @{ version = '0.8.0'; assetName = 'GitVizSetup.exe'; sha256 = (Get-FileHash $tampered -Algorithm SHA256).Hash.ToLowerInvariant(); size = (Get-Item $tampered).Length; url = 'x' } | ConvertTo-Json | Set-Content $tamperedManifest
    Expect 'pinned-ok' 0 @('-InstallerPath', $installer, '-ManifestPath', $manifest, '-PinnedThumbprint', $cert.Thumbprint)
    Expect 'wrong-thumbprint' 1 @('-InstallerPath', $installer, '-ManifestPath', $manifest, '-PinnedThumbprint', ('0' * 40))
    Expect 'tampered-file-with-matching-manifest' 1 @('-InstallerPath', $tampered, '-ManifestPath', $tamperedManifest, '-PinnedThumbprint', $cert.Thumbprint)
    Expect 'untrusted-root-as-public-publisher' 1 @('-InstallerPath', $installer, '-ManifestPath', $manifest, '-ExpectedPublisher', $cert.Subject)
    Expect 'no-publisher' 2 @('-InstallerPath', $installer, '-ManifestPath', $manifest)
} finally {
    Remove-Item -LiteralPath "Cert:\CurrentUser\My\$($cert.Thumbprint)" -Force
    Remove-Item -LiteralPath $work -Recurse -Force
}
if (Test-Path "Cert:\CurrentUser\My\$($cert.Thumbprint)") { $failures += 'certificate was not removed' }
if ($failures) { $failures | ForEach-Object { Write-Error $_ }; exit 1 }
'self-signed update path: all checks passed; certificate removed'
