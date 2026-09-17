[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][Alias("Input")][string]$InputPath,
    [Parameter(Mandatory = $true)][Alias("Mapping")][string]$MappingPath,
    [string]$OutputPath
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$InputPath = [System.IO.Path]::GetFullPath($InputPath)
$MappingPath = [System.IO.Path]::GetFullPath($MappingPath)
foreach ($requiredFile in @($InputPath, $MappingPath)) {
    if (-not (Test-Path -LiteralPath $requiredFile -PathType Leaf)) {
        throw "Required file not found: $requiredFile"
    }
}
$mappingConfig = Get-Content -LiteralPath $MappingPath -Raw | ConvertFrom-Json
$sourceId = [string]$mappingConfig.source.id
$editionId = [string]$mappingConfig.source.edition_id
if (-not $OutputPath) {
    $OutputPath = Join-Path $projectRoot "reports\organizer-$sourceId-$editionId-audit.json"
}
$sourceRoot = Join-Path $projectRoot "src"
$previousPythonPath = $env:PYTHONPATH
$env:PYTHONPATH = if ($previousPythonPath) { "$sourceRoot;$previousPythonPath" } else { $sourceRoot }
try {
    python -m thailex_ingestion.cli audit-organizer `
        --input $InputPath `
        --mapping $MappingPath `
        --output $OutputPath
    if ($LASTEXITCODE -ne 0) {
        throw "Organizer dataset audit failed. Review $OutputPath."
    }
    Write-Host "Review the audit before building or importing: $OutputPath"
}
finally {
    $env:PYTHONPATH = $previousPythonPath
}
