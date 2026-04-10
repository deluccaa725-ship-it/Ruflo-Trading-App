param(
    [switch]$Headless
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectsRoot = Split-Path -Parent $root
$controlPython = Join-Path $projectsRoot "The Claude Portfolio\.venv\Scripts\python.exe"
$pythonExe = if (Test-Path $controlPython) { $controlPython } else { "python" }
$port = 8011
$dashboardUrl = "http://localhost:$port/compare.html"

if (-not $Headless) {
    Start-Process -FilePath $pythonExe -ArgumentList "-m", "http.server", $port -WorkingDirectory $projectsRoot | Out-Null
    Start-Sleep -Seconds 2
    Start-Process $dashboardUrl
}

Set-Location $root
& $pythonExe main.py

Set-Location $projectsRoot
& $pythonExe compare_metrics.py
