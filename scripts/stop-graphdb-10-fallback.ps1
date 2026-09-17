[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$composeFile = Join-Path $projectRoot "docker-compose.10-fallback.yml"

Push-Location $projectRoot
try {
    docker compose -f $composeFile down
    if ($LASTEXITCODE -ne 0) {
        throw "Could not stop the GraphDB 10.8.14 fallback stack. Exit code: $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}
