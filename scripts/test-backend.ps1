[CmdletBinding()]
param(
    [switch]$SkipLiveGraphDB
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$backendPath = Join-Path $projectRoot "backend"
$previousPythonPath = $env:PYTHONPATH

try {
    $env:PYTHONPATH = $backendPath
    python -m unittest discover -s (Join-Path $backendPath "tests") -v
    if ($LASTEXITCODE -ne 0) {
        throw "Backend unit tests failed."
    }
    if (-not $SkipLiveGraphDB) {
        python (Join-Path $backendPath "smoke_test.py")
        if ($LASTEXITCODE -ne 0) {
            throw "Backend GraphDB smoke test failed."
        }
    }
}
finally {
    $env:PYTHONPATH = $previousPythonPath
}
