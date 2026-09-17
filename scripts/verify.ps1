[CmdletBinding()]
param(
    [string]$BaseUrl = "http://localhost:7200",
    [string]$RepositoryId = "thailex"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$queryFile = Join-Path $projectRoot "queries\count_entries.rq"

if (-not (Test-Path -LiteralPath $queryFile)) {
    throw "Verification query not found: $queryFile"
}

$result = & curl.exe --fail --silent --show-error `
    -X POST "$BaseUrl/repositories/$RepositoryId" `
    -H "Accept: application/sparql-results+json" `
    -H "Content-Type: application/sparql-query; charset=utf-8" `
    --data-binary "@$queryFile"

if ($LASTEXITCODE -ne 0) {
    throw "SPARQL verification failed with exit code $LASTEXITCODE"
}

$parsed = $result | ConvertFrom-Json
$binding = $parsed.results.bindings[0]
$entryCount = [int]$binding.entryCount.value
$senseCount = [int]$binding.senseCount.value

Write-Host "Verification result: $entryCount lexical entries, $senseCount senses."

if ($entryCount -ne 5) {
    throw "Expected 5 lexical entries but found $entryCount."
}

if ($senseCount -ne 16) {
    throw "Expected 16 lexical senses but found $senseCount."
}

Write-Host "Phase 1 verification passed."

