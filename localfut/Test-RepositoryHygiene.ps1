[CmdletBinding()]
param()

# Read-only: checks ignore rules and the Git index; never launches the game.
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path $PSScriptRoot -Parent
$script:checks = 0

function Assert-Hygiene($Condition, [string]$Message) {
    if (-not $Condition) { throw "FAIL: $Message" }
    $script:checks++
}

try {
    $artifacts = @(
        'localfut/Project21-LocalFUT-Launcher.exe',
        'localfut/Project21-LocalAuthBridge.exe'
    )
    $required = $artifacts + @(
        'localfut/FIFA21-LocalFUT-Launcher.cs', 'localfut/FIFA21-LocalAuthBridge.cs',
        'localfut/LauncherSettings.cs', 'localfut/LauncherTheme.cs',
        'localfut/Build-FIFA21-Launcher.ps1', 'localfut/Launch-LocalFUT.ps1',
        'localfut/RUN-FIFA21.cmd', 'localfut/User-Settings.ps1',
        'localfut/launcher-settings.example.json',
        'localfut/runtime-source/localfut21/config.example.json',
        'localfut/runtime-source/localfut21/user_config.py',
        'localfut/runtime-source/localfut21/onboarding.py',
        'localfut/runtime-source/localfut21/players.json',
        'localfut/runtime/mined/tables/cards_ng_db/players.json',
        'localfut/runtime/mined/tables/fifa_ng_db/players.json',
        'localfut/native-data/player-catalog-manifest.json'
    )
    $required += @('play_arrow', 'stop', 'settings', 'folder_open', 'description', 'lock' |
        ForEach-Object { "localfut/assets/material/$_.svg" })
    $ignored = @(
        'localfut/runtime/server.log', 'localfut/runtime/server.log.1',
        'localfut/runtime/status.json', 'localfut/runtime/offline-status.json',
        'localfut/runtime/launcher-processes.json', 'localfut/runtime/server.pid',
        'localfut/runtime/captures/session.log', 'localfut/runtime/captures/frame.bin',
        'localfut/runtime-source/localfut21/lsx_capture.log',
        'localfut/runtime-source/localfut21/__pycache__/server.cpython-314.pyc',
        'localfut/launcher-settings.json',
        'localfut/runtime-source/localfut21/config.json',
        'localfut/runtime-source/localfut21/pack_settings.json',
        'localfut/localfut15.sqlite3', 'localfut/localfut15.sqlite3-wal',
        'localfut/localfut15.sqlite3-shm', 'FIFA21.exe', 'localfut/FIFA21.exe',
        'localfut/other-helper.exe', 'localfut/game.dll'
    )
    foreach ($path in $ignored + $required) {
        & git -C $repoRoot check-ignore --no-index --quiet -- $path
        $result = $LASTEXITCODE
        Assert-Hygiene ($result -in @(0, 1)) "Git ignore check failed: $path"
        Assert-Hygiene (($result -eq 0) -eq ($path -in $ignored)) "Wrong ignore policy: $path"
    }
    $tracked = @(& git -C $repoRoot ls-files)
    Assert-Hygiene ($LASTEXITCODE -eq 0) 'Cannot read Git index'
    foreach ($path in $required) {
        Assert-Hygiene ($path -in $tracked) "Required shared file is not staged/tracked: $path"
        Assert-Hygiene (Test-Path -LiteralPath (Join-Path $repoRoot $path) -PathType Leaf) "Missing local file: $path"
    }
    $unwanted = @($tracked | Where-Object {
        $_ -match '(?i)(\.log(?:\..*)?$|\.jsonl$|\.pid$|\.py[cod]$|\.sqlite3(?:-wal|-shm)?$|/__pycache__/)' -or
        $_ -like 'localfut/runtime/captures/*' -or $_ -in $ignored
    })
    Assert-Hygiene ($unwanted.Count -eq 0) "Generated files still tracked: $($unwanted -join ', ')"
    $unexpectedExe = @($tracked | Where-Object { $_ -like '*.exe' -and $_ -notin $artifacts })
    Assert-Hygiene ($unexpectedExe.Count -eq 0) "Unexpected EXEs tracked: $($unexpectedExe -join ', ')"
    Write-Host "PASS: $script:checks repository hygiene checks; shared data/defaults and exactly two launcher artifacts are tracked."
} catch {
    Write-Error $_ -ErrorAction Continue
    exit 1
}
