param(
    [string]$Python = 'python',
    [switch]$SkipDependencyInstall,
    [switch]$TestInstaller,
    [switch]$RequireSigning,
    [string]$CertificateThumbprint = $env:LMR_CERTIFICATE_THUMBPRINT,
    [string]$TimestampUrl = $env:LMR_TIMESTAMP_URL
)
$ErrorActionPreference = 'Stop'
$projectDir = Split-Path -Parent $PSScriptRoot
$signingConfigPath = $null
if ($RequireSigning -and -not $CertificateThumbprint) { throw 'Signed release requires LMR_CERTIFICATE_THUMBPRINT and LMR_TIMESTAMP_URL. Unsigned builds are for testing only.' }
if ($CertificateThumbprint -and ($CertificateThumbprint -notmatch '^[0-9a-fA-F]{40}$' -or $TimestampUrl -notmatch '^https?://')) { throw 'Invalid signing certificate thumbprint or timestamp URL.' }
Push-Location $projectDir
try {
    if (-not [Environment]::Is64BitOperatingSystem) { throw 'Windows x64 is required.' }
    & $Python -c "import sys,struct; assert sys.version_info[:2] == (3,12) and struct.calcsize('P') == 8, 'Use Python 3.12 x64'"
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 x64 is required on the build machine.' }
    $buildPython = Join-Path $projectDir 'build\venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $buildPython)) {
        & $Python -m venv build/venv
        if ($LASTEXITCODE -ne 0) { throw 'Failed to create build environment.' }
    }
    if (-not $SkipDependencyInstall) {
        & $buildPython -m pip install -r packaging/requirements-windows.lock
        if ($LASTEXITCODE -ne 0) { throw 'Backend dependencies failed to install.' }
        npm --prefix frontend ci
        if ($LASTEXITCODE -ne 0) { throw 'Frontend dependencies failed to install.' }
    }
    $version = (Get-Content frontend/package.json -Raw | ConvertFrom-Json).version
    $tauriVersion = (Get-Content frontend/src-tauri/tauri.conf.json -Raw | ConvertFrom-Json).version
    $cargoText = Get-Content frontend/src-tauri/Cargo.toml -Raw
    if ($version -ne $tauriVersion -or $cargoText -notmatch ('(?m)^version = "' + [regex]::Escape($version) + '"')) {
        throw 'Frontend, Tauri and Cargo versions must match.'
    }
    Push-Location backend
    try { & $buildPython -m unittest discover -p 'test_*.py'; if ($LASTEXITCODE -ne 0) { throw 'Backend tests failed.' } }
    finally { Pop-Location }
    & $buildPython -m PyInstaller --noconfirm --distpath build/dist --workpath build/pyinstaller packaging/backend.spec
    if ($LASTEXITCODE -ne 0) { throw 'Backend freezing failed.' }
    if ($CertificateThumbprint) {
        & "$PSScriptRoot/sign-windows.ps1" -File build/dist/backend/router-backend.exe -CertificateThumbprint $CertificateThumbprint -TimestampUrl $TimestampUrl
        # Tauri signs the desktop EXE, the NSIS uninstaller via !uninstfinalize,
        # and the final setup EXE using the same certificate settings.
        $signingConfigPath = Join-Path $projectDir 'build/tauri.signing.json'
        @{ bundle = @{ windows = @{ certificateThumbprint = $CertificateThumbprint; digestAlgorithm = 'sha256'; timestampUrl = $TimestampUrl; tsp = $true } } } |
            ConvertTo-Json -Depth 6 | Set-Content -Encoding utf8 $signingConfigPath
    }
    & $buildPython packaging/smoke_backend.py build/dist/backend/router-backend.exe
    if ($LASTEXITCODE -ne 0) { throw 'Frozen backend smoke failed.' }
    Push-Location frontend
    try {
        $tauriArguments = @('build', '--config', 'src-tauri/tauri.release.json')
        if ($signingConfigPath) { $tauriArguments += @('--config', $signingConfigPath) }
        $tauriArguments += @('--bundles', 'nsis', '--', '--locked')
        npm run tauri -- @tauriArguments
        if ($LASTEXITCODE -ne 0) { throw 'Windows installer build failed.' }
    }
    finally { Pop-Location }
    $bundleRoot = if ($env:CARGO_TARGET_DIR) { $env:CARGO_TARGET_DIR } else { Join-Path $projectDir 'frontend/src-tauri/target' }
    $installers = @(Get-ChildItem -LiteralPath (Join-Path $bundleRoot 'release/bundle/nsis') -Filter '*-setup.exe')
    if ($installers.Count -ne 1) { throw 'Expected one NSIS installer.' }
    if ($CertificateThumbprint) {
        foreach ($signedFile in @($installers[0].FullName, (Join-Path $bundleRoot 'release/local-model-router.exe'))) {
            $signature = Get-AuthenticodeSignature -LiteralPath $signedFile
            if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Thumbprint -ne $CertificateThumbprint -or -not $signature.TimeStamperCertificate) { throw "Invalid release signature: $signedFile" }
        }
    }
    New-Item -ItemType Directory -Force release | Out-Null
    $output = Join-Path $projectDir "release/LocalModelRouter-$version-windows-x64-setup.exe"
    Copy-Item -LiteralPath $installers[0].FullName -Destination $output
    $hash = (Get-FileHash -LiteralPath $output -Algorithm SHA256).Hash.ToLowerInvariant()
    "$hash  $([IO.Path]::GetFileName($output))" | Set-Content -Encoding ascii release/SHA256SUMS.txt
    if ($TestInstaller) {
        $makensis = Join-Path $env:LOCALAPPDATA 'tauri/NSIS/makensis.exe'
        & $buildPython packaging/test_installer_paths.py $makensis
        if ($LASTEXITCODE -ne 0) { throw 'Installer path regression failed.' }
        $smokeArguments = @('packaging/smoke_installer.py', $output)
        if ($CertificateThumbprint) { $smokeArguments += $CertificateThumbprint }
        & $buildPython @smokeArguments
        if ($LASTEXITCODE -ne 0) { throw 'Installer smoke failed.' }
    }
    Write-Host "Installer ready: $output"
}
finally {
    if ($signingConfigPath) { Remove-Item -LiteralPath $signingConfigPath -ErrorAction SilentlyContinue }
    Pop-Location
}
