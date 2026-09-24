# ITOps: Read-only health snapshot (document ITOps-CollectDiagnostics). Runs as SYSTEM. Changes nothing.
$ErrorActionPreference = 'SilentlyContinue'
$ProgressPreference = 'SilentlyContinue'

function Section($t) { Write-Output ''; Write-Output "== $t ==" }

$os = Get-CimInstance Win32_OperatingSystem
$cs = Get-CimInstance Win32_ComputerSystem
Section 'System'
Write-Output ("{0} | {1} {2} (build {3})" -f $env:COMPUTERNAME, $os.Caption, $os.OSArchitecture, $os.BuildNumber)
Write-Output ("Model: {0} {1} | RAM: {2:N1} GB | Uptime: {3:N1} days" -f $cs.Manufacturer, $cs.Model,
    ($cs.TotalPhysicalMemory / 1GB), ((Get-Date) - $os.LastBootUpTime).TotalDays)
Write-Output ("Memory in use: {0:N0}%" -f (100 - ($os.FreePhysicalMemory / $os.TotalVisibleMemorySize * 100)))
$cpu = (Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average).Average
Write-Output "CPU load: $cpu%"

Section 'Disks'
Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3' | ForEach-Object {
    $pct = if ($_.Size) { $_.FreeSpace / $_.Size * 100 } else { 0 }
    $flag = if ($pct -lt 10) { '  <-- LOW' } else { '' }
    Write-Output ("{0} {1:N1} GB free of {2:N1} GB ({3:N0}%){4}" -f $_.DeviceID, ($_.FreeSpace / 1GB), ($_.Size / 1GB), $pct, $flag)
}

Section 'Top processes by CPU time'
Get-Process | Sort-Object CPU -Descending | Select-Object -First 5 | ForEach-Object {
    Write-Output ("{0,-30} CPU {1,8:N0}s  RAM {2,6:N0} MB" -f $_.ProcessName, $_.CPU, ($_.WorkingSet64 / 1MB))
}

Section 'Network'
Get-NetIPConfiguration | Where-Object { $_.IPv4Address } | ForEach-Object {
    Write-Output ("{0}: {1} gw {2} dns {3}" -f $_.InterfaceAlias, $_.IPv4Address.IPAddress,
        $_.IPv4DefaultGateway.NextHop, (($_.DNSServer | Where-Object AddressFamily -eq 2 | ForEach-Object { $_.ServerAddresses }) -join ','))
}
foreach ($target in 'login.microsoftonline.com', 'outlook.office365.com') {
    $t = Test-NetConnection -ComputerName $target -Port 443 -WarningAction SilentlyContinue
    Write-Output ("HTTPS {0}: {1}" -f $target, $(if ($t.TcpTestSucceeded) { 'reachable' } else { 'UNREACHABLE' }))
}

Section 'Security & updates'
$mp = Get-MpComputerStatus
if ($mp) {
    Write-Output ("Defender: realtime={0} signatures={1} (age {2} days)" -f $mp.RealTimeProtectionEnabled,
        $mp.AntivirusSignatureVersion, $mp.AntivirusSignatureAge)
}
$pending = (Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending') -or
           (Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired')
Write-Output "Reboot pending: $pending"
$hotfix = Get-HotFix | Sort-Object InstalledOn -Descending | Select-Object -First 1
Write-Output ("Last update installed: {0} on {1}" -f $hotfix.HotFixID, $hotfix.InstalledOn)

Section 'Critical/Error system events (last 24h, top 10)'
Get-WinEvent -FilterHashtable @{ LogName = 'System'; Level = 1, 2; StartTime = (Get-Date).AddDays(-1) } -MaxEvents 10 |
    ForEach-Object { Write-Output ("{0:yyyy-MM-dd HH:mm} [{1}] {2}: {3}" -f $_.TimeCreated, $_.Id, $_.ProviderName,
        (($_.Message -split "`n")[0]).Trim()) }
exit 0
