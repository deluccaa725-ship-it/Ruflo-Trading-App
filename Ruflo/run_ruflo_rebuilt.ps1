param(
    [switch]$Headless
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectsRoot = Split-Path -Parent $root
$pythonExe = Join-Path $projectsRoot "The Claude Portfolio\.venv_rebuilt\Scripts\python.exe"
$port = 8011
$dashboardUrl = "http://localhost:$port/compare.html"

if (-not (Test-Path $pythonExe)) {
    Write-Host "Replacement Claude Portfolio environment not found at .venv_rebuilt" -ForegroundColor Yellow
    exit 1
}

if (-not $Headless) {
    Start-Process -FilePath $pythonExe -ArgumentList "-m", "http.server", $port -WorkingDirectory $projectsRoot | Out-Null
    Start-Sleep -Seconds 2
    Start-Process $dashboardUrl
}

Set-Location $root
& $pythonExe main.py

Set-Location $projectsRoot
& $pythonExe compare_metrics.py
