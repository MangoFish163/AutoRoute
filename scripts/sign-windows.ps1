param(
    [Parameter(Mandatory)][string]$File,
    [string]$CertificateThumbprint = $env:LMR_CERTIFICATE_THUMBPRINT,
    [string]$TimestampUrl = $env:LMR_TIMESTAMP_URL
)
$ErrorActionPreference = 'Stop'
if ($CertificateThumbprint -notmatch '^[0-9a-fA-F]{40}$') { throw 'A code-signing certificate thumbprint is required.' }
if ($TimestampUrl -notmatch '^https?://') { throw 'Provide the certificate issuer RFC3161 timestamp URL.' }
$certificate = Get-Item -LiteralPath "Cert:\CurrentUser\My\$CertificateThumbprint" -ErrorAction Stop
if (-not $certificate.HasPrivateKey -or $certificate.NotAfter -le (Get-Date)) { throw 'A valid certificate with an accessible private key is required.' }
$codeSigningOid = '1.3.6.1.5.5.7.3.3'
if ($codeSigningOid -notin @($certificate.EnhancedKeyUsageList | ForEach-Object { $_.ObjectId.Value })) { throw 'The certificate is not enabled for code signing.' }
$signTool = (Get-Command signtool.exe -ErrorAction SilentlyContinue).Source
if (-not $signTool) {
    $sdkBin = Join-Path ${env:ProgramFiles(x86)} 'Windows Kits\10\bin'
    $signTool = Get-ChildItem -LiteralPath $sdkBin -Directory -ErrorAction Stop |
        Sort-Object Name -Descending |
        ForEach-Object { Join-Path $_.FullName 'x64\signtool.exe' } |
        Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}
if (-not $signTool) { throw 'Windows SDK signtool.exe is required.' }
$target = (Resolve-Path -LiteralPath $File).Path
& $signTool sign /sha1 $CertificateThumbprint /fd SHA256 /tr $TimestampUrl /td SHA256 $target
if ($LASTEXITCODE -ne 0) { throw "Signing failed: $target" }
$signature = Get-AuthenticodeSignature -LiteralPath $target
if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Thumbprint -ne $CertificateThumbprint -or -not $signature.TimeStamperCertificate) {
    throw "Signature or timestamp verification failed: $target"
}
