[CmdletBinding()]
param(
    [string]$BaseUrl = "http://localhost:7200",
    [string]$RepositoryId = "thailex",
    [Parameter(Mandatory = $true)][string]$ValidationReport
)

$ErrorActionPreference = "Stop"
if (-not (Test-Path -LiteralPath $ValidationReport -PathType Leaf)) {
    throw "Validation report not found: $ValidationReport"
}
$expected = Get-Content -LiteralPath $ValidationReport -Raw | ConvertFrom-Json
$graphIri = [string]$expected.graph_iri
if (-not $graphIri.StartsWith("https://w3id.org/thailex/graph/organizer/")) {
    throw "Validation report contains an invalid organizer graph IRI: $graphIri"
}

$query = @"
PREFIX ontolex: <http://www.w3.org/ns/lemon/ontolex#>
PREFIX prov: <http://www.w3.org/ns/prov#>
PREFIX tlkg: <https://w3id.org/thailex/ontology/>
SELECT ?entryCount ?senseCount ?definitionCount ?relationCount ?sourceRecordCount ?datasetCount
WHERE {
  { SELECT (COUNT(DISTINCT ?entry) AS ?entryCount) WHERE { GRAPH <$graphIri> { ?entry a ontolex:LexicalEntry ; ontolex:sense ?sense } } }
  { SELECT (COUNT(DISTINCT ?sense) AS ?senseCount) WHERE { GRAPH <$graphIri> { ?sense a ontolex:LexicalSense } } }
  { SELECT (COUNT(DISTINCT ?definition) AS ?definitionCount) WHERE { GRAPH <$graphIri> { ?sense tlkg:hasDefinition ?definition } } }
  { SELECT (COUNT(DISTINCT ?relation) AS ?relationCount) WHERE { GRAPH <$graphIri> { ?relation a tlkg:RelationAssertion } } }
  { SELECT (COUNT(DISTINCT ?record) AS ?sourceRecordCount) WHERE { GRAPH <$graphIri> { ?record tlkg:rawRecordText ?raw } } }
  { SELECT (COUNT(DISTINCT ?dataset) AS ?datasetCount) WHERE { GRAPH <$graphIri> { ?dataset a tlkg:OrganizerSource } } }
}
"@
$result = & curl.exe --fail --silent --show-error `
    -X POST "$BaseUrl/repositories/$RepositoryId" `
    -H "Accept: application/sparql-results+json" `
    -H "Content-Type: application/sparql-query; charset=utf-8" `
    --data-binary $query
if ($LASTEXITCODE -ne 0) {
    throw "Organizer SPARQL verification failed with exit code $LASTEXITCODE"
}
$binding = ($result | ConvertFrom-Json).results.bindings[0]
$actual = @{
    Entries = [int]$binding.entryCount.value
    Senses = [int]$binding.senseCount.value
    Definitions = [int]$binding.definitionCount.value
    Relations = [int]$binding.relationCount.value
    SourceRecords = [int]$binding.sourceRecordCount.value
    Datasets = [int]$binding.datasetCount.value
}
$checks = @(
    @("Entries", $actual.Entries, [int]$expected.unique_lemma_count),
    @("Senses", $actual.Senses, [int]$expected.valid_record_count),
    @("Definitions", $actual.Definitions, [int]$expected.definition_count),
    @("Relations", $actual.Relations, [int]$expected.relation_count),
    @("Source records", $actual.SourceRecords, [int]$expected.valid_record_count),
    @("Organizer datasets", $actual.Datasets, 1)
)
foreach ($check in $checks) {
    if ($check[1] -ne $check[2]) {
        throw "$($check[0]): expected $($check[2]), found $($check[1])."
    }
}
Write-Host "Organizer graph verification passed: $($actual.Entries) entries, $($actual.Senses) senses, $($actual.Definitions) definitions, $($actual.Relations) relations, all with source records."
