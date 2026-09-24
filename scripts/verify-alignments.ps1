[CmdletBinding()]
param(
    [string]$BaseUrl = "http://127.0.0.1:7200",
    [string]$RepositoryId = "thailex",
    [string]$ValidationReport,
    [switch]$ProposalOnly
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not $ValidationReport) {
    $ValidationReport = Join-Path $projectRoot "reports\alignment-validation.json"
}
$queryFile = Join-Path $projectRoot "queries\alignment-summary.rq"
foreach ($requiredFile in @($ValidationReport, $queryFile)) {
    if (-not (Test-Path -LiteralPath $requiredFile -PathType Leaf)) {
        throw "Required verification file not found: $requiredFile"
    }
}
$result = & curl.exe --fail --silent --show-error `
    -X POST "$BaseUrl/repositories/$RepositoryId" `
    -H "Accept: application/sparql-results+json" `
    -H "Content-Type: application/sparql-query; charset=utf-8" `
    --data-binary "@$queryFile"
if ($LASTEXITCODE -ne 0) {
    throw "Alignment SPARQL verification failed with exit code $LASTEXITCODE"
}
$binding = ($result | ConvertFrom-Json).results.bindings[0]
$actual = @{
    Proposed = [int]$binding.proposedCount.value
    Reviewed = [int]$binding.reviewedCount.value
    PendingEdges = [int]$binding.pendingEdgeCount.value
    ExactEdges = [int]$binding.exactEdgeCount.value
    CloseEdges = [int]$binding.closeEdgeCount.value
}
$expected = Get-Content -LiteralPath $ValidationReport -Raw | ConvertFrom-Json
$expectedExact = if ($expected.approved_relation_counts.exactMatch) { [int]$expected.approved_relation_counts.exactMatch } else { 0 }
$expectedClose = if ($expected.approved_relation_counts.closeMatch) { [int]$expected.approved_relation_counts.closeMatch } else { 0 }
$checks = @(
    @("Proposed assertions", $actual.Proposed, [int]$expected.pending_count),
    @("Pending possiblySameSense edges", $actual.PendingEdges, [int]$expected.pending_count)
)
if (-not $ProposalOnly) {
    $checks += @(
        @("Reviewed assertions", $actual.Reviewed, ([int]$expected.approved_count + [int]$expected.rejected_count)),
        @("Approved exactMatch edges", $actual.ExactEdges, $expectedExact),
        @("Approved closeMatch edges", $actual.CloseEdges, $expectedClose)
    )
}
foreach ($check in $checks) {
    if ($check[1] -ne $check[2]) {
        throw "$($check[0]): expected $($check[2]), found $($check[1])."
    }
}
Write-Host "Alignment verification passed: $($actual.Proposed) pending, $($actual.Reviewed) reviewed, $($actual.ExactEdges) exact and $($actual.CloseEdges) close edges."
