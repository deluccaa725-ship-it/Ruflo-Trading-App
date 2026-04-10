$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$taskName = "Ruflo Daily 0800"
$runner = Join-Path $scriptDir "run_ruflo_daily.ps1"

if (-not (Test-Path $runner)) {
    throw "Could not find $runner"
}

$tr = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$runner`""
schtasks /Delete /TN $taskName /F | Out-Null
schtasks /Create /TN $taskName /SC DAILY /ST 08:00 /F /TR $tr | Out-Null

Write-Host "Installed scheduled task: $taskName"
Write-Host "It will run daily at 08:00 AM local time and Ruflo will email its report if Gmail env vars are set."
