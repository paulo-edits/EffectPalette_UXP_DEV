param(
    [string]$Version = "0.53.0",
    [string]$Python = "python",
    [switch]$InstallBuildDeps,
    [switch]$SkipInstaller
)

# Ports EffectPalette's own packaging\build_release.ps1 (read-only CEP reference) - same PyInstaller
# + Inno Setup shape, adapted for two real differences: the companion's entrypoint lives under
# companion/, and the UXP plugin itself ships as a .ccx (built once via the UXP Developer Tool's
# own "Package" menu item - there is no way to automate that step from here) instead of a folder of
# CEP extension files this script would otherwise stage directly.

$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$ReleaseDir = Join-Path $Root "release"
$StageRoot = Join-Path $env:TEMP "FXPalette_Installer_Staging"
$StageDir = Join-Path $StageRoot "FXPalette"
$PyInstallerSpec = Join-Path $Root "packaging\pyinstaller\FXPalette.spec"
$InnoScript = Join-Path $Root "packaging\inno\FXPalette.iss"
$PyInstallerWorkRoot = Join-Path $env:TEMP "FXPalette_PyInstaller_Build"
$PyInstallerDistRoot = Join-Path $env:TEMP "FXPalette_PyInstaller_Dist"
$PyInstallerDist = Join-Path $PyInstallerDistRoot "FXPalette"
$CcxDir = Join-Path $Root "packaging\dist"

function Write-Step($Message) {
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Invoke-Checked($File, [string[]]$Arguments) {
    & $File @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed: $File $($Arguments -join ' ')"
    }
}

function Get-InnoCompiler() {
    $candidates = @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
        "${env:ProgramFiles(x86)}\Inno Setup 5\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 5\ISCC.exe"
    )
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path $candidate)) {
            return $candidate
        }
    }
    $fromPath = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($fromPath) {
        return $fromPath.Source
    }
    return $null
}

Write-Step "Checking Python build dependencies"
$previousErrorActionPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& $Python -c "import PyInstaller" 2>$null
$pyinstallerExitCode = $LASTEXITCODE
$ErrorActionPreference = $previousErrorActionPreference

if ($pyinstallerExitCode -ne 0) {
    if (-not $InstallBuildDeps) {
        throw "PyInstaller is not installed. Run: .\packaging\build_release.ps1 -InstallBuildDeps"
    }
    Invoke-Checked $Python @("-m", "pip", "--isolated", "install", "--upgrade", "pip")
    Invoke-Checked $Python @("-m", "pip", "--isolated", "install", "-r", (Join-Path $Root "companion\requirements.txt"))
    Invoke-Checked $Python @("-m", "pip", "--isolated", "install", "pyinstaller")
}

Write-Step "Building FX.palette.exe with PyInstaller"
if (Test-Path $PyInstallerWorkRoot) { Remove-Item -LiteralPath $PyInstallerWorkRoot -Recurse -Force }
if (Test-Path $PyInstallerDistRoot) { Remove-Item -LiteralPath $PyInstallerDistRoot -Recurse -Force }

Invoke-Checked $Python @(
    "-m", "PyInstaller",
    "--noconfirm",
    "--clean",
    "--workpath", $PyInstallerWorkRoot,
    "--distpath", $PyInstallerDistRoot,
    $PyInstallerSpec
)

if (-not (Test-Path (Join-Path $PyInstallerDist "FX.palette.exe"))) {
    throw "PyInstaller output not found: $PyInstallerDist"
}

if ($SkipInstaller) {
    Write-Step "Skipping Inno Setup installer"
    Write-Host "Staged app: $PyInstallerDist" -ForegroundColor Green
    exit 0
}

Write-Step "Locating the packaged UXP plugin (.ccx)"
# Built once via the UXP Developer Tool's own "Package" flyout menu item - not something this
# script (or Claude Code, lacking GUI access to UDT) can automate. Drop the resulting .ccx into
# packaging\dist\ before running this script without -SkipInstaller.
$ccxCandidates = @()
if (Test-Path $CcxDir) {
    $ccxCandidates = Get-ChildItem -Path $CcxDir -Filter "*.ccx" -File
}
if ($ccxCandidates.Count -eq 0) {
    throw "No .ccx file found in $CcxDir. Package the plugin via the UXP Developer Tool (flyout menu > Package) and place the resulting .ccx there, then re-run this script."
}
if ($ccxCandidates.Count -gt 1) {
    throw "More than one .ccx file found in $CcxDir - remove the stale one(s) so the build is unambiguous: $($ccxCandidates.Name -join ', ')"
}
$ccxFile = $ccxCandidates[0].FullName
Write-Host "Using: $ccxFile" -ForegroundColor Green

Write-Step "Preparing clean installer staging folder"
if (Test-Path $StageRoot) { Remove-Item -LiteralPath $StageRoot -Recurse -Force }
New-Item -ItemType Directory -Force -Path $StageDir | Out-Null
Copy-Item -Path (Join-Path $PyInstallerDist "*") -Destination $StageDir -Recurse -Force

Write-Step "Building setup executable with Inno Setup"
$innoCompiler = Get-InnoCompiler
if (-not $innoCompiler) {
    throw "Inno Setup compiler not found. Install Inno Setup 6 or run with -SkipInstaller to only create the staged app."
}

Invoke-Checked $innoCompiler @(
    "/DMyAppVersion=$Version",
    "/DSourceDir=$StageDir",
    "/DCcxFile=$ccxFile",
    $InnoScript
)

Write-Step "Release ready"
Write-Host "Installer output folder: $ReleaseDir" -ForegroundColor Green
