# Only defines settings helpers; dot-sourcing never starts services or elevates.
function Resolve-LocalFutUserConfigRoot([string]$ExplicitRoot = '') {
    $root = $ExplicitRoot
    if ([string]::IsNullOrWhiteSpace($root)) { $root = $env:LOCALFUT21_USER_CONFIG_ROOT }
    if ([string]::IsNullOrWhiteSpace($root)) {
        $root = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'Project21\LocalFUT'
    }
    if (-not [IO.Path]::IsPathRooted($root)) { throw 'The user settings folder must be an absolute path.' }
    return [IO.Path]::GetFullPath($root)
}

function Get-LocalFutUserSettings([string]$ConfigRoot) {
    $defaults = [ordered]@{
        sourcePath = ''; gamePath = ''; gameReadyDelaySeconds = 5
        lsxMode = 'auto'; launchViaEAApp = $true; eaAppOfferId = '195853'; eaAppLauncherPath = ''
    }
    New-Item -ItemType Directory -Path $ConfigRoot -Force | Out-Null
    $path = Join-Path $ConfigRoot 'launcher-settings.json'
    if (-not (Test-Path -LiteralPath $path)) {
        try {
            $file = [IO.File]::Open($path, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write)
            try {
                $bytes = [Text.UTF8Encoding]::new($false).GetBytes(($defaults | ConvertTo-Json))
                $file.Write($bytes, 0, $bytes.Length)
            } finally { $file.Dispose() }
        } catch { if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw } }
    }
    try {
        $saved = Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop
        if ($saved -isnot [pscustomobject]) { throw 'Expected a JSON object.' }
        foreach ($property in $saved.PSObject.Properties) { $defaults[$property.Name] = $property.Value }
        if ($defaults.launchViaEAApp -isnot [bool]) { throw 'launchViaEAApp must be true or false.' }
        foreach ($key in @('sourcePath', 'gamePath', 'lsxMode', 'eaAppOfferId', 'eaAppLauncherPath')) {
            if ($null -ne $defaults[$key] -and $defaults[$key] -isnot [string]) { throw "$key must be text." }
        }
    } catch { throw "Cannot read user settings at '$path': $($_.Exception.Message) The file was not overwritten." }
    return [pscustomobject]$defaults
}
