# ITOps: Reset network adapters (document ITOps-ResetNetwork). Runs as SYSTEM.
# Soft: restart physical adapters that are enabled. Full: also reset Winsock + TCP/IP (reboot required).
# The SSM connection drops briefly while adapters restart; the agent reports back once reconnected.
$Mode = '{{ Mode }}'
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$adapters = @(Get-NetAdapter -Physical | Where-Object { $_.Status -ne 'Disabled' })
if ($adapters.Count -eq 0) { Write-Output 'No enabled physical network adapters found.'; exit 3 }

foreach ($a in $adapters) {
    Write-Output ("Restarting {0} ({1}, was {2})" -f $a.Name, $a.InterfaceDescription, $a.Status)
    Restart-NetAdapter -Name $a.Name -Confirm:$false
}

& ipconfig.exe /release | Out-Null
& ipconfig.exe /renew   | Out-Null
Clear-DnsClientCache

$rebootNeeded = $false
if ($Mode -eq 'Full') {
    Write-Output 'Resetting Winsock catalog and TCP/IP stack...'
    & netsh.exe winsock reset | Out-Null
    & netsh.exe int ip reset  | Out-Null
    $rebootNeeded = $true
}

# Wait up to 60s for connectivity to come back.
$ok = $false
for ($i = 0; $i -lt 12 -and -not $ok; $i++) {
    Start-Sleep -Seconds 5
    try { $null = Resolve-DnsName 'www.microsoft.com' -DnsOnly -QuickTimeout -ErrorAction Stop; $ok = $true } catch { }
}

Get-NetIPConfiguration | Where-Object { $_.IPv4Address } | ForEach-Object {
    Write-Output ("{0}: IP {1}, gateway {2}" -f $_.InterfaceAlias, $_.IPv4Address.IPAddress, $_.IPv4DefaultGateway.NextHop)
}
if ($rebootNeeded) { Write-Output 'REBOOT REQUIRED to complete the Winsock/TCP-IP reset. Ask the user to restart.' }
if (-not $ok) { Write-Output 'Connectivity NOT restored within 60s.'; exit 2 }
Write-Output 'Connectivity restored.'
exit 0
