[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$composeFile = Join-Path $projectRoot "docker-compose.10-fallback.yml"

Push-Location $projectRoot
try {
    docker volume create thailex-graphdb-home | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Could not prepare the GraphDB 10.8.14 fallback volume. Exit code: $LASTEXITCODE"
    }

    docker compose -f $composeFile up -d
    if ($LASTEXITCODE -ne 0) {
        throw "GraphDB 10.8.14 fallback startup failed with exit code $LASTEXITCODE"
    }

    & (Join-Path $PSScriptRoot "init-graphdb.ps1")
    if ($LASTEXITCODE -ne 0) {
        throw "GraphDB 10.8.14 fallback initialization failed with exit code $LASTEXITCODE"
    }

    & (Join-Path $PSScriptRoot "verify.ps1")
    if ($LASTEXITCODE -ne 0) {
        throw "GraphDB 10.8.14 fallback verification failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}
