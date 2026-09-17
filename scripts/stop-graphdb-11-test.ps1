[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$composeFile = Join-Path $projectRoot "docker-compose.11-test.yml"

Push-Location $projectRoot
try {
    docker compose -f $composeFile down
    if ($LASTEXITCODE -ne 0) {
        throw "Could not stop the GraphDB 11.5 test stack. Exit code: $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}
