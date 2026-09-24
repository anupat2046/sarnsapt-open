[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$NormalizedInput,
    [Parameter(Mandatory = $true)][string]$SourceGraph,
    [string]$BaseUrl = "http://127.0.0.1:7200",
    [string]$RepositoryId = "thailex",
    [switch]$SkipImport
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$NormalizedInput = [System.IO.Path]::GetFullPath($NormalizedInput)
if (-not (Test-Path -LiteralPath $NormalizedInput -PathType Leaf)) {
    throw "Normalized JSONL not found: $NormalizedInput"
}
if ($SourceGraph -notmatch '^https://w3id\.org/thailex/graph/(?:lexitron|thai-wordnet|en-wiktionary-thai-entries|th-wiktionary|organizer/[a-z0-9._-]+/[a-z0-9._-]+)$') {
    throw "Unsupported source graph: $SourceGraph"
}

$sourceSuffix = $SourceGraph.Substring("https://w3id.org/thailex/graph/".Length)
$stem = "auto-alignment-" + $sourceSuffix.Replace("/", "-")
$candidateOutput = Join-Path $projectRoot "data\normalized\$stem.jsonl"
$rdfOutput = Join-Path $projectRoot "data\rdf\generated\$stem.ttl"
$validationOutput = Join-Path $projectRoot "reports\$stem-validation.json"
$sourceRoot = Join-Path $projectRoot "src"
$endpoint = "$BaseUrl/repositories/$RepositoryId"

try {
    $null = Invoke-RestMethod -Uri "$BaseUrl/rest/repositories" -Method Get -TimeoutSec 10
}
catch {
    throw "GraphDB is unavailable at $BaseUrl. Start it before automatic alignment."
}

$previousPythonPath = $env:PYTHONPATH
$env:PYTHONPATH = if ($previousPythonPath) { "$sourceRoot;$previousPythonPath" } else { $sourceRoot }
Push-Location $projectRoot
try {
    & python -m thailex_ingestion.cli build-auto-alignments `
        --endpoint $endpoint `
        --normalized-input $NormalizedInput `
        --source-graph $SourceGraph `
        --candidates-output $candidateOutput `
        --rdf-output $rdfOutput `
        --validation-output $validationOutput
    if ($LASTEXITCODE -ne 0) {
        throw "Automatic alignment failed before graph import."
    }

    $report = Get-Content -LiteralPath $validationOutput -Raw | ConvertFrom-Json
    $graphIri = [string]$report.proposal_graph
    if ($graphIri -ne "https://w3id.org/thailex/graph/alignment/proposed/auto/$sourceSuffix") {
        throw "Unexpected proposal graph in validation report: $graphIri"
    }
    if (-not $SkipImport) {
        $context = [Uri]::EscapeDataString("<$graphIri>")
        $graphEndpoint = "$endpoint/statements?context=$context"
        $existsQuery = "ASK WHERE { GRAPH <$graphIri> { ?s ?p ?o } }"
        $existsResult = Invoke-RestMethod -Uri $endpoint -Method Post `
            -ContentType "application/x-www-form-urlencoded" `
            -Headers @{ Accept = "application/sparql-results+json" } `
            -Body @{ query = $existsQuery }
        $backupPath = $null
        if ($existsResult.boolean) {
            $backupDir = Join-Path $projectRoot "data\rdf\backup"
            New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
            $backupPath = Join-Path $backupDir "$stem-$(Get-Date -Format 'yyyyMMdd-HHmmss').ttl"
            & curl.exe --fail --silent --show-error -H "Accept: text/turtle" `
                -o $backupPath $graphEndpoint
            if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $backupPath)) {
                throw "Could not back up existing proposal graph. It was not changed."
            }
            Write-Host "Backed up machine proposals to $backupPath"
            & curl.exe --fail --silent --show-error -X DELETE $graphEndpoint
            if ($LASTEXITCODE -ne 0) {
                throw "Could not clear proposal graph $graphIri."
            }
        }
        try {
            & curl.exe --fail --silent --show-error -X POST $graphEndpoint `
                -H "Content-Type: text/turtle; charset=utf-8" `
                --data-binary "@$rdfOutput"
            if ($LASTEXITCODE -ne 0) {
                throw "Could not import $rdfOutput"
            }
            $countQuery = "PREFIX tlkg: <https://w3id.org/thailex/ontology/> SELECT (COUNT(DISTINCT ?a) AS ?n) WHERE { GRAPH <$graphIri> { ?a a tlkg:AlignmentAssertion } }"
            $countResult = Invoke-RestMethod -Uri $endpoint -Method Post `
                -ContentType "application/x-www-form-urlencoded" `
                -Headers @{ Accept = "application/sparql-results+json" } `
                -Body @{ query = $countQuery }
            $actual = [int]$countResult.results.bindings[0].n.value
            if ($actual -ne [int]$report.proposed_count) {
                throw "Automatic alignment verification failed: expected $($report.proposed_count), found $actual."
            }
        }
        catch {
            $failure = $_
            if ($backupPath) {
                & curl.exe --fail --silent --show-error -X DELETE $graphEndpoint
                if ($LASTEXITCODE -ne 0) {
                    throw "Import failed and clearing the partial graph failed. Restore manually from $backupPath. Original error: $failure"
                }
                & curl.exe --fail --silent --show-error -X POST $graphEndpoint `
                    -H "Content-Type: text/turtle; charset=utf-8" `
                    --data-binary "@$backupPath"
                if ($LASTEXITCODE -ne 0) {
                    throw "Import failed and automatic restore failed. Restore manually from $backupPath. Original error: $failure"
                }
                throw "Import failed; previous machine proposals restored from $backupPath. Original error: $failure"
            }
            throw $failure
        }
        Write-Host "Imported $actual unreviewed links into <$graphIri>. Reviewed decisions were not changed."
    }
}
finally {
    $env:PYTHONPATH = $previousPythonPath
    Pop-Location
}
