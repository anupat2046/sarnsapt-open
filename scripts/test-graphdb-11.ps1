[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$composeFile = Join-Path $projectRoot "docker-compose.11-test.yml"
$licenseFile = Join-Path $projectRoot "secrets\graphdb.license"
$baseUrl = "http://localhost:7201"

if (-not (Test-Path -LiteralPath $licenseFile -PathType Leaf)) {
    throw "GraphDB 11 Free license not found at '$licenseFile'. Save the emailed license there before running this test."
}

Push-Location $projectRoot
try {
    docker volume create thailex-graphdb-11.5-home | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Could not prepare the GraphDB 11.5 Docker volume. Exit code: $LASTEXITCODE"
    }

    docker compose -f $composeFile up -d
    if ($LASTEXITCODE -ne 0) {
        throw "GraphDB 11.5 Docker Compose startup failed with exit code $LASTEXITCODE"
    }

    & (Join-Path $PSScriptRoot "init-graphdb.ps1") -BaseUrl $baseUrl
    if ($LASTEXITCODE -ne 0) {
        throw "GraphDB 11.5 initialization failed with exit code $LASTEXITCODE"
    }

    & (Join-Path $PSScriptRoot "verify.ps1") -BaseUrl $baseUrl
    if ($LASTEXITCODE -ne 0) {
        throw "GraphDB 11.5 verification failed with exit code $LASTEXITCODE"
    }

    Write-Host "GraphDB 11.5 candidate passed on $baseUrl using volume 'thailex-graphdb-11.5-home'."
}
finally {
    Pop-Location
}
