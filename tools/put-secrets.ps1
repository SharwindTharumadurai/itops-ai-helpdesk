<#
.SYNOPSIS
  Store the LLM API key and (optionally) the Graph client secret as SSM SecureString parameters.
.DESCRIPTION
  Standard-tier parameters encrypted with the AWS-managed aws/ssm key cost $0
  (Secrets Manager would be $0.40/secret/month). Values are read from a hidden prompt,
  so they never land in shell history.
#>
param([Parameter(Mandatory)] [string]$Region, [switch]$IncludeGraph, [switch]$IncludeGroq, [switch]$SkipPrimary)
$ErrorActionPreference = 'Stop'

function Read-Secret($prompt) {
    $s = Read-Host -Prompt $prompt -AsSecureString
    [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($s))
}

function Put($name, $value) {
    $file = New-TemporaryFile
    try {
        # Pass the value via file:// so it never appears in the process list.
        [IO.File]::WriteAllText($file.FullName, $value)
        aws ssm put-parameter --region $Region --name $name --type SecureString --tier Standard `
            --overwrite --value "file://$($file.FullName)" | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "put-parameter $name failed" }
        Write-Output "Stored $name"
    } finally { Remove-Item $file.FullName -Force }
}

if (-not $SkipPrimary) { Put '/itops/llm/api-key' (Read-Secret 'Primary LLM API key (Gemini)') }
if ($IncludeGroq) { Put '/itops/llm/groq-api-key' (Read-Secret 'Fallback LLM API key (Groq)') }
if ($IncludeGraph) { Put '/itops/graph/client-secret' (Read-Secret 'Entra app client secret') }
