# Store the PushPlus token encrypted for the current Windows user with DPAPI.
$ErrorActionPreference = 'Stop'
$appHome = Join-Path $env:LOCALAPPDATA 'TencentPurchaseRunner'
New-Item -ItemType Directory -Force -Path $appHome | Out-Null
$secretPath = Join-Path $appHome 'pushplus.dpapi'

$secure = Read-Host -AsSecureString 'PushPlus token (input is hidden)'
if ($secure.Length -eq 0) {
    throw 'PushPlus token cannot be empty.'
}
$encrypted = ConvertFrom-SecureString -SecureString $secure
Set-Content -LiteralPath $secretPath -Value $encrypted -Encoding ASCII -NoNewline

$acl = Get-Acl -LiteralPath $secretPath
$acl.SetAccessRuleProtection($true, $false)
$rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
    [System.Security.Principal.WindowsIdentity]::GetCurrent().Name,
    'FullControl',
    'Allow'
)
$acl.SetAccessRule($rule)
Set-Acl -LiteralPath $secretPath -AclObject $acl

Write-Host "Encrypted PushPlus secret stored for the current user at: $secretPath"
