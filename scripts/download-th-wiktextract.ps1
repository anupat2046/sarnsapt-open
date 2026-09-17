[CmdletBinding()]
param(
    [string]$OutputPath
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not $OutputPath) {
    $OutputPath = Join-Path $projectRoot "data\raw\wiktextract\thwiktionary-raw.jsonl.gz"
}
$url = "https://kaikki.org/thwiktionary/raw-wiktextract-data.jsonl.gz"
$outputDirectory = Split-Path -Parent $OutputPath
New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null

Write-Host "Downloading the Thai Wiktionary Wiktextract snapshot..."
Invoke-WebRequest -Uri $url -OutFile $OutputPath -UseBasicParsing
$hash = (Get-FileHash -LiteralPath $OutputPath -Algorithm SHA256).Hash.ToLowerInvariant()
$bytes = (Get-Item -LiteralPath $OutputPath).Length
Write-Host "Saved: $OutputPath"
Write-Host "Bytes: $bytes"
Write-Host "SHA256: $hash"
