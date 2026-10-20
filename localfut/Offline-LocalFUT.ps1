[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$ProjectRoot = $PSScriptRoot
$DataRoot = Join-Path $ProjectRoot 'runtime-source\localfut21'
$RuntimeRoot = Join-Path $ProjectRoot 'runtime'
$StatusPath = Join-Path $RuntimeRoot 'offline-status.json'
$LogPath = Join-Path $RuntimeRoot 'offline-launcher.log'

New-Item -ItemType Directory -Force -Path $RuntimeRoot | Out-Null

function Write-Log([string]$Message, [ConsoleColor]$Color = [ConsoleColor]::White) {
    $line = "[{0}] {1}" -f (Get-Date).ToString('s'), $Message
    Add-Content -LiteralPath $LogPath -Value $line -Encoding utf8
    Write-Host $line -ForegroundColor $Color
}

function Save-Status([object]$Status) {
    $Status.updatedAt = (Get-Date).ToString('o')
    $Status | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $StatusPath -Encoding utf8
}

function Invoke-PythonStep([string]$Name, [string]$ScriptPath) {
    Write-Log "Running $Name ..." Cyan
    Push-Location $DataRoot
    $priorPreference = $ErrorActionPreference
    try {
        # Existing API tests log normal diagnostics to stderr. Capture that
        # output without treating it as a PowerShell exception.
        $ErrorActionPreference = 'Continue'
        $output = @(& $script:Python.Source -3 $ScriptPath 2>&1)
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $priorPreference
        Pop-Location
    }
    foreach ($line in $output) { Add-Content -LiteralPath $script:LogPath -Value ([string]$line) -Encoding utf8 }
    if ($output) { $output | ForEach-Object { Write-Host $_ } }
    if ($exitCode -ne 0) { throw "$Name failed with exit code $exitCode." }
    Write-Log "$Name passed." Green
}

$status = [ordered]@{
    mode = 'OFFLINE_LOCAL_DATA_VALIDATION'
    sqlite = 'NOT STARTED'
    account = 'NOT STARTED'
    club = 'NOT STARTED'
    squad = 'NOT STARTED'
    players = 'NOT STARTED'
    ownership = 'NOT STARTED'
    persistence = 'NOT STARTED'
    localServices = 'NOT AVAILABLE (no permitted listener service)'
    apiHealth = 'NOT STARTED'
    final = 'FAILED'
    detail = @()
}

try {
    Set-Content -LiteralPath $LogPath -Value '' -Encoding utf8
    Write-Log 'Standalone offline local FUT validation started.' Cyan

    $required = @(
        'config.example.json', 'user_config.py', 'players.json', 'validate_offline_data.py',
        'test_offline_api.py', 'test-fixtures\validate_fixtures.py'
    )
    $missing = @($required | Where-Object { -not (Test-Path -LiteralPath (Join-Path $DataRoot $_) -PathType Leaf) })
    if ($missing) { throw "Missing required offline files: $($missing -join ', ')" }

    $script:Python = Get-Command py -ErrorAction Stop
    & $script:Python.Source -3 -c 'import cryptography; print(cryptography.__version__)' | ForEach-Object {
        Add-Content -LiteralPath $LogPath -Value "cryptography=$_" -Encoding utf8
    }
    if ($LASTEXITCODE -ne 0) { throw 'Python cryptography dependency check failed.' }
    Write-Log 'Python and cryptography dependency checks passed.' Green

    # This script validates the current database only; it does not call the
    # data seeder and therefore does not rebuild or overwrite the FUT state.
    Invoke-PythonStep 'SQLite/account/club/squad/player/ownership/persistence validation' (Join-Path $DataRoot 'validate_offline_data.py')
    $status.sqlite = 'PASS'
    $status.account = 'PASS'
    $status.club = 'PASS'
    $status.squad = 'PASS'
    $status.players = 'PASS'
    $status.ownership = 'PASS'
    $status.persistence = 'PASS'

    Invoke-PythonStep 'Offline fixture validation' (Join-Path $DataRoot 'test-fixtures\validate_fixtures.py')
    Invoke-PythonStep 'Existing direct in-process API/data smoke tests' (Join-Path $DataRoot 'test_offline_api.py')
    $status.apiHealth = 'PASS (in-process; no listener started)'
    $status.detail += 'No listener was started: server.py is the excluded FUT/Blaze protocol-emulation component.'
    $status.detail += 'No FIFA process, TLS, hosts, certificate, trust, or network routing operation was used.'
    $status.final = 'OFFLINE LOCAL FUT READY'
    Save-Status $status

    Write-Host ''
    Write-Host 'OFFLINE LOCAL FUT STATUS' -ForegroundColor Cyan
    Write-Host 'SQLite:             PASS' -ForegroundColor Green
    Write-Host 'Account:            PASS' -ForegroundColor Green
    Write-Host 'Club:               PASS' -ForegroundColor Green
    Write-Host 'Squad:              PASS' -ForegroundColor Green
    Write-Host 'Players:            PASS' -ForegroundColor Green
    Write-Host 'Ownership:          PASS' -ForegroundColor Green
    Write-Host 'Persistence:        PASS' -ForegroundColor Green
    Write-Host 'Local services:     NOT AVAILABLE (no permitted listener service)' -ForegroundColor Yellow
    Write-Host 'API health:         PASS (in-process)' -ForegroundColor Green
    Write-Host 'Final status:       OFFLINE LOCAL FUT READY' -ForegroundColor Green
    exit 0
} catch {
    $status.final = 'FAILED'
    $status.detail += $_.Exception.Message
    Save-Status $status
    Write-Log ("ERROR: " + $_.Exception.Message) Red
    exit 1
}
