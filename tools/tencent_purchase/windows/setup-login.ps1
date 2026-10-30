# Open the dedicated automation Edge profile so the user can sign in once.
param(
    [switch]$ResetCookies
)

$ErrorActionPreference = 'Stop'
$appHome = Join-Path $env:LOCALAPPDATA 'TencentPurchaseRunner'
$scriptPath = Join-Path $appHome 'run.ps1'
if (-not (Test-Path $scriptPath)) {
    throw 'Installed runner not found. Run install.ps1 first.'
}
$commonArguments = @(
    '-NoProfile',
    '-ExecutionPolicy', 'Bypass',
    '-File', $scriptPath
)
if ($ResetCookies) {
    & powershell.exe @commonArguments -Mode reset-login-cookies
    if ($LASTEXITCODE -ne 0) {
        throw 'Could not clear Tencent Cloud login cookies.'
    }
}
& powershell.exe @commonArguments -Mode login
exit $LASTEXITCODE
