# ITOps: Flush DNS cache and re-register this host in DNS.
# Runs as SYSTEM via SSM Run Command (document ITOps-FlushDns). Exit 0 = resolution works afterwards.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$before = @(Get-DnsClientCache -ErrorAction SilentlyContinue).Count
Clear-DnsClientCache
& ipconfig.exe /registerdns | Out-Null
Write-Output "DNS cache cleared ($before entries removed); DNS re-registration requested."

Write-Output "Configured DNS servers:"
Get-DnsClientServerAddress -AddressFamily IPv4 |
    Where-Object { $_.ServerAddresses } |
    ForEach-Object { Write-Output ("  {0}: {1}" -f $_.InterfaceAlias, ($_.ServerAddresses -join ', ')) }

$failed = 0
foreach ($name in 'www.microsoft.com', 'login.microsoftonline.com', 'outlook.office365.com') {
    try {
        $ip = (Resolve-DnsName -Name $name -Type A -DnsOnly -QuickTimeout -ErrorAction Stop |
               Where-Object { $_.IPAddress } | Select-Object -First 1).IPAddress
        Write-Output ("OK    {0} -> {1}" -f $name, $ip)
    } catch {
        Write-Output ("FAIL  {0}: {1}" -f $name, $_.Exception.Message)
        $failed++
    }
}
if ($failed -gt 0) { Write-Output "$failed lookup(s) still failing - likely a network or DNS server problem."; exit 2 }
exit 0
