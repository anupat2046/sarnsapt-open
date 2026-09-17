[CmdletBinding()]
param(
    [string]$InputPath,
    [string]$LemmaFile,
    [string]$PolicyPath,
    [string]$BaseUrl = "http://localhost:7200",
    [string]$RepositoryId = "thailex",
    [string]$GraphIri = "https://w3id.org/thailex/graph/lexitron",
    [switch]$Full,
    [switch]$SkipImport
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot

if (-not $InputPath) {
    $InputPath = Join-Path $projectRoot "data\raw\lexitron\telex.csv"
}
if (-not $Full -and -not $LemmaFile) {
    $LemmaFile = Join-Path $projectRoot "data\config\lexitron-demo-lemmas.txt"
}
if (-not $PolicyPath) {
    $PolicyPath = Join-Path $projectRoot "data\config\lexitron-data-policy.json"
}

$suffix = if ($Full) { "full" } else { "demo" }
$normalizedOutput = Join-Path $projectRoot "data\normalized\lexitron-$suffix.jsonl"
$rdfOutput = Join-Path $projectRoot "data\rdf\generated\lexitron-$suffix.ttl"
$fixtureOutput = Join-Path $projectRoot "data\fixtures\lexitron\telex-demo.csv"
$auditOutput = Join-Path $projectRoot "reports\lexitron-audit.json"
$validationOutput = Join-Path $projectRoot "reports\lexitron-$suffix-validation.json"
$sourceRoot = Join-Path $projectRoot "src"

$requiredFiles = @($InputPath, $PolicyPath)
if (-not $Full) { $requiredFiles += $LemmaFile }
foreach ($requiredFile in $requiredFiles) {
    if (-not (Test-Path -LiteralPath $requiredFile -PathType Leaf)) {
        throw "Required file not found: $requiredFile"
    }
}

$previousPythonPath = $env:PYTHONPATH
$env:PYTHONPATH = if ($previousPythonPath) { "$sourceRoot;$previousPythonPath" } else { $sourceRoot }

Push-Location $projectRoot
try {
    python -m thailex_ingestion.cli audit-lexitron `
        --input $InputPath `
        --policy $PolicyPath `
        --output $auditOutput
    if ($LASTEXITCODE -ne 0) {
        throw "LEXiTRON audit failed with exit code $LASTEXITCODE. See $auditOutput"
    }

    $buildArguments = @(
        "-m", "thailex_ingestion.cli", "build-lexitron",
        "--input", $InputPath,
        "--policy", $PolicyPath,
        "--normalized-output", $normalizedOutput,
        "--rdf-output", $rdfOutput,
        "--validation-output", $validationOutput
    )
    if ($Full) {
        $buildArguments += "--all-records"
    }
    else {
        $buildArguments += @("--lemma-file", $LemmaFile, "--fixture-output", $fixtureOutput)
    }
    & python @buildArguments
    if ($LASTEXITCODE -ne 0) {
        throw "LEXiTRON normalization failed with exit code $LASTEXITCODE. See $validationOutput"
    }

    if (-not $SkipImport) {
        try {
            $null = Invoke-RestMethod -Uri "$BaseUrl/rest/repositories" -Method Get -TimeoutSec 10
        }
        catch {
            throw "GraphDB is unavailable at $BaseUrl. Start it first or use -SkipImport."
        }

        $context = [Uri]::EscapeDataString("<$GraphIri>")
        $statementsEndpoint = "$BaseUrl/repositories/$RepositoryId/statements?context=$context"
        Write-Host "Replacing named graph <$GraphIri> ..."
        & curl.exe --fail --silent --show-error -X DELETE $statementsEndpoint
        if ($LASTEXITCODE -ne 0) {
            throw "Could not clear the LEXiTRON named graph. curl exit code: $LASTEXITCODE"
        }

        & curl.exe --fail --silent --show-error `
            -X POST $statementsEndpoint `
            -H "Content-Type: text/turtle; charset=utf-8" `
            --data-binary "@$rdfOutput"
        if ($LASTEXITCODE -ne 0) {
            throw "Could not import the LEXiTRON RDF. curl exit code: $LASTEXITCODE"
        }

        & (Join-Path $PSScriptRoot "verify-lexitron.ps1") `
            -BaseUrl $BaseUrl `
            -RepositoryId $RepositoryId `
            -ValidationReport $validationOutput
        if ($LASTEXITCODE -ne 0) {
            throw "LEXiTRON GraphDB verification failed with exit code $LASTEXITCODE"
        }
    }
}
finally {
    $env:PYTHONPATH = $previousPythonPath
    Pop-Location
}
