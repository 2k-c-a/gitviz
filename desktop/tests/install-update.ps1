param(
    [Parameter(Mandatory=$true)][string]$InstallerPath,
    [Parameter(Mandatory=$true)][string]$ManifestPath,
    [string]$ExpectedPublisher = ''
)
# Verifies a downloaded installer against its manifest. It never runs the installer.
$ErrorActionPreference = 'Stop'
$installer = (Resolve-Path -LiteralPath $InstallerPath).Path
$manifest = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
$result = [ordered]@{ asset = $manifest.assetName; version = $manifest.version; size = 'mismatch'; sha256 = 'mismatch'; signature = 'not-checked' }
if ((Get-Item -LiteralPath $installer).Length -eq [int64]$manifest.size) { $result.size = 'ok' }
if ((Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToLowerInvariant() -eq $manifest.sha256) { $result.sha256 = 'ok' }
if ($ExpectedPublisher) {
    $signature = Get-AuthenticodeSignature -LiteralPath $installer
    $result.signature = if ($signature.Status -eq 'Valid' -and $signature.SignerCertificate.Subject -eq $ExpectedPublisher) { 'ok' } else { 'mismatch' }
} else { $result.signature = 'unsigned-not-trusted' }
$result | ConvertTo-Json
if ($result.size -ne 'ok' -or $result.sha256 -ne 'ok' -or $result.signature -eq 'mismatch') { exit 1 }
if ($result.signature -ne 'ok') { exit 2 }
