[CmdletBinding()]
param(
    [string]$ThaiInput,
    [string]$EnglishInput,
    [string]$LemmaFile,
    [string]$BaseUrl = "http://127.0.0.1:7200",
    [string]$RepositoryId = "thailex",
    [switch]$SkipImport
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not $ThaiInput) {
    $ThaiInput = Join-Path $projectRoot "data\raw\omw\omw-th\omw-th.xml"
}
if (-not $EnglishInput) {
    $EnglishInput = Join-Path $projectRoot "data\raw\omw\omw-en\omw-en.xml"
}
if (-not $LemmaFile) {
    $LemmaFile = Join-Path $projectRoot "data\config\lexitron-demo-lemmas.txt"
}

$thaiNormalized = Join-Path $projectRoot "data\normalized\omw-th-demo.json"
$englishNormalized = Join-Path $projectRoot "data\normalized\omw-en-demo.json"
$thaiRdf = Join-Path $projectRoot "data\rdf\generated\omw-th-demo.ttl"
$englishRdf = Join-Path $projectRoot "data\rdf\generated\omw-en-demo.ttl"
$auditOutput = Join-Path $projectRoot "reports\omw-audit.json"
$validationOutput = Join-Path $projectRoot "reports\omw-validation.json"
$sourceRoot = Join-Path $projectRoot "src"

foreach ($requiredFile in @($ThaiInput, $EnglishInput, $LemmaFile)) {
    if (-not (Test-Path -LiteralPath $requiredFile -PathType Leaf)) {
        throw "Required file not found: $requiredFile. Run scripts\download-omw.ps1 first."
    }
}

$previousPythonPath = $env:PYTHONPATH
$env:PYTHONPATH = if ($previousPythonPath) { "$sourceRoot;$previousPythonPath" } else { $sourceRoot }

Push-Location $projectRoot
try {
    python -m thailex_ingestion.cli audit-omw `
        --thai-input $ThaiInput `
        --english-input $EnglishInput `
        --output $auditOutput
    if ($LASTEXITCODE -ne 0) {
        throw "OMW audit failed with exit code $LASTEXITCODE."
    }

    python -m thailex_ingestion.cli build-omw `
        --thai-input $ThaiInput `
        --english-input $EnglishInput `
        --lemma-file $LemmaFile `
        --thai-normalized-output $thaiNormalized `
        --english-normalized-output $englishNormalized `
        --thai-rdf-output $thaiRdf `
        --english-rdf-output $englishRdf `
        --validation-output $validationOutput
    if ($LASTEXITCODE -ne 0) {
        throw "OMW subset build failed with exit code $LASTEXITCODE."
    }

    if (-not $SkipImport) {
        try {
            $null = Invoke-RestMethod -Uri "$BaseUrl/rest/repositories" -Method Get -TimeoutSec 10
        }
        catch {
            throw "GraphDB is unavailable at $BaseUrl. Start it first or use -SkipImport."
        }

        function Replace-NamedGraph {
            param(
                [Parameter(Mandatory = $true)][string]$GraphIri,
                [Parameter(Mandatory = $true)][string]$FilePath
            )
            $context = [Uri]::EscapeDataString("<$GraphIri>")
            $endpoint = "$BaseUrl/repositories/$RepositoryId/statements?context=$context"
            Write-Host "Replacing named graph <$GraphIri> ..."
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
        }

        Replace-NamedGraph `
            -GraphIri "https://w3id.org/thailex/graph/thai-wordnet-demo" `
            -FilePath $thaiRdf
        Replace-NamedGraph `
            -GraphIri "https://w3id.org/thailex/graph/omw-en-demo" `
            -FilePath $englishRdf

        & (Join-Path $PSScriptRoot "verify-omw.ps1") `
            -BaseUrl $BaseUrl `
            -RepositoryId $RepositoryId `
            -Mode Demo `
            -ValidationReport $validationOutput
    }
}
finally {
    $env:PYTHONPATH = $previousPythonPath
    Pop-Location
}
