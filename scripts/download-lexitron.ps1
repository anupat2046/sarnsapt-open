[CmdletBinding()]
param(
    [string]$OutputPath,
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$downloadUrl = "https://opend-portal.nectec.or.th/dataset/bdd85296-9398-499f-b3a7-aab85042d3f9/resource/6238e10d-3970-47d9-84a4-6b96494ddde7/download/telex.csv"

if (-not $OutputPath) {
    $OutputPath = Join-Path $projectRoot "data\raw\lexitron\telex.csv"
}
if ((Test-Path -LiteralPath $OutputPath) -and -not $Force) {
    throw "Output already exists: $OutputPath. Use -Force to replace it."
}

$outputDirectory = Split-Path -Parent $OutputPath
New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null

Write-Host "Downloading the official NECTEC LEXiTRON telex.csv resource ..."
& curl.exe --fail --location --silent --show-error $downloadUrl --output $OutputPath
if ($LASTEXITCODE -ne 0) {
    throw "LEXiTRON download failed with exit code $LASTEXITCODE"
}

$file = Get-Item -LiteralPath $OutputPath
$hash = Get-FileHash -Algorithm SHA256 -LiteralPath $OutputPath
Write-Host "Saved $($file.Length) bytes to $OutputPath"
Write-Host "SHA256: $($hash.Hash.ToLowerInvariant())"

