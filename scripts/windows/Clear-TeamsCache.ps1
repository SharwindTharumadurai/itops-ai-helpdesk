# ITOps: Clear Microsoft Teams (new + classic) caches for every local profile (document ITOps-ClearTeamsCache).
# Runs as SYSTEM. Closes Teams first; chats and files live in the cloud and are NOT affected.
$ErrorActionPreference = 'Continue'

$procs = Get-Process -Name 'ms-teams', 'Teams' -ErrorAction SilentlyContinue
if ($procs) { $procs | Stop-Process -Force; Start-Sleep -Seconds 3; Write-Output "Closed $(@($procs).Count) Teams process(es)." }

$classicDirs = 'Cache', 'blob_storage', 'databases', 'GPUCache', 'IndexedDB', 'Local Storage', 'tmp', 'Code Cache'
$cleared = 0
foreach ($userDir in Get-ChildItem -LiteralPath "$env:SystemDrive\Users" -Directory -Force -ErrorAction SilentlyContinue) {
    if ($userDir.Attributes -band [IO.FileAttributes]::ReparsePoint) { continue }
    $targets = @(Join-Path $userDir.FullName 'AppData\Local\Packages\MSTeams_8wekyb3d8bbwe\LocalCache\Microsoft\MSTeams')
    foreach ($d in $classicDirs) { $targets += Join-Path $userDir.FullName "AppData\Roaming\Microsoft\Teams\$d" }
    foreach ($t in $targets) {
        if (Test-Path -LiteralPath $t) {
            Remove-Item -LiteralPath $t -Recurse -Force -ErrorAction SilentlyContinue
            Write-Output "Cleared $t"
            $cleared++
        }
    }
}
if ($cleared -eq 0) { Write-Output 'No Teams cache folders found on this device.' }
Write-Output 'Ask the user to reopen Teams and sign in again.'
exit 0
