$runtimeDir = Join-Path $env:LOCALAPPDATA 'LocalModelRouter'
$pidFile = Join-Path $runtimeDir 'processes.json'
if (-not (Test-Path $pidFile)) {
    Write-Host 'No saved Local Model Router process IDs were found.'
    exit 0
}
$processes = Get-Content -Raw -LiteralPath $pidFile | ConvertFrom-Json
$allProcesses = Get-CimInstance Win32_Process
function Get-ProcessTreeIds([int]$RootId, $ProcessList) {
    $ids = @()
    $children = $ProcessList | Where-Object { $_.ParentProcessId -eq $RootId }
    foreach ($child in $children) { $ids += Get-ProcessTreeIds -RootId $child.ProcessId -ProcessList $ProcessList }
    $ids += $RootId
    return $ids
}
foreach ($id in @($processes.backend, $processes.frontend)) {
    if (-not $id) { continue }
    $root = $allProcesses | Where-Object { $_.ProcessId -eq $id } | Select-Object -First 1
    if (-not $root) { continue }
    $owned = ($root.CommandLine -match 'uvicorn app:app.*--port 8787') -or ($root.CommandLine -match 'node_modules\\vite\\bin\\vite\.js')
    if (-not $owned) {
        Write-Warning "Skipping PID $id because it no longer matches a Local Model Router process."
        continue
    }
    foreach ($targetId in (Get-ProcessTreeIds -RootId $id -ProcessList $allProcesses)) {
        Stop-Process -Id $targetId -Force -ErrorAction SilentlyContinue
    }
}
Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
Write-Host 'Local Model Router services stopped.'
