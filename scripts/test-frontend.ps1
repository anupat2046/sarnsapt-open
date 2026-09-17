$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$frontendRoot = Join-Path $projectRoot "frontend"
Push-Location $frontendRoot

try {
    if (-not (Test-Path -LiteralPath "node_modules")) {
        npm ci
    }

    npm run lint
    npm run build
}
finally {
    Pop-Location
}
