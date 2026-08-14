param(
    [Parameter(Mandatory = $true)][string]$ExpectedQuery
)

$ErrorActionPreference = "Stop"
$capturePath = Join-Path $env:TEMP "fxpalette-premiere-pointer-probe.png"
$helperDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
$diagnostic = Join-Path $helperDirectory "diagnose_premiere_window.py"
$inspector = Join-Path $helperDirectory "inspect_effects_result.ps1"

& python $diagnostic --output $capturePath
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& powershell -NoProfile -ExecutionPolicy Bypass -File $inspector `
    -ImagePath $capturePath `
    -ExpectedQuery $ExpectedQuery `
    -MovePointer `
    -MaxImageAgeSeconds 5
exit $LASTEXITCODE
