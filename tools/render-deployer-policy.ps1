<#
.SYNOPSIS
  Fill in the itops-deployer IAM policy template with your account ID and region.
.DESCRIPTION
  Writes infra/iam/itops-deployer-policy.json (git-ignored, because it contains your account ID).
  Apply it with:  aws iam create-policy --policy-name ITOpsDeployerPolicy --policy-document file://infra/iam/itops-deployer-policy.json
.EXAMPLE
  .\tools\render-deployer-policy.ps1 -Region ap-southeast-1
#>
param(
    [Parameter(Mandatory)] [ValidatePattern('^[a-z]{2}(-gov)?-[a-z]+-\d$')] [string]$Region,
    [ValidatePattern('^\d{12}$')] [string]$AccountId
)
$ErrorActionPreference = 'Stop'
if (-not $AccountId) {
    $AccountId = aws sts get-caller-identity --query Account --output text
    if ($LASTEXITCODE -ne 0) { throw 'Could not read the account ID; pass -AccountId' }
}
$iam = Join-Path $PSScriptRoot '..\infra\iam'
$policy = (Get-Content (Join-Path $iam 'itops-deployer-policy.template.json') -Raw).
    Replace('{{ACCOUNT_ID}}', $AccountId).Replace('{{REGION}}', $Region)
Set-Content -Path (Join-Path $iam 'itops-deployer-policy.json') -Value $policy -Encoding ascii -NoNewline
Write-Output "Wrote infra/iam/itops-deployer-policy.json for account $AccountId in $Region"
