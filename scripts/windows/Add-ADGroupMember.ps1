# ITOps: Add a user to an APPROVED on-prem AD group (document ITOps-ADAddGroupMember).
# GroupName is restricted by SSM allowedValues. Runs on the ITOps:Role=ADAdmin server as SYSTEM;
# delegate "write members" on ONLY the approved groups to that computer account.
$SamAccountName = '{{ SamAccountName }}'
$GroupName = '{{ GroupName }}'
$ErrorActionPreference = 'Stop'
Import-Module ActiveDirectory

$group = Get-ADGroup -Identity $GroupName -Properties AdminCount
if ($group.AdminCount -eq 1) { Write-Output "Refusing: $GroupName is a protected (privileged) group."; exit 4 }
$user = Get-ADUser -Identity $SamAccountName -Properties AdminCount, Enabled
if (-not $user.Enabled) { Write-Output "Refusing: $SamAccountName is disabled."; exit 5 }

$already = Get-ADGroupMember -Identity $group | Where-Object { $_.distinguishedName -eq $user.DistinguishedName }
if ($already) { Write-Output "$SamAccountName is already a member of $GroupName."; exit 0 }

Add-ADGroupMember -Identity $group -Members $user
Write-Output "Added $SamAccountName to $GroupName. Membership applies at next sign-in / Kerberos ticket refresh."
exit 0
