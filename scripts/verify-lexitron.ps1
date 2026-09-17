[CmdletBinding()]
param(
    [string]$BaseUrl = "http://localhost:7200",
    [string]$RepositoryId = "thailex",
    [string]$ValidationReport
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$queryFile = Join-Path $projectRoot "queries\lexitron-summary.rq"
if (-not $ValidationReport) {
    $ValidationReport = Join-Path $projectRoot "reports\lexitron-validation.json"
}

foreach ($requiredFile in @($queryFile, $ValidationReport)) {
    if (-not (Test-Path -LiteralPath $requiredFile -PathType Leaf)) {
        throw "Required verification file not found: $requiredFile"
    }
}

$result = & curl.exe --fail --silent --show-error `
    -X POST "$BaseUrl/repositories/$RepositoryId" `
    -H "Accept: application/sparql-results+json" `
    -H "Content-Type: application/sparql-query; charset=utf-8" `
    --data-binary "@$queryFile"
if ($LASTEXITCODE -ne 0) {
    throw "LEXiTRON SPARQL verification failed with exit code $LASTEXITCODE"
}

$binding = ($result | ConvertFrom-Json).results.bindings[0]
$actualEntries = [int]$binding.entryCount.value
$actualSenses = [int]$binding.senseCount.value
$actualDefinitions = [int]$binding.definitionCount.value
$expected = Get-Content -LiteralPath $ValidationReport -Raw | ConvertFrom-Json
$expectedEntries = [int]$expected.unique_lemma_count
$expectedSenses = [int]$expected.valid_record_count
$expectedDefinitions = [int]$expected.definition_bearing_count

Write-Host "LEXiTRON graph: $actualEntries entries, $actualSenses senses, $actualDefinitions definitions."
if ($actualEntries -ne $expectedEntries) {
    throw "Expected $expectedEntries LEXiTRON entries but found $actualEntries."
}
if ($actualSenses -ne $expectedSenses) {
    throw "Expected $expectedSenses LEXiTRON senses but found $actualSenses."
}
if ($actualDefinitions -ne $expectedDefinitions) {
    throw "Expected $expectedDefinitions LEXiTRON definitions but found $actualDefinitions."
}
Write-Host "LEXiTRON verification passed."
