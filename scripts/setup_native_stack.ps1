<#
Downloads portable Windows binaries for Prometheus, Grafana, and Garnet into tools/
(gitignored) and generates the small config files start.ps1 needs to launch them.
Idempotent: re-running skips any binary that's already extracted.
#>

$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Root  = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Tools = Join-Path $Root "tools"
New-Item -ItemType Directory -Force -Path $Tools | Out-Null

function Get-GithubLatestAssetUrl {
    param([string]$Repo, [string]$Pattern)
    $release = Invoke-RestMethod -Uri "https://api.github.com/repos/$Repo/releases/latest" -Headers @{ "User-Agent" = "excelsis-setup-script" }
    $asset = $release.assets | Where-Object { $_.name -like $Pattern } | Select-Object -First 1
    if (-not $asset) {
        throw "No asset matching '$Pattern' found in latest release of $Repo. Check https://github.com/$Repo/releases manually."
    }
    return $asset.browser_download_url
}

function Install-PortableZip {
    param([string]$Name, [string]$ZipUrl)
    $target = Join-Path $Tools $Name
    if (Test-Path $target) {
        Write-Host "$Name already installed at $target, skipping."
        return
    }
    $zipPath = Join-Path $env:TEMP "$Name-download.zip"
    $extractDir = Join-Path $env:TEMP "$Name-extract"
    Write-Host "Downloading $Name from $ZipUrl ..."
    Invoke-WebRequest -Uri $ZipUrl -OutFile $zipPath
    if (Test-Path $extractDir) { Remove-Item -Recurse -Force $extractDir }
    Expand-Archive -Path $zipPath -DestinationPath $extractDir -Force
    Remove-Item -Force $zipPath

    $children = Get-ChildItem -Path $extractDir
    if ($children.Count -eq 1 -and $children[0].PSIsContainer) {
        Move-Item -Path $children[0].FullName -Destination $target
    } else {
        Move-Item -Path $extractDir -Destination $target
    }
    Remove-Item -Recurse -Force $extractDir -ErrorAction SilentlyContinue
    Write-Host "$Name installed to $target"
}

Write-Host "--- Prometheus ---"
$promUrl = Get-GithubLatestAssetUrl -Repo "prometheus/prometheus" -Pattern "*.windows-amd64.zip"
Install-PortableZip -Name "prometheus" -ZipUrl $promUrl

Write-Host "--- Grafana ---"
$grafanaVersion = (Invoke-RestMethod -Uri "https://grafana.com/api/grafana/versions/stable").version
$grafanaUrl = "https://dl.grafana.com/oss/release/grafana-$grafanaVersion.windows-amd64.zip"
Install-PortableZip -Name "grafana" -ZipUrl $grafanaUrl

Write-Host "--- Garnet ---"
$garnetUrl = Get-GithubLatestAssetUrl -Repo "microsoft/garnet" -Pattern "*win-x64*.zip"
Install-PortableZip -Name "garnet" -ZipUrl $garnetUrl

$garnetExe = Get-ChildItem -Path (Join-Path $Tools "garnet") -Filter "*.exe" -Recurse |
    Where-Object { $_.Name -like "*Garnet*" } | Select-Object -First 1
if (-not $garnetExe) {
    throw "Could not find a Garnet .exe under tools/garnet after extraction. Check the release layout at https://github.com/microsoft/garnet/releases."
}
Write-Host "Garnet executable: $($garnetExe.FullName)"

Write-Host "--- Writing native Prometheus config ---"
@"
global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  - job_name: excelsis-fastapi
    static_configs:
      - targets:
          - localhost:8000
    metrics_path: /metrics
"@ | Set-Content -Path (Join-Path $Tools "prometheus.yml") -Encoding utf8

Write-Host "--- Writing native Grafana provisioning ---"
$provDir = Join-Path $Tools "grafana-provisioning"
New-Item -ItemType Directory -Force -Path (Join-Path $provDir "datasources") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $provDir "dashboards") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $provDir "alerting") | Out-Null

Copy-Item -Path (Join-Path $Root "docker\grafana\provisioning\datasources\datasources.yaml") `
    -Destination (Join-Path $provDir "datasources\datasources.yaml") -Force

$dashboardsPath = Join-Path $Root "docker\grafana\dashboards"
@"
apiVersion: 1

providers:
  - name: excelsis
    orgId: 1
    type: file
    disableDeletion: false
    updateIntervalSeconds: 30
    allowUiUpdates: true
    options:
      path: $dashboardsPath
"@ | Set-Content -Path (Join-Path $provDir "dashboards\dashboards.yaml") -Encoding utf8

Write-Host ""
Write-Host "Native stack ready:"
Write-Host "  Prometheus -> $Tools\prometheus\prometheus.exe"
Write-Host "  Grafana    -> $Tools\grafana\bin\grafana.exe server"
Write-Host "  Garnet     -> $($garnetExe.FullName)"
Write-Host ""
Write-Host "Run start.ps1 to launch everything."
