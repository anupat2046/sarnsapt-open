[CmdletBinding()]
param(
    [string]$BatchId = "phase3-review-batch-01",
    [int]$Size = 40,
    [int]$PerLemma = 2,
    [double]$MinimumConfidence = 0.85
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$candidateInput = Join-Path $projectRoot "data\normalized\alignment-candidates.jsonl"
$lemmaFile = Join-Path $projectRoot "data\config\lexitron-demo-lemmas.txt"
$output = Join-Path $projectRoot "reports\alignment-review-batch-01.json"
$sourceRoot = Join-Path $projectRoot "src"

if (-not (Test-Path -LiteralPath $candidateInput -PathType Leaf)) {
    throw "Alignment candidates not found. Run scripts\build-alignments.ps1 first."
}

$previousPythonPath = $env:PYTHONPATH
$env:PYTHONPATH = if ($previousPythonPath) { "$sourceRoot;$previousPythonPath" } else { $sourceRoot }
Push-Location $projectRoot
try {
    & python -m thailex_ingestion.cli select-alignment-review-batch `
        --candidates-input $candidateInput `
        --lemma-file $lemmaFile `
        --output $output `
        --batch-id $BatchId `
        --size $Size `
        --per-lemma $PerLemma `
        --min-confidence $MinimumConfidence
    if ($LASTEXITCODE -ne 0) {
        throw "Review batch selection failed with exit code $LASTEXITCODE."
    }
}
finally {
    $env:PYTHONPATH = $previousPythonPath
    Pop-Location
}
