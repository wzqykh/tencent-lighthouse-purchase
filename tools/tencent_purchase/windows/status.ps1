# Show local task, state and latest evidence locations without exposing credentials.
$ErrorActionPreference = 'Stop'
$appHome = Join-Path $env:LOCALAPPDATA 'TencentPurchaseRunner'
$statePath = Join-Path $appHome ('state\' + (Get-Date -Format 'yyyy-MM-dd') + '.json')
$evidenceDir = Join-Path $appHome 'evidence'

Get-ScheduledTask -TaskName 'Tencent-Lighthouse-Purchase' -ErrorAction SilentlyContinue |
    Select-Object TaskName, State
Get-ScheduledTaskInfo -TaskName 'Tencent-Lighthouse-Purchase' -ErrorAction SilentlyContinue |
    Select-Object LastRunTime, LastTaskResult, NextRunTime
if (Test-Path $statePath) {
    Get-Content -LiteralPath $statePath -Raw -Encoding utf8
} else {
    Write-Host 'No purchase state exists for today.'
}
if (Test-Path $evidenceDir) {
    Get-ChildItem -LiteralPath $evidenceDir |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 5 Name, Length, LastWriteTime
}
