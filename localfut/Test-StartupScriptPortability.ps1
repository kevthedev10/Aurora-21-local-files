[CmdletBinding()]
param([ValidatePattern('^[0-9a-fA-F]{7,40}$')][string]$SourceRevision)

# Read source/AST only, except the command-free ownership predicate below.
# Never run the launcher, UAC, Python, a compiler, or a game/server process.
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path $PSScriptRoot -Parent
$script:checks = 0
$script:failures = @()

function Assert-Portability($Condition, [string]$Message) {
    $script:checks++
    if (-not $Condition) { $script:failures += $Message }
}

function Read-Source([string]$Name) {
    if (-not $SourceRevision) { return [IO.File]::ReadAllText((Join-Path $PSScriptRoot $Name)) }
    $lines = @(& git -C $repoRoot show "${SourceRevision}:localfut/$Name")
    if ($LASTEXITCODE -ne 0) { throw "Cannot read $Name at $SourceRevision" }
    return $lines -join [Environment]::NewLine
}

function Parse-Source([string]$Source, [string]$Name) {
    $tokens = $null
    $errors = $null
    $ast = [Management.Automation.Language.Parser]::ParseInput($Source, [ref]$tokens, [ref]$errors)
    if ($errors.Count) { throw "Cannot parse ${Name}: $($errors[0].Message)" }
    return $ast
}

function Get-Commands($Ast, [string]$Name) {
    return @($Ast.FindAll({ param($node)
        $node -is [Management.Automation.Language.CommandAst] -and $node.GetCommandName() -ieq $Name
    }, $true))
}

function Get-Literals($Ast) {
    return @($Ast.FindAll({ param($node)
        $node -is [Management.Automation.Language.StringConstantExpressionAst]
    }, $true) | ForEach-Object { $_.Value })
}

function Get-Variables($Ast) {
    return @($Ast.FindAll({ param($node)
        $node -is [Management.Automation.Language.VariableExpressionAst]
    }, $true) | ForEach-Object { $_.VariablePath.UserPath })
}

function Get-Function($Ast, [string]$Name) {
    return @($Ast.FindAll({ param($node)
        $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -ieq $Name
    }, $true)) | Select-Object -First 1
}

function Get-Argument($Command, [string]$Name) {
    for ($i = 0; $i -lt $Command.CommandElements.Count; $i++) {
        $element = $Command.CommandElements[$i]
        if ($element -is [Management.Automation.Language.CommandParameterAst] -and $element.ParameterName -ieq $Name) {
            if ($element.Argument) { return $element.Argument }
            if ($i + 1 -lt $Command.CommandElements.Count) { return $Command.CommandElements[$i + 1] }
        }
    }
    return $null
}

