[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateRange(1, 2147483647)]
    [Int64]$ResourceId,
    [ValidateSet('club', 'unassigned')]
    [string]$Pile = 'club',
    [ValidateRange(1, 2147483647)]
    [Int64]$ReplaceResourceId,
    [switch]$AllowDuplicate
)

$ErrorActionPreference = 'Stop'

if ($env:LOCALFUT_RUNTIME_ROOT) {
    throw 'Unset LOCALFUT_RUNTIME_ROOT before granting a card to the normal local club.'
}

$source = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot 'runtime-source\localfut21\server.py')).Path
$database = Join-Path $env:LOCALAPPDATA 'FIFA21LocalFUT\data\localfut15.sqlite3'
if (-not (Test-Path -LiteralPath $database -PathType Leaf)) {
    throw "No local club database exists at $database. Create your LocalFUT club once before granting cards."
}

if (Get-Process -Name FIFA21 -ErrorAction SilentlyContinue) {
    throw 'FIFA 21 is running. Close it before an offline card grant so it cannot retain stale club state.'
}

foreach ($port in 8090, 44321) {
    if (Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue) {
        throw "A LocalFUT service is listening on port $port. Stop LocalFUT before an offline card grant."
    }
}

$python = Get-Command py -ErrorAction Stop
$arguments = @('-3', $source, '--grant-card', $ResourceId, '--grant-pile', $Pile)
if ($PSBoundParameters.ContainsKey('ReplaceResourceId')) { $arguments += @('--replace-resource-id', $ReplaceResourceId) }
if ($AllowDuplicate) { $arguments += '--allow-duplicate' }
& $python.Source @arguments
if ($LASTEXITCODE -ne 0) { throw "Offline grant failed with exit code $LASTEXITCODE." }
