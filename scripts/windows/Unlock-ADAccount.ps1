# ITOps: Unlock an on-prem AD account (document ITOps-ADUnlockAccount).
# Runs on a management server/DC registered with tag ITOps:Role=ADAdmin that has the RSAT ActiveDirectory module.
# The server's computer account (SYSTEM) must be delegated "unlock account" on the user OUs - nothing more.
$SamAccountName = '{{ SamAccountName }}'
$ErrorActionPreference = 'Stop'
Import-Module ActiveDirectory

$user = Get-ADUser -Identity $SamAccountName -Properties LockedOut, AdminCount, Enabled, LastBadPasswordAttempt
if ($user.AdminCount -eq 1) {
    Write-Output "Refusing: $SamAccountName is a protected (privileged) account. Handle manually."
    exit 4
}
if (-not $user.Enabled) { Write-Output "$SamAccountName is DISABLED - unlocking will not help. Escalate."; exit 5 }
if (-not $user.LockedOut) { Write-Output "$SamAccountName is not locked out (last bad password: $($user.LastBadPasswordAttempt))."; exit 0 }

Unlock-ADAccount -Identity $user
Write-Output "Unlocked $SamAccountName (last bad password attempt: $($user.LastBadPasswordAttempt))."
exit 0
