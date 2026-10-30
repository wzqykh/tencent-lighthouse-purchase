# Monitor current 15:00 inventory and attempt signed do-goods on returned stock.
$ErrorActionPreference = 'Stop'
$appHome = Join-Path $env:LOCALAPPDATA 'TencentPurchaseRunner'
$scriptPath = Join-Path $appHome 'run.ps1'
if (-not (Test-Path $scriptPath)) {
    throw 'Installed runner not found. Run install.ps1 first.'
}
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $scriptPath -Mode restock
exit $LASTEXITCODE
