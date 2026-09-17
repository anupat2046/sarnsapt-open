[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][Alias("Input")][string]$InputPath,
    [Parameter(Mandatory = $true)][Alias("Mapping")][string]$MappingPath,
    [string]$BaseUrl = "http://localhost:7200",
    [string]$RepositoryId = "thailex",
    [switch]$SkipImport,
    [switch]$ReplaceExisting
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$InputPath = [System.IO.Path]::GetFullPath($InputPath)
$MappingPath = [System.IO.Path]::GetFullPath($MappingPath)
foreach ($requiredFile in @($InputPath, $MappingPath)) {
    if (-not (Test-Path -LiteralPath $requiredFile -PathType Leaf)) {
        throw "Required file not found: $requiredFile"
    }
}

$mappingConfig = Get-Content -LiteralPath $MappingPath -Raw | ConvertFrom-Json
$sourceId = [string]$mappingConfig.source.id
$editionId = [string]$mappingConfig.source.edition_id
if ($sourceId -notmatch '^[a-z0-9][a-z0-9._-]*$' -or $editionId -notmatch '^[a-z0-9][a-z0-9._-]*$') {
    throw "source.id and source.edition_id must be lowercase safe IDs before ingestion."
}
$stem = "organizer-$sourceId-$editionId"
$auditOutput = Join-Path $projectRoot "reports\$stem-audit.json"
$normalizedOutput = Join-Path $projectRoot "data\normalized\$stem.jsonl"
$rdfOutput = Join-Path $projectRoot "data\rdf\generated\$stem.ttl"
$validationOutput = Join-Path $projectRoot "reports\$stem-validation.json"
$sourceRoot = Join-Path $projectRoot "src"

$previousPythonPath = $env:PYTHONPATH
$env:PYTHONPATH = if ($previousPythonPath) { "$sourceRoot;$previousPythonPath" } else { $sourceRoot }
Push-Location $projectRoot
try {
    python -m thailex_ingestion.cli audit-organizer `
        --input $InputPath `
        --mapping $MappingPath `
        --output $auditOutput
    if ($LASTEXITCODE -ne 0) {
        throw "Organizer dataset audit failed. Review $auditOutput before continuing."
    }

    python -m thailex_ingestion.cli build-organizer `
        --input $InputPath `
        --mapping $MappingPath `
        --audit-report $auditOutput `
        --normalized-output $normalizedOutput `
        --rdf-output $rdfOutput `
        --validation-output $validationOutput
    if ($LASTEXITCODE -ne 0) {
        throw "Organizer dataset build failed. Review $validationOutput."
    }

    if (-not $SkipImport) {
        try {
            $null = Invoke-RestMethod -Uri "$BaseUrl/rest/repositories" -Method Get -TimeoutSec 10
        }
        catch {
            throw "GraphDB is unavailable at $BaseUrl. Start it first or use -SkipImport."
        }
        $validation = Get-Content -LiteralPath $validationOutput -Raw | ConvertFrom-Json
        $graphIri = [string]$validation.graph_iri
        if (-not $graphIri.StartsWith("https://w3id.org/thailex/graph/organizer/")) {
            throw "Validation report contains an unexpected graph IRI: $graphIri"
        }
        $context = [Uri]::EscapeDataString("<$graphIri>")
        $endpoint = "$BaseUrl/repositories/$RepositoryId/statements?context=$context"
        $existsQuery = "ASK WHERE { GRAPH <$graphIri> { ?s ?p ?o } }"
        $existsResult = Invoke-RestMethod `
            -Uri "$BaseUrl/repositories/$RepositoryId" `
            -Method Post `
            -ContentType "application/x-www-form-urlencoded" `
            -Headers @{ Accept = "application/sparql-results+json" } `
            -Body @{ query = $existsQuery }
        if ($existsResult.boolean) {
            if (-not $ReplaceExisting) {
                throw "Named graph <$graphIri> already contains data. Rerun with -ReplaceExisting only if you intend to replace this exact source edition."
            }
            $backupDir = Join-Path $projectRoot "data\rdf\backup"
            New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
            $backupPath = Join-Path $backupDir "$stem-$(Get-Date -Format 'yyyyMMdd-HHmmss').ttl"
            & curl.exe --fail --silent --show-error `
                -H "Accept: text/turtle" `
                -o $backupPath $endpoint
            if ($LASTEXITCODE -ne 0) {
                throw "Could not back up named graph $graphIri. Existing graph was not changed."
            }
            Write-Host "Backed up existing graph to $backupPath"
            & curl.exe --fail --silent --show-error -X DELETE $endpoint
            if ($LASTEXITCODE -ne 0) {
                throw "Could not clear named graph $graphIri. curl exit code: $LASTEXITCODE"
            }
        }
        & curl.exe --fail --silent --show-error `
            -X POST $endpoint `
            -H "Content-Type: text/turtle; charset=utf-8" `
            --data-binary "@$rdfOutput"
        if ($LASTEXITCODE -ne 0) {
            throw "Could not import $rdfOutput. curl exit code: $LASTEXITCODE"
        }
        & (Join-Path $PSScriptRoot "verify-organizer.ps1") `
            -BaseUrl $BaseUrl `
            -RepositoryId $RepositoryId `
            -ValidationReport $validationOutput
    }
}
finally {
    $env:PYTHONPATH = $previousPythonPath
    Pop-Location
}
