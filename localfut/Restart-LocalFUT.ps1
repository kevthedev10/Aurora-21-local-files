[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$stopScript = Join-Path $projectRoot 'Stop-LocalFUT.ps1'
$launcherPath = Join-Path $projectRoot 'Project21-LocalFUT-Launcher.exe'

if (-not (Test-Path -LiteralPath $stopScript -PathType Leaf)) { throw "Stop script was not found: $stopScript" }
if (-not (Test-Path -LiteralPath $launcherPath -PathType Leaf)) { throw "Launcher was not found: $launcherPath" }

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    $elevated = Start-Process -FilePath 'powershell.exe' -Verb RunAs -ArgumentList @(
        '-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ('"{0}"' -f $PSCommandPath)
    ) -WindowStyle Hidden -Wait -PassThru
    exit $elevated.ExitCode
}

# Stop-LocalFUT owns the tracked server/redirector processes and hosts entry.
& powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File $stopScript
if ($LASTEXITCODE -ne 0) { throw "LocalFUT stop failed with exit code $LASTEXITCODE." }

# FIFA21.exe is the only game process in scope. Do not terminate EA App or
# unrelated FIFA-named tools; the launcher can start the game again normally.
Get-Process -Name 'FIFA21' -ErrorAction SilentlyContinue | ForEach-Object {
    Stop-Process -Id $_.Id -Force
    Write-Host "Stopped FIFA21 process $($_.Id)."
}

Start-Process -FilePath $launcherPath -WorkingDirectory $projectRoot
Write-Host "Relaunched Project 21 LocalFUT Launcher."
