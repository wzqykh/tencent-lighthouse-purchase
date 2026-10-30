# Remove only the scheduled task; local state and evidence are retained.
$ErrorActionPreference = 'Stop'
Unregister-ScheduledTask -TaskName 'Tencent-Lighthouse-Purchase' -Confirm:$false
Write-Host 'Scheduled purchase task removed. Local state and evidence were retained.'
