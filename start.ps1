$Root   = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = "$Root\.venv\Scripts\python.exe"
$Tools  = "$Root\tools"

Write-Host "Starting FastAPI on :8000 ..."
$api = Start-Process -NoNewWindow -PassThru -FilePath $Python `
    -ArgumentList "-m uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload" `
    -WorkingDirectory $Root

Write-Host "Starting React dev server on :5173 ..."
$web = Start-Process -NoNewWindow -PassThru -FilePath "cmd" `
    -ArgumentList "/c npm run dev" `
    -WorkingDirectory "$Root\web"

$processes = @($api, $web)

if (-not (Test-Path $Tools)) {
    Write-Host ""
    Write-Host "tools/ not found -- run scripts\setup_native_stack.ps1 first to enable Prometheus/Grafana/Garnet."
} else {
    Write-Host "Starting Prometheus on :9090 ..."
    $prometheus = Start-Process -NoNewWindow -PassThru -FilePath "$Tools\prometheus\prometheus.exe" `
        -ArgumentList "--config.file=$Tools\prometheus.yml" `
        -WorkingDirectory "$Tools\prometheus"
    $processes += $prometheus

    Write-Host "Starting Grafana on :3000 ..."
    $env:GF_PATHS_PROVISIONING = "$Tools\grafana-provisioning"
    $grafana = Start-Process -NoNewWindow -PassThru -FilePath "$Tools\grafana\bin\grafana.exe" `
        -ArgumentList "server" `
        -WorkingDirectory "$Tools\grafana"
    $processes += $grafana

    $garnetExe = Get-ChildItem -Path "$Tools\garnet" -Filter "*.exe" -Recurse |
        Where-Object { $_.Name -like "*Garnet*" } | Select-Object -First 1
    Write-Host "Starting Garnet on :6379 ..."
    # --lua: python-limits' Redis storage backend (used by slowapi) requires EVALSHA support,
    # which Garnet disables by default.
    $garnet = Start-Process -NoNewWindow -PassThru -FilePath $garnetExe.FullName `
        -ArgumentList "--port 6379 --lua" `
        -WorkingDirectory $garnetExe.DirectoryName
    $processes += $garnet
}

Write-Host ""
Write-Host "  Backend    -> http://localhost:8000"
Write-Host "  Frontend   -> http://localhost:5173"
if (Test-Path $Tools) {
    Write-Host "  Prometheus -> http://localhost:9090"
    Write-Host "  Grafana    -> http://localhost:3000"
    Write-Host "  Garnet     -> localhost:6379"
}
Write-Host ""
Write-Host "Press Ctrl-C to stop all servers."

$ids = $processes | ForEach-Object { $_.Id }
try { Wait-Process -Id $ids }
finally { Stop-Process -Id $ids -ErrorAction SilentlyContinue }