try {
    $worker = Parse-Source (Read-Source 'Launch-LocalFUT.ps1') 'launch worker'
    $stop = Parse-Source (Read-Source 'Stop-LocalFUT.ps1') 'stop worker'
    $builder = Parse-Source (Read-Source 'Build-FIFA21-Launcher.ps1') 'builder'
    $offline = Parse-Source (Read-Source 'Offline-LocalFUT.ps1') 'offline checks'
    $gui = Read-Source 'FIFA21-LocalFUT-Launcher.cs'
    $run = Read-Source 'RUN-FIFA21.cmd'
    $workerParameters = @($worker.ParamBlock.Parameters | ForEach-Object { $_.Name.VariablePath.UserPath })
    $builderParameters = @($builder.ParamBlock.Parameters | ForEach-Object { $_.Name.VariablePath.UserPath })
    foreach ($name in @('UserConfigRoot', 'LauncherLogPath')) {
        Assert-Portability ($gui -match ('\s-' + $name + '\s')) "GUI must forward $name"
        Assert-Portability ($name -in $workerParameters) "Launch worker must accept GUI argument $name"
    }
    Assert-Portability ('LauncherLogPath' -in @($stop.ParamBlock.Parameters | ForEach-Object { $_.Name.VariablePath.UserPath })) 'Stop worker must accept the GUI log path'
    Assert-Portability ($run -match '(?im)^\s*powershell(?:\.exe)?\b[^\r\n]*-IfNeeded\b') 'RUN must use the incremental builder entrypoint'
    foreach ($name in @('IfNeeded', 'LauncherOnly')) {
        Assert-Portability ($name -in $builderParameters) "Builder must support $name"
    }
    $buildLiterals = Get-Literals $builder
    foreach ($name in @('LauncherSettings.cs', 'LauncherTheme.cs', 'FIFA21-LocalAuthBridge.cs')) {
        Assert-Portability ($name -in $buildLiterals) "Builder must include $name"
    }
    foreach ($reference in @('System.Web.Extensions.dll', 'System.Xml.dll', 'WindowsBase.dll', 'PresentationCore.dll')) {
        Assert-Portability (@($buildLiterals | Where-Object { $_.EndsWith($reference, [StringComparison]::OrdinalIgnoreCase) }).Count -gt 0) "Builder must reference $reference"
    }

    $settingsCalls = Get-Commands $worker 'Get-LocalFutUserSettings'
    Assert-Portability ($settingsCalls.Count -eq 1 -and 'UserConfigRoot' -in (Get-Variables $settingsCalls[0])) 'Worker must load defaults/user settings using the resolved user-config root'
    $uacCalls = @(Get-Commands $worker 'Start-Process' | Where-Object {
        $verb = Get-Argument $_ 'Verb'
        $verb -and $verb.Extent.Text -match '^[''\"]?RunAs[''\"]?$'
    })
    Assert-Portability ($uacCalls.Count -eq 1) 'Worker must have one explicit UAC relaunch'
    if ($uacCalls.Count -eq 1) {
        $uac = $uacCalls[0]
        $argumentList = Get-Argument $uac 'ArgumentList'
        Assert-Portability ($argumentList -is [Management.Automation.Language.VariableExpressionAst]) 'UAC must receive the constructed argument list'
        if ($argumentList -is [Management.Automation.Language.VariableExpressionAst]) {
            foreach ($name in @('UserConfigRoot', 'LauncherLogPath')) {
                $forwarded = @($worker.FindAll({ param($node)
                    $node -is [Management.Automation.Language.AssignmentStatementAst] -and
                    $node.Left -is [Management.Automation.Language.VariableExpressionAst] -and
                    $node.Left.VariablePath.UserPath -ieq $argumentList.VariablePath.UserPath -and
                    $node.Extent.EndOffset -lt $uac.Extent.StartOffset -and
                    ('-' + $name) -in (Get-Literals $node.Right) -and $name -in (Get-Variables $node.Right)
                }, $true))
                Assert-Portability ($forwarded.Count -gt 0) "UAC must preserve the $name value"
            }
        }
    }
    foreach ($name in @('Resolve-GamePath', 'Resolve-EALauncherPath')) {
        $resolver = Get-Function $worker $name
        Assert-Portability ($null -ne $resolver) "Worker must define $name"
        if ($resolver) {
            Assert-Portability (@(Get-Literals $resolver | Where-Object { $_ -match '^[A-Za-z]:[\\/]' }).Count -eq 0) "$name must not hard-code a drive-specific installation path"
            Assert-Portability (@(Get-Variables $resolver | Where-Object { $_ -like 'env:ProgramFiles*' }).Count -gt 0) "$name must use the current machine's Program Files location"
        }
    }
    foreach ($name in @('Ensure-LocalAuthBridge', 'Invoke-LocalAuthBridge')) {
        Assert-Portability ($null -ne (Get-Function $worker $name) -and (Get-Commands $worker $name).Count -gt 0) "Worker must include and call $name"
    }
    $offlineLiterals = Get-Literals $offline
    foreach ($name in @('config.example.json', 'user_config.py')) {
        Assert-Portability ($name -in $offlineLiterals) "Offline verification must use shipped $name"
    }
    Assert-Portability ('config.json' -notin $offlineLiterals) 'Offline verification must not require an untracked config.json'

    $ownership = Get-Function $worker 'Test-ProcessOwnsExactScript'
    Assert-Portability ($null -ne $ownership) 'Worker must define exact-script process ownership'
    Assert-Portability ((Get-Commands $worker 'Test-ProcessOwnsExactScript').Count -ge 3) 'Server/reuse/redirector checks must use exact-script ownership'
    Assert-Portability ((Get-Commands $worker 'Test-ProcessOwnsScript').Count -eq 0) 'Worker must not use the old relative-filename ownership fallback'
    if ($ownership) {
        $unsafeNodes = @($ownership.Body.FindAll({ param($node)
            $node -is [Management.Automation.Language.CommandAst] -or
            ($node -is [Management.Automation.Language.InvokeMemberExpressionAst] -and
             $node.Member.Extent.Text -notin @('Escape', 'GetFullPath', 'Replace'))
        }, $true))
        if ($unsafeNodes.Count) { throw 'Ownership predicate is no longer command-free/path-only; review before executing fixtures' }
        $predicate = [scriptblock]::Create($ownership.Extent.Text + [Environment]::NewLine + 'Test-ProcessOwnsExactScript $args[0] $args[1]')
        $expected = 'C:\Portable Copy A\localfut\runtime-source\localfut21\server.py'
        $fixtures = @(
            @{ Name = 'quoted absolute script'; Process = 'python.exe'; Line = '"C:\Python\python.exe" "' + $expected + '"'; Expected = $true },
            @{ Name = 'unbuffered Python'; Process = 'python.exe'; Line = 'python.exe -u "' + $expected + '" --lsx-mode off'; Expected = $true },
            @{ Name = 'slash normalization'; Process = 'python3.exe'; Line = 'python.exe "' + $expected.Replace('\', '/') + '"'; Expected = $true },
            @{ Name = 'relative script from another clone'; Process = 'python.exe'; Line = 'python.exe localfut21\server.py'; Expected = $false },
            @{ Name = 'same filename in another clone'; Process = 'python.exe'; Line = 'python.exe "C:\Portable Copy B\localfut\runtime-source\localfut21\server.py"'; Expected = $false },
            @{ Name = 'expected path is only a later argument'; Process = 'python.exe'; Line = 'python.exe other.py --log "' + $expected + '"'; Expected = $false },
            @{ Name = 'script path prefix'; Process = 'python.exe'; Line = 'python.exe "' + $expected + '.bak"'; Expected = $false },
            @{ Name = 'non-Python process'; Process = 'powershell.exe'; Line = 'powershell.exe "' + $expected + '"'; Expected = $false },
            @{ Name = 'unknown command line'; Process = 'python.exe'; Line = $null; Expected = $false }
        )
        foreach ($fixture in $fixtures) {
            $processInfo = [pscustomobject]@{ Name = $fixture.Process; CommandLine = $fixture.Line }
            Assert-Portability ((& $predicate $processInfo $expected) -eq $fixture.Expected) "Ownership fixture: $($fixture.Name)"
        }
        Assert-Portability (-not (& $predicate $null $expected)) 'Ownership rejects an unknown process'
    }
    if ($script:failures.Count) {
        foreach ($failure in $script:failures) { Write-Host "FAIL: $failure" }
        throw "$($script:failures.Count) of $script:checks startup portability checks failed"
    }
    $sourceLabel = if ($SourceRevision) { "revision $SourceRevision" } else { 'working scripts' }
    Write-Host "PASS: $script:checks startup portability checks ($sourceLabel); no launcher, Python, UAC, game or server execution."
} catch {
    Write-Error $_ -ErrorAction Continue
    exit 1
}
