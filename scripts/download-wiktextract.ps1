[CmdletBinding()]
param(
    [string]$Destination,
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not $Destination) {
    $Destination = Join-Path $projectRoot "data\raw\wiktextract\kaikki.org-dictionary-Thai.jsonl"
}
$parent = Split-Path -Parent $Destination
New-Item -ItemType Directory -Force -Path $parent | Out-Null

$url = "https://kaikki.org/dictionary/Thai/kaikki.org-dictionary-Thai.jsonl"
$expectedSha256 = "413F5F257987B25CFFBFC45EACAB4DBADE2C34603F267D77E5546D789DDD4167"

if ($Force -or -not (Test-Path -LiteralPath $Destination -PathType Leaf)) {
    Write-Host "Downloading the pinned Thai Wiktextract JSONL snapshot ..."
    & curl.exe --fail --location --silent --show-error --output $Destination $url
    if ($LASTEXITCODE -ne 0) {
        throw "Could not download $url. curl exit code: $LASTEXITCODE"
    }
}

$actualHash = (Get-FileHash -LiteralPath $Destination -Algorithm SHA256).Hash
if ($actualHash -ne $expectedSha256) {
    throw "SHA256 mismatch. Kaikki's latest URL is mutable; audit the new extraction before updating the pinned hash. Expected $expectedSha256, found $actualHash."
}

Write-Host "Wiktextract snapshot verified: $Destination"
