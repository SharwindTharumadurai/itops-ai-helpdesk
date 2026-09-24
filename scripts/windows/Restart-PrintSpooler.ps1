# ITOps: Clear stuck print jobs and restart the spooler (document ITOps-RestartPrinting). Runs as SYSTEM.
$ErrorActionPreference = 'Stop'

$jobs = @(Get-Printer -ErrorAction SilentlyContinue | ForEach-Object { Get-PrintJob -PrinterName $_.Name -ErrorAction SilentlyContinue })
Write-Output "Jobs in queue before reset: $($jobs.Count)"

Stop-Service -Name Spooler -Force
$spool = Join-Path $env:SystemRoot 'System32\spool\PRINTERS'
$files = @(Get-ChildItem -LiteralPath $spool -File -Force -ErrorAction SilentlyContinue)
$files | Remove-Item -Force -ErrorAction SilentlyContinue
Write-Output "Removed $($files.Count) spool file(s)."
Start-Service -Name Spooler

$svc = Get-Service -Name Spooler
Write-Output "Spooler status: $($svc.Status)"
Get-Printer | ForEach-Object { Write-Output ("Printer: {0} [{1}] port {2}" -f $_.Name, $_.PrinterStatus, $_.PortName) }
if ($svc.Status -ne 'Running') { exit 1 }
exit 0
