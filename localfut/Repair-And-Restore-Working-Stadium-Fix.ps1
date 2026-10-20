[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# Repairs the failed wrapper patch AND restores the actual stadium code from
# the earlier working build.
#
# Put this script directly in:
#   Project-21-V2-main\localfut\
#
# Then run:
#   .\Repair-And-Restore-Working-Stadium-Fix.ps1

$serverPath = Join-Path $PSScriptRoot "runtime-source\localfut21\server.py"

if (-not (Test-Path -LiteralPath $serverPath -PathType Leaf)) {
    throw "Could not find: $serverPath`nPut this script directly inside the localfut folder."
}

$serverText = Get-Content -LiteralPath $serverPath -Raw -Encoding UTF8
$originalText = $serverText

# ---------------------------------------------------------------------------
# 1) Remove the two wrapper-style patches created earlier.
#    One of them was inserted before _native_squad_battle_opponent() existed,
#    causing the server-start NameError.
# ---------------------------------------------------------------------------

$badBlocks = @(
    @{
        Begin = "# LOCALFUT_AWAY_STADIUM_FIX_BEGIN"
        End   = "# LOCALFUT_AWAY_STADIUM_FIX_END"
    },
    @{
        Begin = "# LOCALFUT_LEGACY_STADIUM_FIX_V2_BEGIN"
        End   = "# LOCALFUT_LEGACY_STADIUM_FIX_V2_END"
    }
)

foreach ($block in $badBlocks) {
    if ($serverText.Contains($block.Begin)) {
        $pattern =
            "(?ms)^" +
            [regex]::Escape($block.Begin) +
            ".*?^" +
            [regex]::Escape($block.End) +
            "\r?\n?"

        $serverText = [regex]::Replace($serverText, $pattern, "")
        Write-Host "Removed old patch block: $($block.Begin)" -ForegroundColor Yellow
    }
}

# ---------------------------------------------------------------------------
# 2) Restore the ACTUAL active-stadium code from the earlier working build.
#
# Earlier working build:
#   - did NOT use _selected_stadium_id() for the active stadium
#   - resourceId/carddbid = 6200001
#   - assetId             = physical stadium id 2
#   - stadiumid/StadiumId = 2
#
# The newer build changed this to the selectable Sanderson Park card
# (6200058 / assetId 261), which is the regression we want to undo.
# ---------------------------------------------------------------------------

$newActiveHeader = @'
    ts = int(STATE.get("established", now_s()))
    sel_stadium = _selected_stadium_id()
    sel_cap = int(_STADIUM_BY_ID.get(sel_stadium, {}).get("capacity", 45000) or 45000)
    items = [
        {
            # The stadium CARD identity stays 6200058 / assetId 261 -- a valid
            # fcc_stadium card the My Stadium editor can render.  The physical
            # venue (stadiumid / StadiumId) follows the player's selection
            # (persisted via PUT /ut/game/fifa21/stadium).
            "family": "stadiums", "table": "fcc_stadium", "resourceId": 6200058,
            "carddbid": 6200058, "assetId": 261, "cardassetid": 36,
            "itemType": "stadium", "cardsubtypeid": 10, "itemState": "activeStadium",
            "owners": 1, "untradeable": True, "discardValue": 10, "lastSalePrice": 0,
            "stackCount": 1, "untradeableCount": 1, "pile": "club",
            "timestamp": ts, "rating": 64, "rareflag": 0, "value": 64,
            "weightrare": 0, "category": 4,
            "stadiumid": sel_stadium, "capacity": sel_cap,
            "StadiumId": sel_stadium, "boost": 0, "id": 910000000003,
        },
'@

$workingActiveHeader = @'
    ts = int(STATE.get("established", now_s()))
    items = [
        {
            # Physical venue = SQUAD_BATTLE_STADIUM_ID (Santiago Bernabeu, id 2):
            # a fully-modelled real stadium that renders correctly.  Sanderson
            # Park (id 261) loaded as a bare "floating pitch" because the local
            # My Stadium structure/crowds config is incomplete.
            "family": "stadiums", "table": "fcc_stadium",
            "resourceId": SQUAD_BATTLE_STADIUM_RESOURCE_ID,
            "carddbid": SQUAD_BATTLE_STADIUM_RESOURCE_ID,
            "assetId": SQUAD_BATTLE_STADIUM_ID, "cardassetid": 36,
            "itemType": "stadium", "cardsubtypeid": 10, "itemState": "activeStadium",
            "owners": 1, "untradeable": True, "discardValue": 10, "lastSalePrice": 0,
            "stackCount": 1, "untradeableCount": 1, "pile": "club",
            "timestamp": ts, "rating": 64, "rareflag": 0, "value": 64,
            "weightrare": 0, "category": 4,
            "stadiumid": SQUAD_BATTLE_STADIUM_ID, "capacity": 80000,
            "StadiumId": SQUAD_BATTLE_STADIUM_ID, "boost": 0, "id": 910000000003,
        },
'@

if ($serverText.Contains($newActiveHeader)) {
    $serverText = $serverText.Replace($newActiveHeader, $workingActiveHeader)
    Write-Host "Restored known-good active stadium definition." -ForegroundColor Green
}
elseif ($serverText.Contains($workingActiveHeader)) {
    Write-Host "Active stadium definition is already on the known-good version." -ForegroundColor DarkGray
}
else {
    throw @"
Could not locate the newer _active_club_items() stadium block.

No file has been written yet.
Your server.py may differ from the supplied Project-21-V2 build.
"@
}

# ---------------------------------------------------------------------------
# 3) Restore the ACTUAL match handoff from the earlier working build.
#
# Earlier build ALWAYS handed the match loader physical stadium id 2.
# The newer build switches to _selected_stadium_id() whenever opponent_slot
# is missing.  Some FIFA 21 match requests do not include that slot, which can
# reintroduce the invalid/invisible venue.
# ---------------------------------------------------------------------------

$newMatchBlock = @'
    # Home matches play in the club's selected venue; a Squad Battles away
    # fixture (opponent_slot set) still needs the forced Bernabeu because the
    # retail PC client cannot resolve a generated opponent's away venue.
    match_stadium = (SQUAD_BATTLE_STADIUM_ID if opponent_slot is not None
                     else _selected_stadium_id())
    squad["stadiumAssetId"] = match_stadium
    squad["stadiumId"] = match_stadium
    response = {
        "squad": squad,
        "homeSquad": squad,
        "isHomeTeam": True,
        "startDateTime": now_s(),
        "stadiumAssetId": match_stadium,
        "stadiumId": match_stadium,
    }
'@

$workingMatchBlock = @'
    squad["stadiumAssetId"] = SQUAD_BATTLE_STADIUM_ID
    squad["stadiumId"] = SQUAD_BATTLE_STADIUM_ID
    response = {
        "squad": squad,
        "homeSquad": squad,
        "isHomeTeam": True,
        "startDateTime": now_s(),
        "stadiumAssetId": SQUAD_BATTLE_STADIUM_ID,
        "stadiumId": SQUAD_BATTLE_STADIUM_ID,
    }
'@

if ($serverText.Contains($newMatchBlock)) {
    $serverText = $serverText.Replace($newMatchBlock, $workingMatchBlock)
    Write-Host "Restored known-good match stadium handoff." -ForegroundColor Green
}
elseif ($serverText.Contains($workingMatchBlock)) {
    Write-Host "Match stadium handoff is already on the known-good version." -ForegroundColor DarkGray
}
else {
    throw @"
Could not locate the newer _native_match_response() stadium block.

No file has been written yet.
"@
}

# ---------------------------------------------------------------------------
# 4) Sanity checks.
# ---------------------------------------------------------------------------

if ($serverText.Contains("_localfut_newer_native_squad_battle_opponent = _native_squad_battle_opponent")) {
    throw "The crashing wrapper assignment is still present. Refusing to write server.py."
}

foreach ($required in @(
    '"resourceId": SQUAD_BATTLE_STADIUM_RESOURCE_ID',
    '"assetId": SQUAD_BATTLE_STADIUM_ID, "cardassetid": 36',
    '"stadiumid": SQUAD_BATTLE_STADIUM_ID, "capacity": 80000',
    'squad["stadiumAssetId"] = SQUAD_BATTLE_STADIUM_ID',
    '"stadiumAssetId": SQUAD_BATTLE_STADIUM_ID'
)) {
    if (-not $serverText.Contains($required)) {
        throw "Validation failed. Missing expected restored code: $required"
    }
}

if ($serverText -eq $originalText) {
    Write-Host ""
    Write-Host "Nothing needed changing." -ForegroundColor Yellow
    exit 0
}

# ---------------------------------------------------------------------------
# 5) Back up and write atomically.
# ---------------------------------------------------------------------------

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$backupPath = "$serverPath.bak-before-stadium-repair-$timestamp"
$tempPath = "$serverPath.tmp-$PID"

Copy-Item -LiteralPath $serverPath -Destination $backupPath

$utf8NoBom = New-Object System.Text.UTF8Encoding($false)

try {
    [System.IO.File]::WriteAllText($tempPath, $serverText, $utf8NoBom)
    Move-Item -LiteralPath $tempPath -Destination $serverPath -Force
}
catch {
    Copy-Item -LiteralPath $backupPath -Destination $serverPath -Force
    Remove-Item -LiteralPath $tempPath -Force -ErrorAction SilentlyContinue
    throw
}

Write-Host ""
Write-Host "Repair complete." -ForegroundColor Green
Write-Host ""
Write-Host "Fixed:"
Write-Host "  - removed the wrapper patch that stopped server.py starting"
Write-Host "  - restored the earlier build's active Bernabeu stadium object"
Write-Host "  - restored the earlier build's always-stadium-2 match handoff"
Write-Host ""
Write-Host "Known-good stadium mapping:"
Write-Host "  resourceId: 6200001"
Write-Host "  physical ID: 2"
Write-Host ""
Write-Host "Backup:"
Write-Host "  $backupPath"
Write-Host ""
Write-Host "Fully restart LocalFUT and FIFA 21."
