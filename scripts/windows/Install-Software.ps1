# ITOps: Silent install of an APPROVED package (document ITOps-InstallSoftware). Runs as SYSTEM.
# The Software parameter is restricted by SSM allowedValues; the map below turns it into package IDs.
$Software = '{{ Software }}'
$ErrorActionPreference = 'Stop'

$packages = @{
    'chrome'          = @{ winget = 'Google.Chrome';                choco = 'googlechrome' }
    'firefox'         = @{ winget = 'Mozilla.Firefox';              choco = 'firefox' }
    '7zip'            = @{ winget = '7zip.7zip';                    choco = '7zip' }
    'vlc'             = @{ winget = 'VideoLAN.VLC';                 choco = 'vlc' }
    'notepadplusplus' = @{ winget = 'Notepad++.Notepad++';          choco = 'notepadplusplus' }
    'vscode'          = @{ winget = 'Microsoft.VisualStudioCode';   choco = 'vscode' }
    'zoom'            = @{ winget = 'Zoom.Zoom';                    choco = 'zoom' }
    'adobereader'     = @{ winget = 'Adobe.Acrobat.Reader.64-bit';  choco = 'adobereader' }
}
$pkg = $packages[$Software]
if (-not $pkg) { Write-Output "Unknown package '$Software'"; exit 3 }

# winget is not on SYSTEM's PATH; locate the newest App Installer build.
$winget = Resolve-Path "$env:ProgramFiles\WindowsApps\Microsoft.DesktopAppInstaller_*__8wekyb3d8bbwe\winget.exe" -ErrorAction SilentlyContinue |
    Sort-Object { [version](($_.Path -split '_')[1]) } -Descending | Select-Object -First 1 -ExpandProperty Path

if ($winget) {
    Write-Output "Using winget: $winget"
    & $winget list --id $pkg.winget --exact --source winget --accept-source-agreements | Out-Null
    if ($LASTEXITCODE -eq 0) { Write-Output "$($pkg.winget) is already installed."; exit 0 }
    & $winget install --id $pkg.winget --exact --source winget --scope machine --silent `
        --accept-package-agreements --accept-source-agreements --disable-interactivity
    $code = $LASTEXITCODE
    if ($code -eq 0) { Write-Output "Installed $($pkg.winget)."; exit 0 }
    Write-Output "winget failed with exit code $code; trying Chocolatey if present."
}

$choco = Join-Path $env:ProgramData 'chocolatey\bin\choco.exe'
if (Test-Path $choco) {
    & $choco install $pkg.choco -y --no-progress --limit-output
    if ($LASTEXITCODE -in 0, 1641, 3010) { Write-Output "Installed $($pkg.choco) via Chocolatey."; exit 0 }
    Write-Output "Chocolatey failed with exit code $LASTEXITCODE"; exit 1
}
Write-Output 'Neither winget (App Installer) nor Chocolatey is available on this device.'
exit 3
