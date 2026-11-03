param(
    [string]$KeyDirectory = (Join-Path $env:USERPROFILE '.tauri/autoroute-updater')
)
$ErrorActionPreference = 'Stop'
$projectDir = Split-Path -Parent $PSScriptRoot
$keyPath = Join-Path $KeyDirectory 'updater.key'
$passwordPath = Join-Path $KeyDirectory 'password.txt'
if (-not (Test-Path -LiteralPath $keyPath)) {
    New-Item -ItemType Directory -Force -Path $KeyDirectory | Out-Null
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    & icacls.exe $KeyDirectory /inheritance:r /grant:r "${identity}:(OI)(CI)F" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Could not protect the updater key directory.' }
    $password = [Convert]::ToBase64String([Security.Cryptography.RandomNumberGenerator]::GetBytes(32))
    [IO.File]::WriteAllText($passwordPath, $password)
    Push-Location (Join-Path $projectDir 'frontend')
    try {
        # The CLI prints private-key instructions; keep them out of terminal logs.
        & npm run tauri -- signer generate --ci --password $password --write-keys $keyPath *> $null
        if ($LASTEXITCODE -ne 0) { throw 'Could not generate the updater signing key.' }
    } finally { Pop-Location }
}
if (-not (Test-Path -LiteralPath "$keyPath.pub") -or -not (Test-Path -LiteralPath $passwordPath)) {
    throw 'Existing key is incomplete. Restore its public key and password; do not replace a published key.'
}
$publicPath = Join-Path $projectDir 'frontend/src-tauri/updater-public-key.txt'
$publicKey = [IO.File]::ReadAllText("$keyPath.pub").Trim()
if ((Test-Path -LiteralPath $publicPath) -and [IO.File]::ReadAllText($publicPath).Trim() -ne $publicKey) {
    throw 'The repository uses a different updater key. Restore that private key instead of rotating it.'
}
[IO.File]::WriteAllText($publicPath, $publicKey + "`n", [Text.UTF8Encoding]::new($false))
Write-Host "Updater key is stored in $KeyDirectory. Back up this directory securely."
Write-Host 'Set repository Actions secrets TAURI_SIGNING_PRIVATE_KEY and TAURI_SIGNING_PRIVATE_KEY_PASSWORD from updater.key and password.txt. Never commit these two files.'
