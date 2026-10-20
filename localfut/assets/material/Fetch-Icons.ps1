# Maintainer-only refresh; the launcher uses embedded assets, never the network.
$ErrorActionPreference = 'Stop'
$icons = @{ play_arrow='av'; stop='av'; settings='action'; folder_open='file'; description='action'; lock='action' }
foreach ($name in $icons.Keys) {
    $url = "https://raw.githubusercontent.com/google/material-design-icons/master/src/$($icons[$name])/$name/materialicons/24px.svg"
    Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile (Join-Path $PSScriptRoot "$name.svg")
}
Invoke-WebRequest -UseBasicParsing -Uri 'https://raw.githubusercontent.com/google/material-design-icons/master/LICENSE' -OutFile (Join-Path $PSScriptRoot 'LICENSE')
Write-Host 'Downloaded six Google Material icons and their Apache-2.0 license.'
