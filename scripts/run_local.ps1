# Test Python monitor scripts on Windows (no Raspberry Pi).
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts/run_local.ps1
#   powershell -ExecutionPolicy Bypass -File scripts/run_local.ps1 -Upload

param(
    [switch]$Upload
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "Creating .venv ..."
    py -3 -m venv .venv
}

& .\.venv\Scripts\python.exe -m pip install -q -U pip
& .\.venv\Scripts\python.exe -m pip install -q -r requirements.txt

$csv = "scripts\sample_daily_monitor.csv"
if (Test-Path "output\daily_monitor.csv") {
    $csv = "output\daily_monitor.csv"
    Write-Host "Using $csv"
} elseif (Test-Path "D:\daily_monitor_result\daily_monitor.csv") {
    $csv = "D:\daily_monitor_result\daily_monitor.csv"
    Write-Host "Using $csv"
} else {
    Write-Host "Using sample CSV $csv (add --date if the sample date is not today)"
}

$pyArgs = @(
    "scripts\sharepoint_excel.py",
    "--conf", "sharepoint.conf",
    "--csv", $csv
)
if ($csv -match "sample_daily_monitor") {
    $first = (Get-Content $csv)[1]
    $day = ($first.Split(",")[0]).Substring(0, 10)
    $pyArgs += "--date", $day
    Write-Host "Sample CSV date $day"
}
if (-not $Upload) {
    $pyArgs += "--dry-run"
    Write-Host "Dry-run: SharePoint file will NOT be overwritten"
}

$env:PYTHONUNBUFFERED = "1"
& .\.venv\Scripts\python.exe @pyArgs
exit $LASTEXITCODE
