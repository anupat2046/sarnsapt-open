[CmdletBinding()]
param(
    [string]$BaseUrl = "http://127.0.0.1:7200",
    [string]$RepositoryId = "thailex",
    [int]$TimeoutSeconds = 180
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$repositoryConfig = Join-Path $projectRoot "graphdb\repository-config.ttl"
$ontologyFile = Join-Path $projectRoot "ontology\thailex.ttl"
$sampleFile = Join-Path $projectRoot "data\sample\thailex-sample.ttl"

foreach ($requiredFile in @($repositoryConfig, $ontologyFile, $sampleFile)) {
    if (-not (Test-Path -LiteralPath $requiredFile)) {
        throw "Required file not found: $requiredFile"
    }
}

Write-Host "Waiting for GraphDB at $BaseUrl ..."
$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
$ready = $false
while ((Get-Date) -lt $deadline) {
    try {
        $null = Invoke-RestMethod -Uri "$BaseUrl/rest/repositories" -Method Get -TimeoutSec 5
        $ready = $true
        break
    }
    catch {
        Start-Sleep -Seconds 2
    }
}

if (-not $ready) {
    throw "GraphDB did not become ready within $TimeoutSeconds seconds. Run 'docker compose logs graphdb' for details."
}

$repositories = @(Invoke-RestMethod -Uri "$BaseUrl/rest/repositories" -Method Get)
$repositoryExists = $repositories | Where-Object { $_.id -eq $RepositoryId }

if (-not $repositoryExists) {
    Write-Host "Creating GraphDB repository '$RepositoryId' ..."
    $createOutput = & curl.exe --fail --silent --show-error `
        -X POST "$BaseUrl/rest/repositories" `
        -F "config=@$repositoryConfig;type=text/turtle"
    if ($LASTEXITCODE -ne 0) {
        throw "Could not create repository '$RepositoryId'. curl exit code: $LASTEXITCODE"
    }
    if ($createOutput) { Write-Host $createOutput }
}
else {
    Write-Host "Repository '$RepositoryId' already exists."
}

function Import-TurtleNamedGraph {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string]$GraphIri
    )

    $context = [Uri]::EscapeDataString("<$GraphIri>")
    $endpoint = "$BaseUrl/repositories/$RepositoryId/statements?context=$context"
    Write-Host "Replacing <$GraphIri> with $(Split-Path -Leaf $FilePath) ..."
    & curl.exe --fail --silent --show-error -X DELETE $endpoint
    if ($LASTEXITCODE -ne 0) {
        throw "Could not clear named graph '$GraphIri'. curl exit code: $LASTEXITCODE"
    }
    & curl.exe --fail --silent --show-error `
        -X POST $endpoint `
        -H "Content-Type: text/turtle; charset=utf-8" `
        --data-binary "@$FilePath"
    if ($LASTEXITCODE -ne 0) {
        throw "Could not import '$FilePath'. curl exit code: $LASTEXITCODE"
    }
}

Import-TurtleNamedGraph -FilePath $ontologyFile -GraphIri "https://w3id.org/thailex/graph/ontology"
Import-TurtleNamedGraph -FilePath $sampleFile -GraphIri "https://w3id.org/thailex/graph/demo-edition-a"

Write-Host "GraphDB initialization complete."
Write-Host "Workbench: $BaseUrl"
Write-Host "SPARQL endpoint: $BaseUrl/repositories/$RepositoryId"
