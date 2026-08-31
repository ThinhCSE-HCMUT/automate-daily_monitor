# Copy this repo to the Raspberry Pi, skipping Windows junk (.venv, output, binaries).
# Does send sharepoint.conf, fax.conf, portal.conf, monitor.conf, token_cache.bin.
#
# From the project folder:
#   powershell -File deploy/sync-to-pi.ps1
# From Downloads (or any copy):
#   powershell -File deploy/sync-to-pi.ps1 -Source C:\Users\Test\Downloads\automate-daily_monitor

param(
    [string]$Source = "",
    [string]$Pi = "pi@192.168.1.129",
    [string]$Dest = "/home/pi/Workspace/Daily_Monitor"
)

$ErrorActionPreference = "Stop"
if (-not $Source) {
    $Source = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
}

$stageRoot = Join-Path $env:TEMP "simplifi-pi-sync"
$stage = Join-Path $stageRoot "automate-daily_monitor"
if (Test-Path $stageRoot) {
    Remove-Item $stageRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $stage | Out-Null

Write-Host "Staging $Source  (skip .venv, output, monitor, .xlsx, ...)"
& robocopy $Source $stage /E `
    /XD .venv venv output __pycache__ .cursor .git `
    /XF monitor *.o *.xlsx *.pyc `
    /NFL /NDL /NJH /NJS /NP | Out-Null
$rc = $LASTEXITCODE
if ($rc -ge 8) {
    throw "robocopy failed (exit $rc)"
}

Write-Host "scp -> ${Pi}:${Dest}/"
scp -r $stage "${Pi}:${Dest}/"
Write-Host "Done. On the Pi:"
Write-Host "  cd $Dest/automate-daily_monitor && make && make deps"
