[CmdletBinding()]
param(
    [string]$Input,
    [string]$LemmaFile,
    [string]$BaseUrl = "http://127.0.0.1:7200",
    [string]$RepositoryId = "thailex",
    [ValidateSet("en", "th")]
    [string]$Edition = "en",
    [switch]$Demo,
    [switch]$SkipImport,
    [switch]$SkipAutoAlignment
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not $Input) {
    $Input = if ($Edition -eq "th") {
        Join-Path $projectRoot "data\raw\wiktextract\thwiktionary-raw.jsonl.gz"
    }
    else {
        Join-Path $projectRoot "data\raw\wiktextract\kaikki.org-dictionary-Thai.jsonl"
    }
}
if ($Demo -and -not $LemmaFile) {
    $LemmaFile = Join-Path $projectRoot "data\config\lexitron-demo-lemmas.txt"
}
$suffix = if ($Demo) { "demo" } else { "full" }
$datasetSlug = if ($Edition -eq "th") { "th-wiktionary" } else { "en-wiktionary-thai-entries" }
$normalizedOutput = Join-Path $projectRoot "data\normalized\$datasetSlug-$suffix.jsonl"
$rdfOutput = Join-Path $projectRoot "data\rdf\generated\$datasetSlug-$suffix.ttl"
$auditOutput = Join-Path $projectRoot "reports\$datasetSlug-audit.json"
$validationOutput = Join-Path $projectRoot "reports\$datasetSlug-$suffix-validation.json"
$sourceRoot = Join-Path $projectRoot "src"

if (-not (Test-Path -LiteralPath $Input -PathType Leaf)) {
    throw "Wiktextract JSONL not found: $Input. Run scripts\download-wiktextract.ps1 first."
}
if ($LemmaFile -and -not (Test-Path -LiteralPath $LemmaFile -PathType Leaf)) {
    throw "Lemma file not found: $LemmaFile"
}

$previousPythonPath = $env:PYTHONPATH
$env:PYTHONPATH = if ($previousPythonPath) { "$sourceRoot;$previousPythonPath" } else { $sourceRoot }

Push-Location $projectRoot
try {
    python -m thailex_ingestion.cli audit-wiktextract `
        --input $Input `
        --edition $Edition `
        --output $auditOutput
    if ($LASTEXITCODE -ne 0) {
        throw "Wiktextract audit failed with exit code $LASTEXITCODE."
    }

    $buildArguments = @(
        "-m", "thailex_ingestion.cli", "build-wiktextract",
        "--input", $Input,
        "--edition", $Edition,
        "--normalized-output", $normalizedOutput,
        "--rdf-output", $rdfOutput,
        "--validation-output", $validationOutput
    )
    if ($LemmaFile) {
        $buildArguments += @("--lemma-file", $LemmaFile)
    }
    & python @buildArguments
    if ($LASTEXITCODE -ne 0) {
        throw "Wiktextract build failed with exit code $LASTEXITCODE."
    }

    if (-not $SkipImport) {
        try {
            $null = Invoke-RestMethod -Uri "$BaseUrl/rest/repositories" -Method Get -TimeoutSec 10
        }
        catch {
            throw "GraphDB is unavailable at $BaseUrl. Start it first or use -SkipImport."
        }
        $graphIri = if ($Edition -eq "th") {
            "https://w3id.org/thailex/graph/th-wiktionary"
        }
        else {
            "https://w3id.org/thailex/graph/en-wiktionary-thai-entries"
        }
        $context = [Uri]::EscapeDataString("<$graphIri>")
        $endpoint = "$BaseUrl/repositories/$RepositoryId/statements?context=$context"
        Write-Host "Replacing named graph <$graphIri> ..."
        & curl.exe --fail --silent --show-error -X DELETE $endpoint
        if ($LASTEXITCODE -ne 0) {
            throw "Could not clear named graph $graphIri. curl exit code: $LASTEXITCODE"
        }
        & curl.exe --fail --silent --show-error `
            -X POST $endpoint `
            -H "Content-Type: text/turtle; charset=utf-8" `
            --data-binary "@$rdfOutput"
        if ($LASTEXITCODE -ne 0) {
            throw "Could not import $rdfOutput. curl exit code: $LASTEXITCODE"
        }
        & (Join-Path $PSScriptRoot "verify-wiktextract.ps1") `
            -BaseUrl $BaseUrl `
            -RepositoryId $RepositoryId `
            -ValidationReport $validationOutput `
            -Edition $Edition `
            -GraphIri $graphIri
        if (-not $SkipAutoAlignment) {
            & (Join-Path $PSScriptRoot "build-auto-alignments.ps1") `
                -NormalizedInput $normalizedOutput `
                -SourceGraph $graphIri `
                -BaseUrl $BaseUrl `
                -RepositoryId $RepositoryId
        }
    }
}
finally {
    $env:PYTHONPATH = $previousPythonPath
    Pop-Location
}
