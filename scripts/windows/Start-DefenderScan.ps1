# ITOps: Update signatures and run a Microsoft Defender scan (document ITOps-AntivirusScan). Runs as SYSTEM.
# Exit 0 = clean, 1 = active threats remain, 3 = Defender not the active AV.
$ScanType = '{{ ScanType }}'
$ErrorActionPreference = 'Stop'

$status = Get-MpComputerStatus
if (-not $status.AMServiceEnabled -or -not $status.AntivirusEnabled) {
    Write-Output 'Microsoft Defender Antivirus is not active (a third-party AV may be installed).'
    exit 3
}

Write-Output "Signatures before: $($status.AntivirusSignatureVersion) (updated $($status.AntivirusSignatureLastUpdated))"
try { Update-MpSignature -ErrorAction Stop; Write-Output 'Signature update: OK' }
catch { Write-Output "Signature update failed (continuing with current signatures): $($_.Exception.Message)" }

$started = Get-Date
Write-Output "Starting $ScanType..."
Start-MpScan -ScanType $ScanType
Write-Output ("{0} finished in {1:N0} minutes." -f $ScanType, ((Get-Date) - $started).TotalMinutes)

$detections = @(Get-MpThreatDetection | Where-Object { $_.InitialDetectionTime -ge $started.AddMinutes(-1) })
foreach ($d in $detections) {
    $threat = Get-MpThreat -ThreatID $d.ThreatID -ErrorAction SilentlyContinue
    Write-Output ("DETECTED {0} | action success: {1} | {2}" -f $threat.ThreatName, $d.ActionSuccess, ($d.Resources -join '; '))
}
$active = @(Get-MpThreat | Where-Object { $_.IsActive })
if ($active.Count -gt 0) {
    Write-Output "$($active.Count) ACTIVE threat(s) remain - escalate to security."
    exit 1
}
Write-Output ('Scan complete: {0} new detection(s), no active threats.' -f $detections.Count)
exit 0
