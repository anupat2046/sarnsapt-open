[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot

Push-Location $projectRoot
try {
    docker compose up -d
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose up failed with exit code $LASTEXITCODE"
    }

    & (Join-Path $PSScriptRoot "init-graphdb.ps1")
    if ($LASTEXITCODE -ne 0) {
        throw "GraphDB initialization failed with exit code $LASTEXITCODE"
    }

    & (Join-Path $PSScriptRoot "verify.ps1")
    if ($LASTEXITCODE -ne 0) {
        throw "GraphDB verification failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}
