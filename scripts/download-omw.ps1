[CmdletBinding()]
param(
    [string]$Destination,
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not $Destination) {
    $Destination = Join-Path $projectRoot "data\raw\omw"
}
New-Item -ItemType Directory -Force -Path $Destination | Out-Null

$packages = @(
    @{
        Name = "omw-th-2.0.tar.xz"
        Url = "https://github.com/omwn/omw-data/releases/download/v2.0/omw-th-2.0.tar.xz"
        Sha256 = "9B5E4C3696269A33CCD46092514440FD33FA81DCECD4969591D661D2D61A63D7"
    },
    @{
        Name = "omw-en-2.0.tar.xz"
        Url = "https://github.com/omwn/omw-data/releases/download/v2.0/omw-en-2.0.tar.xz"
        Sha256 = "0E09DFB7F096BC3F10B9DE68FFECF13839FA22AE46FD9B227CEC890D204CA1DC"
    }
)

foreach ($package in $packages) {
    $archive = Join-Path $Destination $package.Name
    if ($Force -or -not (Test-Path -LiteralPath $archive -PathType Leaf)) {
        Write-Host "Downloading $($package.Name) ..."
        & curl.exe --fail --location --silent --show-error --output $archive $package.Url
        if ($LASTEXITCODE -ne 0) {
            throw "Could not download $($package.Url). curl exit code: $LASTEXITCODE"
        }
    }
    $actualHash = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash
    if ($actualHash -ne $package.Sha256) {
        throw "SHA256 mismatch for $archive. Expected $($package.Sha256), found $actualHash."
    }
    Write-Host "Verified $($package.Name)."
    & tar.exe -xf $archive -C $Destination
    if ($LASTEXITCODE -ne 0) {
        throw "Could not extract $archive. tar exit code: $LASTEXITCODE"
    }
}

Write-Host "OMW 2.0 is ready under $Destination."

