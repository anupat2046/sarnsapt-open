[CmdletBinding()]
param(
    [string]$LemmaFile,
    [string]$Decisions,
    [string]$BaseUrl = "http://127.0.0.1:7200",
    [string]$RepositoryId = "thailex",
    [switch]$SkipImport
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not $LemmaFile) {
    $LemmaFile = Join-Path $projectRoot "data\config\lexitron-demo-lemmas.txt"
}
if (-not (Test-Path -LiteralPath $LemmaFile -PathType Leaf)) {
    throw "Lemma file not found: $LemmaFile"
}
if ($Decisions -and -not (Test-Path -LiteralPath $Decisions -PathType Leaf)) {
    throw "Alignment decisions file not found: $Decisions"
}
if ($Decisions -and -not $SkipImport) {
    throw "This script no longer bulk-imports reviewed decisions because it could overwrite existing human reviews. Use -SkipImport to generate review files, then submit decisions through the Expert Review API."
}

try {
    $null = Invoke-RestMethod -Uri "$BaseUrl/rest/repositories" -Method Get -TimeoutSec 10
}
catch {
    throw "GraphDB is unavailable at $BaseUrl. Start it before generating alignments."
}

$endpoint = "$BaseUrl/repositories/$RepositoryId"
$candidateOutput = Join-Path $projectRoot "data\normalized\alignment-candidates.jsonl"
$reviewOutput = Join-Path $projectRoot "reports\alignment-review.csv"
$proposedRdf = Join-Path $projectRoot "data\rdf\generated\alignment-proposed.ttl"
$reviewedRdf = Join-Path $projectRoot "data\rdf\generated\alignment-reviewed.ttl"
$validationOutput = Join-Path $projectRoot "reports\alignment-validation.json"
$sourceRoot = Join-Path $projectRoot "src"

$arguments = @(
    "-m", "thailex_ingestion.cli", "build-alignments",
    "--endpoint", $endpoint,
    "--lemma-file", $LemmaFile,
    "--candidates-output", $candidateOutput,
    "--review-output", $reviewOutput,
    "--proposed-rdf-output", $proposedRdf,
    "--reviewed-rdf-output", $reviewedRdf,
    "--validation-output", $validationOutput
)
if ($Decisions) {
    $arguments += @("--decisions", $Decisions)
}

$previousPythonPath = $env:PYTHONPATH
$env:PYTHONPATH = if ($previousPythonPath) { "$sourceRoot;$previousPythonPath" } else { $sourceRoot }
Push-Location $projectRoot
try {
    & python @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Alignment build failed with exit code $LASTEXITCODE."
    }
    if (-not $SkipImport) {
        function Replace-NamedGraph {
            param(
                [Parameter(Mandatory = $true)][string]$GraphIri,
                [Parameter(Mandatory = $true)][string]$FilePath
            )
            $context = [Uri]::EscapeDataString("<$GraphIri>")
            $graphEndpoint = "$BaseUrl/repositories/$RepositoryId/statements?context=$context"
            Write-Host "Replacing named graph <$GraphIri> ..."
            & curl.exe --fail --silent --show-error -X DELETE $graphEndpoint
            if ($LASTEXITCODE -ne 0) {
                throw "Could not clear named graph $GraphIri."
            }
            & curl.exe --fail --silent --show-error `
                -X POST $graphEndpoint `
                -H "Content-Type: text/turtle; charset=utf-8" `
                --data-binary "@$FilePath"
            if ($LASTEXITCODE -ne 0) {
                throw "Could not import $FilePath."
            }
        }
        Replace-NamedGraph -GraphIri "https://w3id.org/thailex/graph/alignment/proposed" -FilePath $proposedRdf
        & (Join-Path $PSScriptRoot "verify-alignments.ps1") `
            -BaseUrl $BaseUrl `
            -RepositoryId $RepositoryId `
            -ValidationReport $validationOutput `
            -ProposalOnly
    }
}
finally {
    $env:PYTHONPATH = $previousPythonPath
    Pop-Location
}
