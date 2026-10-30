param(
    [Parameter(Mandatory=$true)][string]$InstallerPath,
    [Parameter(Mandatory=$true)][string]$ManifestPath,
    [string]$ExpectedPublisher = '',
    [string]$PinnedThumbprint = ''
)
# Verifies a downloaded installer against its manifest. It never runs the installer.
# -ExpectedPublisher: publicly trusted signature (Status Valid) with this exact subject.
# -PinnedThumbprint: self-signed development certificate; accepts an untrusted root only when the signer thumbprint matches.
$ErrorActionPreference = 'Stop'
if ($ExpectedPublisher -and $PinnedThumbprint) { throw 'Use either -ExpectedPublisher or -PinnedThumbprint.' }
$installer = (Resolve-Path -LiteralPath $InstallerPath).Path
$manifest = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
$result = [ordered]@{ asset = $manifest.assetName; version = $manifest.version; size = 'mismatch'; sha256 = 'mismatch'; signature = 'not-checked' }
if ((Get-Item -LiteralPath $installer).Length -eq [int64]$manifest.size) { $result.size = 'ok' }
if ((Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToLowerInvariant() -eq $manifest.sha256) { $result.sha256 = 'ok' }
if ($ExpectedPublisher) {
    $signature = Get-AuthenticodeSignature -LiteralPath $installer
    $result.signature = if ($signature.Status -eq 'Valid' -and $signature.SignerCertificate.Subject -eq $ExpectedPublisher) { 'ok' } else { 'mismatch' }
} elseif ($PinnedThumbprint) {
    $signature = Get-AuthenticodeSignature -LiteralPath $installer
    $pinned = $signature.SignerCertificate -and $signature.SignerCertificate.Thumbprint -eq $PinnedThumbprint.ToUpperInvariant()
    $result.signature = if ($pinned -and $signature.Status -in @('Valid', 'UnknownError')) { 'ok-pinned-self-signed' } else { 'mismatch' }
} else { $result.signature = 'unsigned-not-trusted' }
$result | ConvertTo-Json
if ($result.size -ne 'ok' -or $result.sha256 -ne 'ok' -or $result.signature -eq 'mismatch') { exit 1 }
if ($result.signature -notin @('ok', 'ok-pinned-self-signed')) { exit 2 }
