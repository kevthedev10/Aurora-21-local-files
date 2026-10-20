[CmdletBinding()]
param(
    [string]$UserConfigRoot = "",
    [int]$Port = 0
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'User-Settings.ps1')

$resolvedRoot = Resolve-LocalFutUserConfigRoot $UserConfigRoot
if ($Port -le 0) {
    $Port = 8090
    $configPath = Join-Path $resolvedRoot 'config.json'
    if (Test-Path -LiteralPath $configPath -PathType Leaf) {
        try {
            $config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
            if ($config.fut_port -and [int]$config.fut_port -gt 0) { $Port = [int]$config.fut_port }
        } catch {
            throw "Cannot read $configPath. Correct its JSON before granting assets. $($_.Exception.Message)"
        }
    }
}

$uri = "http://127.0.0.1:$Port/localfut/customisation/grant-all"
try {
    $result = Invoke-RestMethod -Method Post -Uri $uri -ContentType 'application/json' -Body '{}'
} catch {
    throw "LocalFUT is not responding at $uri. Start the local server, then run this script again. $($_.Exception.Message)"
}

Write-Host "Granted $($result.grantedCount) new customisation items."
Write-Host "Available: $($result.available.kits) kits, $($result.available.stadiums) stadiums, $($result.available.badges) verified badge, $($result.available.balls) verified ball."
if ($result.unsupportedVanityCategories.Count -gt 0) {
    Write-Warning ("Not granted because their native FIFA 21 definition tables are not bundled: " + ($result.unsupportedVanityCategories -join ', '))
}
$result
