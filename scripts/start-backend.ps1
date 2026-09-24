[CmdletBinding()]
param(
    [int]$Port = 8000,
    [switch]$Reload
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$backendPath = Join-Path $projectRoot "backend"
$envFile = Join-Path $projectRoot ".env"

if (Test-Path -LiteralPath $envFile) {
    foreach ($line in Get-Content -LiteralPath $envFile -Encoding UTF8) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#")) {
            continue
        }
        $parts = $trimmed -split "=", 2
        if ($parts.Count -ne 2 -or $parts[0] -notmatch "^[A-Za-z_][A-Za-z0-9_]*$") {
            throw "Invalid .env line: $line"
        }
        $name = $parts[0]
        $value = $parts[1].Trim().Trim('"').Trim("'")
        if (-not [Environment]::GetEnvironmentVariable($name, "Process")) {
            [Environment]::SetEnvironmentVariable($name, $value, "Process")
        }
    }
}

if (-not $env:THAILEX_SPARQL_ENDPOINT) {
    $env:THAILEX_SPARQL_ENDPOINT = "http://127.0.0.1:7200/repositories/thailex"
}

$arguments = @(
    "-m", "uvicorn", "thailex_api.main:app",
    "--app-dir", $backendPath,
    "--host", "127.0.0.1",
    "--port", $Port.ToString()
)
if ($Reload) {
    $arguments += "--reload"
}

Write-Host "Starting ThaiLex API at http://localhost:$Port"
Write-Host "OpenAPI docs: http://localhost:$Port/docs"
$selectorMode = if ($env:THAILEX_SELECTOR_MODE) { $env:THAILEX_SELECTOR_MODE } else { "heuristic" }
Write-Host "Sense selector: $selectorMode"
& python @arguments
