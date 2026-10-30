# Run the Tencent purchase task and keep all secrets out of logs and arguments.
param(
    [ValidateSet(
        'scheduled',
        'login',
        'reset-login-cookies',
        'restock',
        'inspect',
        'preflight',
        'http-preflight',
        'http-probe',
        'http-api',
        'notify-test'
    )]
    [string]$Mode = 'scheduled',
    [switch]$ResetLoginCookies
)

$ErrorActionPreference = 'Stop'
$appHome = Split-Path -Parent $MyInvocation.MyCommand.Path
$appRoot = Join-Path $appHome 'app'
$python = Join-Path $appHome '.venv\Scripts\python.exe'
$logDir = Join-Path $appHome 'logs'
$logFile = Join-Path $logDir 'task-output.log'
$secretPath = Join-Path $appHome 'pushplus.dpapi'

New-Item -ItemType Directory -Force -Path $logDir | Out-Null
if (-not (Test-Path $python)) {
    throw "Runner virtual environment not found. Run install.ps1 first."
}
if (Test-Path $secretPath) {
    $encrypted = Get-Content -LiteralPath $secretPath -Raw
    $secure = ConvertTo-SecureString -String $encrypted
    $plain = [System.Net.NetworkCredential]::new('', $secure).Password
    $env:PURCHASE_PUSHPLUS_TOKEN = $plain
    Remove-Variable plain
}

$env:TENCENT_PURCHASE_HOME = $appHome
$env:PYTHONPATH = $appRoot
$env:PYTHONUTF8 = '1'
$arguments = "-m tencent_purchase.cli $Mode"
if ($Mode -eq 'login' -and $ResetLoginCookies) {
    $arguments += ' --reset-cookies'
}
Set-Location -LiteralPath $appHome

# Keep UTF-8 bytes intact when Windows PowerShell ordinarily decodes pipes as GBK.
cmd.exe /d /c "`"$python`" $arguments >> `"$logFile`" 2>&1"
exit $LASTEXITCODE
