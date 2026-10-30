# Install the standalone runner into the current Windows user's local app data.
$ErrorActionPreference = 'Stop'
$sourceRoot = Resolve-Path (Join-Path $PSScriptRoot '..')
$appHome = Join-Path $env:LOCALAPPDATA 'TencentPurchaseRunner'
$appRoot = Join-Path $appHome 'app'
$packageRoot = Join-Path $appRoot 'tencent_purchase'
$venvRoot = Join-Path $appHome '.venv'
$python = Join-Path $venvRoot 'Scripts\python.exe'

New-Item -ItemType Directory -Force -Path $appHome, $appRoot, $packageRoot | Out-Null
Get-ChildItem -Path $sourceRoot -Filter '*.py' -File |
    Copy-Item -Destination $packageRoot -Force
Copy-Item -Path (Join-Path $sourceRoot 'requirements.txt') -Destination $appRoot -Force
Copy-Item -Path (Join-Path $PSScriptRoot '*.ps1') -Destination $appHome -Force

if (-not (Test-Path $python)) {
    $systemPython = (Get-Command python.exe -ErrorAction Stop).Source
    & $systemPython -m venv $venvRoot
}

& $python -m pip install --disable-pip-version-check --upgrade pip
& $python -m pip install --disable-pip-version-check -r (Join-Path $appRoot 'requirements.txt')

Write-Host "Installed runner at: $appHome"
Write-Host "Next: run configure-secret.ps1, setup-login.ps1, notify-test.ps1, then install-task.ps1"
