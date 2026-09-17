[CmdletBinding()]
param(
    [string]$BaseUrl = "http://localhost:7200",
    [string]$RepositoryId = "thailex",
    [string]$ValidationReport,
    [ValidateSet("en", "th")]
    [string]$Edition = "en",
    [string]$GraphIri
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$queryFile = Join-Path $projectRoot "queries\wiktionary-summary.rq"
if (-not $ValidationReport) {
    $datasetSlug = if ($Edition -eq "th") { "th-wiktionary" } else { "en-wiktionary-thai-entries" }
    $ValidationReport = Join-Path $projectRoot "reports\$datasetSlug-full-validation.json"
}
if (-not $GraphIri) {
    $GraphIri = if ($Edition -eq "th") {
        "https://w3id.org/thailex/graph/th-wiktionary"
    }
    else {
        "https://w3id.org/thailex/graph/en-wiktionary-thai-entries"
    }
}
foreach ($requiredFile in @($queryFile, $ValidationReport)) {
    if (-not (Test-Path -LiteralPath $requiredFile -PathType Leaf)) {
        throw "Required verification file not found: $requiredFile"
    }
}

$query = (Get-Content -LiteralPath $queryFile -Raw).Replace("{{GRAPH_IRI}}", "<$GraphIri>")
try {
    $result = Invoke-RestMethod `
        -Uri "$BaseUrl/repositories/$RepositoryId" `
        -Method Post `
        -Headers @{ Accept = "application/sparql-results+json" } `
        -ContentType "application/sparql-query; charset=utf-8" `
        -Body $query
}
catch {
    throw "Wiktionary SPARQL verification failed for $GraphIri`: $($_.Exception.Message)"
}

$binding = $result.results.bindings[0]
$actual = @{
    Entries = [int]$binding.entryCount.value
    Senses = [int]$binding.senseCount.value
    Definitions = [int]$binding.definitionCount.value
    Pronunciations = [int]$binding.pronunciationCount.value
    Etymologies = [int]$binding.etymologyEntryCount.value
    Relations = [int]$binding.relationAssertionCount.value
}
$expected = Get-Content -LiteralPath $ValidationReport -Raw | ConvertFrom-Json
$checks = @(
    @("Entries", $actual.Entries, [int]$expected.unique_lemma_count),
    @("Senses", $actual.Senses, [int]$expected.valid_sense_record_count),
    @("Definitions", $actual.Definitions, [int]$expected.gloss_count),
    @("Pronunciations", $actual.Pronunciations, [int]$expected.ipa_count),
    @("Etymology entry records", $actual.Etymologies, [int]$expected.etymology_entry_record_count),
    @("Relation assertions", $actual.Relations, [int]$expected.sense_relation_count)
)
foreach ($check in $checks) {
    if ($check[1] -ne $check[2]) {
        throw "$($check[0]): expected $($check[2]), found $($check[1])."
    }
}
Write-Host "Wiktionary verification passed for $GraphIri`: $($actual.Entries) entries, $($actual.Senses) senses, $($actual.Definitions) definitions, $($actual.Pronunciations) pronunciations, $($actual.Etymologies) etymologies, $($actual.Relations) relations."
