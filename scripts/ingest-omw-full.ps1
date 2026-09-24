[CmdletBinding()]
param(
    [string]$ThaiInput,
    [string]$EnglishInput,
    [string]$BaseUrl = "http://127.0.0.1:7200",
    [string]$RepositoryId = "thailex",
    [int]$BatchSize = 1000,
    [switch]$SkipBuild,
    [switch]$SkipImport,
    [switch]$SkipAutoAlignment
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not $ThaiInput) {
    $ThaiInput = Join-Path $projectRoot "data\raw\omw\omw-th\omw-th.xml"
}
if (-not $EnglishInput) {
    $EnglishInput = Join-Path $projectRoot "data\raw\omw\omw-en\omw-en.xml"
}

$thaiNormalized = Join-Path $projectRoot "data\normalized\omw-th-full.jsonl"
$englishNormalized = Join-Path $projectRoot "data\normalized\omw-en-full.jsonl"
$thaiRdf = Join-Path $projectRoot "data\rdf\generated\omw-th-full.ttl"
$englishRdf = Join-Path $projectRoot "data\rdf\generated\omw-en-full.ttl"
$validationOutput = Join-Path $projectRoot "reports\omw-full-validation.json"
$benchmarkOutput = Join-Path $projectRoot "reports\omw-full-benchmark.json"
$lookupQuery = Join-Path $projectRoot "queries\omw-lookup.rq"
$sourceRoot = Join-Path $projectRoot "src"

foreach ($requiredFile in @($ThaiInput, $EnglishInput)) {
    if (-not (Test-Path -LiteralPath $requiredFile -PathType Leaf)) {
        throw "Required file not found: $requiredFile. Run scripts\download-omw.ps1 first."
    }
}

$previousPythonPath = $env:PYTHONPATH
$env:PYTHONPATH = if ($previousPythonPath) { "$sourceRoot;$previousPythonPath" } else { $sourceRoot }

Push-Location $projectRoot
try {
    if (-not $SkipBuild) {
        python -m thailex_ingestion.cli build-omw-full `
            --thai-input $ThaiInput `
            --english-input $EnglishInput `
            --thai-normalized-output $thaiNormalized `
            --english-normalized-output $englishNormalized `
            --thai-rdf-output $thaiRdf `
            --english-rdf-output $englishRdf `
            --validation-output $validationOutput `
            --batch-size $BatchSize
        if ($LASTEXITCODE -ne 0) {
            throw "Full OMW build failed with exit code $LASTEXITCODE."
        }
    }

    foreach ($requiredOutput in @($thaiRdf, $englishRdf, $validationOutput)) {
        if (-not (Test-Path -LiteralPath $requiredOutput -PathType Leaf)) {
            throw "Required full-build output not found: $requiredOutput"
        }
    }

    if (-not $SkipImport) {
        try {
            $null = Invoke-RestMethod -Uri "$BaseUrl/rest/repositories" -Method Get -TimeoutSec 10
        }
        catch {
            throw "GraphDB is unavailable at $BaseUrl. Start it first or use -SkipImport."
        }

        $importSeconds = @{}
        function Replace-FullNamedGraph {
            param(
                [Parameter(Mandatory = $true)][string]$GraphIri,
                [Parameter(Mandatory = $true)][string]$FilePath,
                [Parameter(Mandatory = $true)][string]$MetricName
            )
            $context = [Uri]::EscapeDataString("<$GraphIri>")
            $endpoint = "$BaseUrl/repositories/$RepositoryId/statements?context=$context"
            Write-Host "Replacing full named graph <$GraphIri> ..."
            $timer = [System.Diagnostics.Stopwatch]::StartNew()
            & curl.exe --fail --silent --show-error -X DELETE $endpoint
            if ($LASTEXITCODE -ne 0) {
                throw "Could not clear named graph $GraphIri. curl exit code: $LASTEXITCODE"
            }
            & curl.exe --fail --silent --show-error `
                -X POST $endpoint `
                -H "Content-Type: text/turtle; charset=utf-8" `
                --data-binary "@$FilePath"
            if ($LASTEXITCODE -ne 0) {
                throw "Could not import $FilePath. curl exit code: $LASTEXITCODE"
            }
            $timer.Stop()
            $importSeconds[$MetricName] = [math]::Round($timer.Elapsed.TotalSeconds, 3)
        }

        Replace-FullNamedGraph `
            -GraphIri "https://w3id.org/thailex/graph/thai-wordnet" `
            -FilePath $thaiRdf `
            -MetricName "thai_graph_import_seconds"
        Replace-FullNamedGraph `
            -GraphIri "https://w3id.org/thailex/graph/omw-en" `
            -FilePath $englishRdf `
            -MetricName "english_graph_import_seconds"

        $verifyTimer = [System.Diagnostics.Stopwatch]::StartNew()
        & (Join-Path $PSScriptRoot "verify-omw.ps1") `
            -BaseUrl $BaseUrl `
            -RepositoryId $RepositoryId `
            -Mode Full `
            -ValidationReport $validationOutput
        $verifyTimer.Stop()
        if (-not $SkipAutoAlignment) {
            & (Join-Path $PSScriptRoot "build-auto-alignments.ps1") `
                -NormalizedInput $thaiNormalized `
                -SourceGraph "https://w3id.org/thailex/graph/thai-wordnet" `
                -BaseUrl $BaseUrl `
                -RepositoryId $RepositoryId
        }

        $lookupTimer = [System.Diagnostics.Stopwatch]::StartNew()
        $lookupResult = & curl.exe --fail --silent --show-error `
            -X POST "$BaseUrl/repositories/$RepositoryId" `
            -H "Accept: application/sparql-results+json" `
            -H "Content-Type: application/sparql-query; charset=utf-8" `
            --data-binary "@$lookupQuery"
        if ($LASTEXITCODE -ne 0) {
            throw "Full OMW lookup benchmark failed with exit code $LASTEXITCODE"
        }
        $lookupTimer.Stop()
        $lookupRows = (($lookupResult | ConvertFrom-Json).results.bindings).Count

        $validation = Get-Content -LiteralPath $validationOutput -Raw | ConvertFrom-Json
        $repositorySize = Invoke-RestMethod -Uri "$BaseUrl/repositories/$RepositoryId/size" -Method Get
        $benchmark = [ordered]@{
            release = $validation.release
            build_elapsed_seconds = $validation.build_elapsed_seconds
            thai_rdf_bytes = $validation.outputs.thai_rdf_bytes
            english_rdf_bytes = $validation.outputs.english_rdf_bytes
            thai_graph_import_seconds = $importSeconds.thai_graph_import_seconds
            english_graph_import_seconds = $importSeconds.english_graph_import_seconds
            verification_seconds = [math]::Round($verifyTimer.Elapsed.TotalSeconds, 3)
            lookup_query_seconds = [math]::Round($lookupTimer.Elapsed.TotalSeconds, 3)
            lookup_result_rows = $lookupRows
            repository_statement_count = [int64]$repositorySize
        }
        $benchmark | ConvertTo-Json | Set-Content -LiteralPath $benchmarkOutput -Encoding utf8
        Write-Host "Benchmark written to $benchmarkOutput"
    }
}
finally {
    $env:PYTHONPATH = $previousPythonPath
    Pop-Location
}
