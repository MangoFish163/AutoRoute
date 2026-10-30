$ErrorActionPreference = 'Stop'
$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$backendDir = Join-Path $projectDir 'backend'
$frontendDir = Join-Path $projectDir 'frontend'
$venvPython = Join-Path $backendDir '.venv\Scripts\python.exe'
$viteEntry = Join-Path $frontendDir 'node_modules\vite\bin\vite.js'

if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) {
    throw 'Rust/Cargo is required for the native desktop window. Install Rust using the official rustup installer, reopen PowerShell, then run this script again.'
}
if (-not (Test-Path $venvPython)) {
    throw 'Backend environment is missing. Run .\start.ps1 once to create it, then run .\stop.ps1 before opening the desktop app.'
}
if (-not (Test-Path (Join-Path $frontendDir 'node_modules\@tauri-apps\cli'))) {
    Push-Location $frontendDir
    try { npm install; if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' } }
    finally { Pop-Location }
}

Push-Location $frontendDir
$overridePath = Join-Path $env:TEMP ("local-model-router-tauri-{0}.json" -f [guid]::NewGuid().ToString('N'))
$frontendPort = $null
try {
    foreach ($candidatePort in 5175..5200) {
        $probe = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $candidatePort)
        try {
            $probe.Start()
            $frontendPort = $candidatePort
            break
        }
        catch { }
        finally { $probe.Stop() }
    }
    if (-not $frontendPort) { throw 'No free desktop frontend port was found between 5175 and 5200.' }

    $override = @{
        build = @{
            devUrl = "http://127.0.0.1:$frontendPort"
            beforeDevCommand = "npm run dev -- --port $frontendPort --strictPort"
        }
    } | ConvertTo-Json -Depth 5 -Compress
    [System.IO.File]::WriteAllText($overridePath, $override, [System.Text.UTF8Encoding]::new($false))

    npm run tauri -- dev --config $overridePath
    if ($LASTEXITCODE -ne 0) { throw 'Tauri desktop session ended with an error.' }
}
finally {
    if ($frontendPort) {
        Get-CimInstance Win32_Process -Filter "Name = 'node.exe'" |
            Where-Object { $_.CommandLine -like "*$viteEntry*--port $frontendPort*" } |
            ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    }
    Remove-Item -LiteralPath $overridePath -Force -ErrorAction SilentlyContinue
    Pop-Location
}
