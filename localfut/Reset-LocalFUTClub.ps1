[CmdletBinding()]
param([switch]$ResetClub, [switch]$Elevated)

# Destructive by explicit request only. Never touches mined data or preferences.
$ErrorActionPreference = 'Stop'
if (-not $ResetClub) { throw 'This permanently deletes the local test club without a backup. Pass -ResetClub explicitly.' }
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    if ($Elevated) { throw 'Administrator permission was not granted.' }
    $arguments = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ('"{0}"' -f $PSCommandPath), '-ResetClub', '-Elevated')
    $child = Start-Process -FilePath 'powershell.exe' -ArgumentList $arguments -Verb RunAs -WindowStyle Hidden -PassThru -Wait
    exit $child.ExitCode
}

$logPath = Join-Path $PSScriptRoot 'runtime\club-reset.log'
New-Item -ItemType Directory -Path (Split-Path -Parent $logPath) -Force | Out-Null
Start-Transcript -LiteralPath $logPath -Append | Out-Null
try {
    if ($env:LOCALFUT_RUNTIME_ROOT) { throw 'Unset the test runtime override before resetting the normal club.' }
    $source = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot 'runtime-source\localfut21\server.py')).Path
    $dataRoot = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'FIFA21LocalFUT\data'))
    $database = Join-Path $dataRoot 'localfut15.sqlite3'
    $targets = @($database, "$database-wal", "$database-shm")
    foreach ($target in $targets) {
        if ([IO.Path]::GetDirectoryName([IO.Path]::GetFullPath($target)) -ne $dataRoot) {
            throw "Unsafe database target: $target"
        }
        if (Test-Path -LiteralPath $target) {
            $entry = Get-Item -LiteralPath $target
            if ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Refusing linked database: $target" }
        }
    }
    foreach ($directory in @($dataRoot, (Split-Path -Parent $dataRoot))) {
        if ((Test-Path -LiteralPath $directory) -and ((Get-Item -LiteralPath $directory).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw "Refusing linked runtime directory: $directory"
        }
    }
    $listeners = @(Get-NetTCPConnection -State Listen -LocalPort 8090,44321 -ErrorAction SilentlyContinue)
    $serverIds = @($listeners.OwningProcess | Sort-Object -Unique)
    if ($serverIds.Count -gt 1) { throw 'FUT and Blaze ports have different owners; refusing reset.' }
    foreach ($serverId in $serverIds) {
        $owner = Get-CimInstance Win32_Process -Filter "ProcessId=$serverId"
        $scriptPattern = '(?i)(?:^|\s)"?' + [regex]::Escape($source) + '"?(?=\s|$)'
        if (-not $owner.CommandLine -or $owner.CommandLine -notmatch $scriptPattern) {
            throw "PID $serverId is not this repository server; refusing reset."
        }
    }
    # Shut the known game down so it cannot retain the previous club in memory.
    $allowedGames = @('C:\Program Files\EA Games\FIFA 21\FIFA21.exe', 'C:\Games\FIFA 21\FIFA21.exe')
    foreach ($game in @(Get-CimInstance Win32_Process -Filter "Name='FIFA21.exe'")) {
        if ($allowedGames -notcontains $game.ExecutablePath) { throw "Unrecognized FIFA process $($game.ProcessId); refusing to close it." }
        Write-Host "Closing FIFA21 PID $($game.ProcessId) to clear cached club state."
        Stop-Process -Id $game.ProcessId -Force
    }
    foreach ($serverId in $serverIds) {
        Write-Host "Stopping verified local server PID $serverId."
        Stop-Process -Id $serverId -Force
        Wait-Process -Id $serverId -Timeout 15 -ErrorAction SilentlyContinue
    }
    if (Get-NetTCPConnection -State Listen -LocalPort 8090,44321 -ErrorAction SilentlyContinue) {
        throw 'A service reacquired the runtime ports; refusing database deletion.'
    }
    foreach ($target in $targets) {
        if (Test-Path -LiteralPath $target -PathType Leaf) {
            Remove-Item -LiteralPath $target -Force
            Write-Host "Deleted $target (no backup requested)."
        }
    }
    Write-Host 'Club reset complete. Restarting the repository services; EA App remains open.'
    & (Join-Path $PSScriptRoot 'Launch-LocalFUT.ps1') -NoGameLaunch -LSXMode ea-app
    exit $LASTEXITCODE
} catch {
    Write-Host "RESET FAILED: $($_.Exception.Message)"
    Stop-Transcript -ErrorAction SilentlyContinue | Out-Null
    exit 1
}
