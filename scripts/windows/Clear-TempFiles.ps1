# ITOps: Delete temp files older than N days (document ITOps-ClearTemp). Runs as SYSTEM.
# Only touches well-known temp/cache folders, never follows junctions or symlinks.
$AgeDays = [int]'{{ AgeDays }}'
$ErrorActionPreference = 'Continue'
$cutoff = (Get-Date).AddDays(-$AgeDays)
$script:freed = 0; $script:deleted = 0; $script:skipped = 0

function Remove-OldFiles([string]$Root) {
    if (-not (Test-Path -LiteralPath $Root)) { return }
    $stack = New-Object System.Collections.Stack
    $stack.Push($Root)
    while ($stack.Count -gt 0) {
        $dir = $stack.Pop()
        foreach ($item in Get-ChildItem -LiteralPath $dir -Force -ErrorAction SilentlyContinue) {
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { continue }
            if ($item.PSIsContainer) { $stack.Push($item.FullName); continue }
            if ($item.LastWriteTime -ge $cutoff) { continue }
            try {
                $size = $item.Length
                Remove-Item -LiteralPath $item.FullName -Force -ErrorAction Stop
                $script:freed += $size; $script:deleted++
            } catch { $script:skipped++ }   # locked / in use - fine
        }
    }
}

$drive = Get-PSDrive -Name ($env:SystemDrive.TrimEnd(':'))
$freeBefore = $drive.Free

$roots = @("$env:SystemRoot\Temp")
foreach ($userDir in Get-ChildItem -LiteralPath "$env:SystemDrive\Users" -Directory -Force -ErrorAction SilentlyContinue) {
    if ($userDir.Attributes -band [IO.FileAttributes]::ReparsePoint) { continue }
    $roots += Join-Path $userDir.FullName 'AppData\Local\Temp'
    $roots += Join-Path $userDir.FullName 'AppData\Local\Microsoft\Windows\INetCache'
    $roots += Join-Path $userDir.FullName 'AppData\Local\CrashDumps'
}
foreach ($r in $roots) { Remove-OldFiles $r }

$drive = Get-PSDrive -Name ($env:SystemDrive.TrimEnd(':'))
Write-Output ("Deleted {0} files older than {1} days ({2:N1} MB); {3} skipped (in use)." -f `
    $script:deleted, $AgeDays, ($script:freed / 1MB), $script:skipped)
Write-Output ("{0} free space: {1:N1} GB -> {2:N1} GB" -f $env:SystemDrive, ($freeBefore / 1GB), ($drive.Free / 1GB))
exit 0
