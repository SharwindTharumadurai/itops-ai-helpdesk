<#
.SYNOPSIS
  Create an SSM hybrid activation (run on an IT admin workstation with the AWS CLI configured).
.DESCRIPTION
  Tags on the activation are copied onto every managed node that registers with it, so every
  device automatically gets ITOps:Managed=true (required by the Lambda's IAM policy).
  Use -Role ADAdmin for the management server that runs the AD scripts.
.EXAMPLE
  .\new-activation.ps1 -Region ap-southeast-1 -Limit 25
  .\new-activation.ps1 -Region ap-southeast-1 -Limit 1 -Role ADAdmin -Days 1
#>
param(
    [Parameter(Mandatory)] [string]$Region,
    [ValidateRange(1, 1000)] [int]$Limit = 10,
    [ValidateRange(1, 30)]   [int]$Days = 7,
    [ValidateSet('Endpoint', 'ADAdmin')] [string]$Role = 'Endpoint',
    [string]$RoleName = "ITOpsHybridEndpointRole-$Region"
)
$ErrorActionPreference = 'Stop'

$tags = @('Key=ITOps:Managed,Value=true', "Key=ITOps:Role,Value=$Role")
$expiry = (Get-Date).ToUniversalTime().AddDays($Days).ToString('yyyy-MM-ddTHH:mm:ssZ')

$json = aws ssm create-activation `
    --region $Region `
    --iam-role $RoleName `
    --registration-limit $Limit `
    --expiration-date $expiry `
    --default-instance-name "itops-$($Role.ToLower())" `
    --description "ITOps $Role activation created by $env:USERNAME" `
    --tags $tags `
    --output json
if ($LASTEXITCODE -ne 0) { throw 'create-activation failed' }
$a = $json | ConvertFrom-Json

Write-Output ''
Write-Output "ActivationId   : $($a.ActivationId)"
Write-Output "ActivationCode : $($a.ActivationCode)"
Write-Output "Expires        : $expiry   Registrations allowed: $Limit"
Write-Output ''
Write-Output 'Treat the code like a password: anyone holding it can enrol a machine into your fleet.'
Write-Output 'Delete it when onboarding is finished:  aws ssm delete-activation --activation-id <id>'
