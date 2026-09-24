[CmdletBinding()]
param(
    [string]$BaseUrl = "http://127.0.0.1:7200",
    [string]$RepositoryId = "thailex",
    [ValidateSet("Demo", "Full")][string]$Mode = "Demo",
    [string]$ValidationReport
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$queryName = if ($Mode -eq "Full") { "omw-summary.rq" } else { "omw-summary-demo.rq" }
$queryFile = Join-Path $projectRoot "queries\$queryName"
if (-not $ValidationReport) {
    $reportName = if ($Mode -eq "Full") { "omw-full-validation.json" } else { "omw-validation.json" }
    $ValidationReport = Join-Path $projectRoot "reports\$reportName"
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
    throw "OMW SPARQL verification failed with exit code $LASTEXITCODE"
}

$binding = ($result | ConvertFrom-Json).results.bindings[0]
$actual = @{
    ThaiEntries = [int]$binding.thaiEntryCount.value
    ThaiSenses = [int]$binding.thaiSenseCount.value
    ThaiSynsets = [int]$binding.thaiSynsetCount.value
    AlignedSynsets = [int]$binding.alignedSynsetCount.value
    EnglishSynsets = [int]$binding.englishSynsetCount.value
    Definitions = [int]$binding.definitionCount.value
    Relations = [int]$binding.relationCount.value
}
$expected = Get-Content -LiteralPath $ValidationReport -Raw | ConvertFrom-Json
$expectedRelations = 0
foreach ($property in $expected.selected_relation_counts.PSObject.Properties) {
    $expectedRelations += [int]$property.Value
}
$checks = @(
    @("Thai entries", $actual.ThaiEntries, [int]$expected.thai_entry_count),
    @("Thai senses", $actual.ThaiSenses, [int]$expected.thai_sense_count),
    @("Thai synsets", $actual.ThaiSynsets, [int]$expected.thai_synset_count),
    @("Aligned synsets", $actual.AlignedSynsets, [int]$expected.aligned_english_synset_count),
    @("English synsets", $actual.EnglishSynsets, [int]$expected.english_synset_count),
    @("English definitions", $actual.Definitions, [int]$expected.english_definition_count),
    @("Semantic relations", $actual.Relations, $expectedRelations)
)
foreach ($check in $checks) {
    if ($check[1] -ne $check[2]) {
        throw "$($check[0]): expected $($check[2]), found $($check[1])."
    }
}
Write-Host "OMW $Mode verification passed: $($actual.ThaiEntries) Thai entries, $($actual.ThaiSenses) Thai senses, $($actual.AlignedSynsets) aligned English synsets, $($actual.Definitions) definitions, $($actual.Relations) relations."
