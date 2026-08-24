# Deploys this throwaway dev-tool CEP panel into the local CEP extensions folder so Premiere Pro
# can load it. Separate from (and does not touch) the stable EffectPalette CEP repo or its own
# deploy_cep_dev.ps1 - this only ever writes into tools/template_generator's own destination folder.

$ErrorActionPreference = "Stop"

$SourceRoot = $PSScriptRoot
$Destination = Join-Path $env:APPDATA "Adobe\CEP\extensions\FXPaletteTemplateGenerator"

if (Get-Process -Name "Adobe Premiere Pro" -ErrorAction SilentlyContinue) {
    Write-Warning "Feche o Premiere Pro antes de rodar este deploy (CEP nao recarrega arquivos de uma extensao com o app aberto)."
    exit 1
}

if (Test-Path $Destination) {
    Remove-Item -Path $Destination -Recurse -Force
}
New-Item -ItemType Directory -Path $Destination -Force | Out-Null

$filesToCopy = @("index.html", "index.js", "generator.jsx", ".debug")
foreach ($file in $filesToCopy) {
    Copy-Item -Path (Join-Path $SourceRoot $file) -Destination $Destination -Force
}
Copy-Item -Path (Join-Path $SourceRoot "CSXS") -Destination $Destination -Recurse -Force
Copy-Item -Path (Join-Path $SourceRoot "lib") -Destination $Destination -Recurse -Force

# Unsigned/dev extensions only load with PlayerDebugMode=1 for the matching CSXS runtime. This repo's
# stable installer (EffectPalette.iss) already sets this for CSXS.8-20 on this machine, but set it
# again here defensively in case this script ever runs on a machine that hasn't installed that.
9..20 | ForEach-Object {
    $key = "HKCU:\Software\Adobe\CSXS.$_"
    if (-not (Test-Path $key)) {
        New-Item -Path $key -Force | Out-Null
    }
    New-ItemProperty -Path $key -Name "PlayerDebugMode" -Value "1" -PropertyType String -Force | Out-Null
}

Write-Host "Deployed to $Destination"
Write-Host "Abra o Premiere Pro e va em Window > Extensions > FX.palette Template Generator"
