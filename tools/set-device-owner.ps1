<#
.SYNOPSIS
  Assign a registered device (mi-...) to a user and refresh the ITOps inventory.
.DESCRIPTION
  The ITOps:Owner tag is what lets a non-admin user run chatbot fixes on "their" laptop.
  Only IT admins with ssm:AddTagsToResource can set it - the device itself cannot.
.EXAMPLE
  .\set-device-owner.ps1 -Region ap-southeast-1 -InstanceId mi-0123456789abcdef0 -Owner jane.doe@contoso.com
#>
param(
    [Parameter(Mandatory)] [string]$Region,
    [Parameter(Mandatory)] [ValidatePattern('^mi-[0-9a-f]{17}$')] [string]$InstanceId,
    [Parameter(Mandatory)] [ValidatePattern('^[^@\s]+@[^@\s]+\.[^@\s]+$')] [string]$Owner,
    [string]$StackName = 'ItOpsAi'
)
$ErrorActionPreference = 'Stop'

aws ssm add-tags-to-resource --region $Region --resource-type ManagedInstance `
    --resource-id $InstanceId --tags "Key=ITOps:Owner,Value=$($Owner.ToLower())"
if ($LASTEXITCODE -ne 0) { throw 'Tagging failed' }

$fn = aws cloudformation describe-stacks --region $Region --stack-name $StackName `
    --query "Stacks[0].Outputs[?OutputKey=='EventsFunctionName'].OutputValue" --output text
$payload = Join-Path $env:TEMP 'itops-sync.json'
'{"action":"sync_inventory"}' | Set-Content -Path $payload -Encoding ascii
aws lambda invoke --region $Region --function-name $fn --cli-binary-format raw-in-base64-out `
    --payload "fileb://$payload" (Join-Path $env:TEMP 'itops-sync-out.json') | Out-Null
Get-Content (Join-Path $env:TEMP 'itops-sync-out.json')
Write-Output "$InstanceId assigned to $Owner and inventory refreshed."
