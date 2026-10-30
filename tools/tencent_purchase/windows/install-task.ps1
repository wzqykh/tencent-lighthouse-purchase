# Configure the laptop for unattended 10:00 and 15:00 sale sessions.
$ErrorActionPreference = 'Stop'
$taskName = 'Tencent-Lighthouse-Purchase'
$appHome = Join-Path $env:LOCALAPPDATA 'TencentPurchaseRunner'
$runScript = Join-Path $appHome 'run.ps1'
$secretPath = Join-Path $appHome 'pushplus.dpapi'
if (-not (Test-Path $runScript)) {
    throw 'Installed runner not found. Run install.ps1 first.'
}
if (-not (Test-Path $secretPath)) {
    throw 'PushPlus secret not found. Run configure-secret.ps1 first.'
}
$action = New-ScheduledTaskAction `
    -Execute 'powershell.exe' `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$runScript`" -Mode http-api" `
    -WorkingDirectory $appHome
$morning = New-ScheduledTaskTrigger -Daily -At '09:55'
$afternoon = New-ScheduledTaskTrigger -Daily -At '14:55'
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -WakeToRun `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
$principal = New-ScheduledTaskPrincipal `
    -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger @($morning, $afternoon) `
    -Settings $settings `
    -Principal $principal `
    -Description 'Use the authenticated HTTP/2 API path to create unpaid Tencent Lighthouse orders and stop before payment.' `
    -Force | Out-Null

# Keep the dorm computer awake on AC while allowing the display to turn off.
powercfg.exe /change monitor-timeout-ac 15
$sleepSubgroup = '238c9fa8-0aad-41ed-83f4-97be242c8f20'
$standbyIdle = '29f6c1db-86da-48c5-9fdb-f2b67b1f44da'
$hibernateIdle = '9d7815a6-7ee4-497e-8888-515a05f02364'
$lidAction = '5ca83367-6e45-459f-a27b-476b1d01c936'
$allowWakeTimers = 'bd3b718a-0680-4d9d-8ab2-e1d2b4ac806d'
$buttonSubgroup = '4f971e89-eebd-4455-a8de-9e59040e7347'
powercfg.exe /setacvalueindex SCHEME_CURRENT $sleepSubgroup $standbyIdle 0
powercfg.exe /setacvalueindex SCHEME_CURRENT $sleepSubgroup $hibernateIdle 0
powercfg.exe /setacvalueindex SCHEME_CURRENT $sleepSubgroup $allowWakeTimers 1
powercfg.exe /setacvalueindex SCHEME_CURRENT $buttonSubgroup $lidAction 0
if ($LASTEXITCODE -ne 0) {
    Write-Warning 'This device does not expose a lid-close power action; no lid setting was changed.'
}
powercfg.exe /setactive SCHEME_CURRENT

Write-Host "Registered scheduled task: $taskName"
Get-ScheduledTask -TaskName $taskName | Select-Object TaskName, State
