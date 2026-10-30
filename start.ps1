$ErrorActionPreference = 'Stop'
$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$backendDir = Join-Path $projectDir 'backend'
$frontendDir = Join-Path $projectDir 'frontend'
$venvDir = Join-Path $backendDir '.venv'
$venvPython = Join-Path $venvDir 'Scripts\python.exe'
$runtimeDir = Join-Path $env:LOCALAPPDATA 'LocalModelRouter'
$pidFile = Join-Path $runtimeDir 'processes.json'
$backendOut = Join-Path $runtimeDir 'backend.stdout.log'
$backendErr = Join-Path $runtimeDir 'backend.stderr.log'
$frontendOut = Join-Path $runtimeDir 'frontend.stdout.log'
$frontendErr = Join-Path $runtimeDir 'frontend.stderr.log'

New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null
$pythonCandidates = @()
$systemPython = Get-Command python.exe -ErrorAction SilentlyContinue
if ($systemPython -and $systemPython.Source -notlike '*\WindowsApps\*') {
    $pythonCandidates += $systemPython.Source
}
$bundledPython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
if (Test-Path $bundledPython) { $pythonCandidates += $bundledPython }
$pythonExe = $null
foreach ($candidate in $pythonCandidates) {
    if (Test-Path $candidate) {
        & $candidate -c 'import sys; print(sys.executable)' *> $null
        if ($LASTEXITCODE -eq 0) { $pythonExe = $candidate; break }
    }
}
if (-not $pythonExe) {
    throw 'Python 3.11 or newer was not found. Install Python from python.org, then run this script again.'
}
if (-not (Test-Path $venvPython)) {
    & $pythonExe -m venv $venvDir
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $venvPython)) {
        throw "Failed to create the backend virtual environment with $pythonExe."
    }
}
& $venvPython -c 'import sys; print(sys.version)' *> $null
if ($LASTEXITCODE -ne 0) { throw "The backend Python environment is incomplete: $venvPython" }
& $venvPython -m pip install -r (Join-Path $backendDir 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Backend dependency installation failed.' }
if (-not (Test-Path (Join-Path $frontendDir 'node_modules'))) {
    Push-Location $frontendDir
    npm install
    if ($LASTEXITCODE -ne 0) { Pop-Location; throw 'Frontend dependency installation failed.' }
    Pop-Location
}

$backendId = 0
$backendReady = $false
try {
    $health = Invoke-WebRequest -Uri 'http://127.0.0.1:8787/health' -TimeoutSec 2 -UseBasicParsing
    if ($health.StatusCode -eq 200 -and $health.Content -match '"version":"0\.1\.0"') {
        $openApi = Invoke-WebRequest -Uri 'http://127.0.0.1:8787/openapi.json' -TimeoutSec 2 -UseBasicParsing
        if ($openApi.Content -match '"/api/settings/provider-presets"' -and ($health.Content | ConvertFrom-Json).usage_schema -eq 2) {
            $backendReady = $true
            if (Test-Path $pidFile) {
                $previous = Get-Content -Raw -LiteralPath $pidFile | ConvertFrom-Json
                if ($previous.backend -and (Get-Process -Id $previous.backend -ErrorAction SilentlyContinue)) { $backendId = $previous.backend }
            }
        }
    }
} catch { }
if (-not $backendReady) {
    if (Test-Path $pidFile) {
        $previous = Get-Content -Raw -LiteralPath $pidFile | ConvertFrom-Json
        if ($previous.backend -and (Get-Process -Id $previous.backend -ErrorAction SilentlyContinue)) {
            & taskkill.exe /PID $previous.backend /T /F *> $null
            Start-Sleep -Seconds 1
        }
    }
    $backend = Start-Process -FilePath $venvPython -ArgumentList @('-m','uvicorn','app:app','--host','127.0.0.1','--port','8787') -WorkingDirectory $backendDir -WindowStyle Hidden -PassThru -RedirectStandardOutput $backendOut -RedirectStandardError $backendErr
    $backendId = $backend.Id
    for ($attempt = 0; $attempt -lt 15; $attempt++) {
        Start-Sleep -Seconds 1
        try {
            $health = Invoke-WebRequest -Uri 'http://127.0.0.1:8787/health' -TimeoutSec 2 -UseBasicParsing
            if ($health.StatusCode -eq 200 -and $health.Content -match '"version":"0\.1\.0"') { $backendReady = $true; break }
        } catch { }
        if ($backend.HasExited) { break }
    }
    if (-not $backendReady) {
        $detail = @()
        if (Test-Path $backendOut) { $detail += Get-Content -Raw -LiteralPath $backendOut }
        if (Test-Path $backendErr) { $detail += Get-Content -Raw -LiteralPath $backendErr }
        $detailsText = $detail -join [Environment]::NewLine
        throw "Backend did not become ready. Logs: $backendOut and $backendErr`n$detailsText"
    }
}
@{ backend = $backendId; frontend = 0; frontend_port = 0 } | ConvertTo-Json | Set-Content -LiteralPath $pidFile
$node = (Get-Command node.exe).Source
$viteEntry = Join-Path $frontendDir 'node_modules\vite\bin\vite.js'
$frontendPort = 5173
while (Get-NetTCPConnection -State Listen -LocalPort $frontendPort -ErrorAction SilentlyContinue) { $frontendPort++ }
$viteArguments = '"{0}" --host 127.0.0.1 --port {1} --strictPort' -f $viteEntry,$frontendPort
$frontend = Start-Process -FilePath $node -ArgumentList $viteArguments -WorkingDirectory $frontendDir -WindowStyle Hidden -PassThru -RedirectStandardOutput $frontendOut -RedirectStandardError $frontendErr
$frontendReady = $false
for ($attempt = 0; $attempt -lt 15; $attempt++) {
    Start-Sleep -Seconds 1
    try {
        $page = Invoke-WebRequest -Uri "http://127.0.0.1:$frontendPort/" -TimeoutSec 2 -UseBasicParsing
        if ($page.StatusCode -eq 200) { $frontendReady = $true; break }
    } catch { }
    if ($frontend.HasExited) { break }
}
if (-not $frontendReady) {
    $detail = @()
    if (Test-Path $frontendOut) { $detail += Get-Content -Raw -LiteralPath $frontendOut }
    if (Test-Path $frontendErr) { $detail += Get-Content -Raw -LiteralPath $frontendErr }
    Stop-Process -Id $frontend.Id -Force -ErrorAction SilentlyContinue
    $detailsText = $detail -join [Environment]::NewLine
    throw "Frontend did not become ready. Logs: $frontendOut and $frontendErr`n$detailsText"
}
@{ backend = $backendId; frontend = $frontend.Id; frontend_port = $frontendPort } | ConvertTo-Json | Set-Content -LiteralPath $pidFile
$uiUrl = "http://127.0.0.1:$frontendPort/"
Start-Process $uiUrl
Write-Host "Local Model Router UI: $uiUrl"
Write-Host 'Run .\stop.ps1 to stop the local services.'
