[CmdletBinding()]
param([string]$LauncherLogPath = '')

$ErrorActionPreference = 'Stop'
$ProjectRoot = $PSScriptRoot
$RuntimeRoot = Join-Path $ProjectRoot 'runtime'
$PidPath = Join-Path $RuntimeRoot 'launcher-processes.json'
$VendorRoot = Join-Path $ProjectRoot 'runtime-source'
$HostsPath = Join-Path $env:SystemRoot 'System32\drivers\etc\hosts'
$Pattern = '^\s*127\.0\.0\.1\s+spring18\.gosredirector\.(?:ea\.com|(?:stest|scert|sdev)\.ea\.com)\s*$'

function Test-ProcessOwnsScript([object]$ProcessInfo, [string]$ScriptPath, [string]$RelativeScriptPath = '') {
    if (-not $ProcessInfo -or -not $ScriptPath -or -not $ProcessInfo.CommandLine) { return $false }
    try {
        $expected = [IO.Path]::GetFullPath($ScriptPath).Replace('/', '\').ToLowerInvariant()
        $actual = ([string]$ProcessInfo.CommandLine).Replace('/', '\').ToLowerInvariant()
        if ($actual.Contains($expected)) { return $true }
        if ($RelativeScriptPath -and [string]$ProcessInfo.Name -match '^python(?:3)?(?:\.exe)?$') {
            $relative = $RelativeScriptPath.Replace('/', '\').ToLowerInvariant()
            $argumentPattern = '(?:^|[\s"])' + [regex]::Escape($relative) + '(?:[\s"]|$)'
            return $actual -match $argumentPattern
        }
        return $false
    } catch {
        return $false
    }
}

function Test-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-Administrator)) {
    $arguments = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ('"{0}"' -f $PSCommandPath))
    if ($LauncherLogPath) { $arguments += @('-LauncherLogPath', ('"{0}"' -f $LauncherLogPath)) }
    $elevated = Start-Process -FilePath 'powershell.exe' -Verb RunAs -ArgumentList $arguments -WindowStyle Hidden -Wait -PassThru
    exit $elevated.ExitCode
}

$transcribing = $false
try {
if ($LauncherLogPath) { Start-Transcript -LiteralPath $LauncherLogPath -Append -Force | Out-Null; $transcribing = $true }
if (Test-Path -LiteralPath $PidPath) {
    $tracked = Get-Content -LiteralPath $PidPath -Raw | ConvertFrom-Json
    $trackedScripts = @(
        [pscustomobject]@{ Name = 'serverPid'; FullPath = (Join-Path $VendorRoot 'localfut21\server.py'); RelativePath = 'localfut21\server.py' },
        [pscustomobject]@{ Name = 'redirectorPid'; FullPath = (Join-Path $VendorRoot 'redirector_tls.py'); RelativePath = 'redirector_tls.py' }
    )
    foreach ($trackedScript in $trackedScripts) {
        $id = [int]$tracked.($trackedScript.Name)
        $processInfo = if ($id -gt 0) { Get-CimInstance Win32_Process -Filter "ProcessId=$id" -ErrorAction SilentlyContinue } else { $null }
        if ($processInfo -and (Test-ProcessOwnsScript $processInfo $trackedScript.FullPath $trackedScript.RelativePath)) {
            Stop-Process -Id $id -Force
            Write-Host "Stopped tracked $($trackedScript.Name) process $id."
        }
    }
    Remove-Item -LiteralPath $PidPath -Force
} else {
    Write-Host "No tracked launcher processes were found; checking for this launcher's orphaned local-runtime processes."
}

# A failed start can occur after the services are created but before their PIDs
# are saved. Restrict fallback cleanup to Python processes whose command line
# names one of this project's two runtime entry points.
$ownedScripts = @(
    [pscustomobject]@{ FullPath = (Join-Path $VendorRoot 'localfut21\server.py'); RelativePath = 'localfut21\server.py' },
    [pscustomobject]@{ FullPath = (Join-Path $VendorRoot 'redirector_tls.py'); RelativePath = 'redirector_tls.py' }
)
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | ForEach-Object {
    $processInfo = $_
    $owned = $ownedScripts | Where-Object { Test-ProcessOwnsScript $processInfo $_.FullPath $_.RelativePath }
    if ($owned) {
        Stop-Process -Id $processInfo.ProcessId -Force
        Write-Host "Stopped orphaned local-runtime process $($processInfo.ProcessId)."
    }
}

$lines = @(Get-Content -LiteralPath $HostsPath -ErrorAction Stop)
if ($lines | Where-Object { $_ -match $Pattern }) {
    $lines | Where-Object { $_ -notmatch $Pattern } | Set-Content -LiteralPath $HostsPath -Encoding ascii
    ipconfig /flushdns | Out-Null
    Write-Host "Removed this launcher's FIFA redirector hosts entry."
} else {
    Write-Host 'No FIFA redirector hosts entry was present.'
}
} catch {
    Write-Host "ERROR: $($_.Exception.Message)"
    exit 1
} finally {
    if ($transcribing) { Stop-Transcript | Out-Null }
}
