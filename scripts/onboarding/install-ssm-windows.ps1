#Requires -RunAsAdministrator
<#
.SYNOPSIS
  Install the AWS SSM Agent on a Windows laptop/server and register it with a hybrid activation.
.EXAMPLE
  .\install-ssm-windows.ps1 -ActivationCode 'abc...' -ActivationId '1234-...' -Region 'ap-southeast-1'
.NOTES
  Outbound HTTPS (443) only: ssm.<region>.amazonaws.com, ssmmessages.<region>.amazonaws.com,
  ec2messages.<region>.amazonaws.com and amazon-ssm-<region>.s3.<region>.amazonaws.com.
  No inbound ports, no VPN. The agent runs as LocalSystem.
#>
param(
    [Parameter(Mandatory)] [ValidatePattern('^[A-Za-z0-9+/=]{20,}$')] [string]$ActivationCode,
    [Parameter(Mandatory)] [ValidatePattern('^[0-9a-f-]{36}$')]         [string]$ActivationId,
    [Parameter(Mandatory)] [ValidatePattern('^[a-z]{2}(-gov)?-[a-z]+-\d$')] [string]$Region
)
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

if (Get-Service -Name AmazonSSMAgent -ErrorAction SilentlyContinue) {
    $reg = Join-Path $env:ProgramData 'Amazon\SSM\InstanceData\registration'
    if (Test-Path $reg) { Write-Output "Already registered: $(Get-Content $reg)"; exit 0 }
}

$arch = if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { 'windows_arm64' } else { 'windows_amd64' }
$dir = Join-Path $env:TEMP 'ssm'
New-Item -ItemType Directory -Path $dir -Force | Out-Null
$installer = Join-Path $dir 'AmazonSSMAgentSetup.exe'
$url = "https://amazon-ssm-$Region.s3.$Region.amazonaws.com/latest/$arch/AmazonSSMAgentSetup.exe"

Write-Output "Downloading $url"
Invoke-WebRequest -Uri $url -OutFile $installer -UseBasicParsing

# Only run an installer that Amazon signed.
$sig = Get-AuthenticodeSignature $installer
if ($sig.Status -ne 'Valid' -or $sig.SignerCertificate.Subject -notmatch 'O=Amazon') {
    Remove-Item $installer -Force
    throw "Installer signature check failed: $($sig.Status) $($sig.SignerCertificate.Subject)"
}

Start-Process -FilePath $installer -Wait -ArgumentList @(
    '/q', '/log', (Join-Path $dir 'install.log'), "CODE=$ActivationCode", "ID=$ActivationId", "REGION=$Region")

Start-Sleep -Seconds 10
Get-Content (Join-Path $env:ProgramData 'Amazon\SSM\InstanceData\registration')
Get-Service -Name AmazonSSMAgent | Format-Table Name, Status, StartType
Remove-Item $installer -Force
Write-Output 'Done. Ask IT to tag this device with its owner (tools/set-device-owner.ps1).'
