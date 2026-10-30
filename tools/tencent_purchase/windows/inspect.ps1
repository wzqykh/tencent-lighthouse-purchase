# Read the dedicated profile's current login and target-card state without purchase actions.
$ErrorActionPreference = 'Stop'
$appHome = Join-Path $env:LOCALAPPDATA 'TencentPurchaseRunner'
$scriptPath = Join-Path $appHome 'run.ps1'
if (-not (Test-Path $scriptPath)) {
    throw 'Installed runner not found. Run install.ps1 first.'
}
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $scriptPath -Mode inspect
exit $LASTEXITCODE
