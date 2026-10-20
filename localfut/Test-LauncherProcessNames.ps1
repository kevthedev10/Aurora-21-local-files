[CmdletBinding()]
param([switch]$SelfTest)

# Read-only: never load the application assemblies or start any processes.
# fifaconfig.exe SHA256 62311EE31BDAC7759016C47EB570D5A165741A755FBFAA440A49EDB2D1A09135:
# PlayButton_Click token 0x0600006C returns when IsAppAlreadyRunning (0x06000075)
# finds ANY process whose name StartsWith("fifa21", OrdinalIgnoreCase).
$ErrorActionPreference = 'Stop'
$script:checks = 0

function Assert-ProcessName($Condition, [string]$Message) {
    if (-not $Condition) { throw "FAIL: $Message" }
    $script:checks++
}

function Test-ExecutableName([string]$Path, [ValidateSet('Helper', 'Game')][string]$Role = 'Helper') {
    if ([IO.Path]::GetExtension($Path) -ine '.exe') { return $false }
    $name = [IO.Path]::GetFileNameWithoutExtension($Path)
    if ($Role -eq 'Game') {
        return [string]::Equals($name, 'FIFA21', [StringComparison]::OrdinalIgnoreCase)
    }
    return $name.Length -gt 0 -and -not $name.StartsWith('fifa21', [StringComparison]::OrdinalIgnoreCase)
}

function Get-ConfiguredExecutable([string]$Source, [string]$Variable, [string]$Label, [switch]$Optional) {
    $parseTokens = $null
    $parseErrors = $null
    $syntax = [Management.Automation.Language.Parser]::ParseInput($Source, [ref]$parseTokens, [ref]$parseErrors)
    if ($parseErrors.Count) { throw "Cannot parse $Label" }
    $assignments = @($syntax.FindAll({
        param($node)
        $node -is [Management.Automation.Language.AssignmentStatementAst] -and
        $node.Left -is [Management.Automation.Language.VariableExpressionAst] -and
        $node.Left.VariablePath.UserPath -ieq $Variable
    }, $true))
    if ($Optional -and $assignments.Count -eq 0) { return $null }
    if ($assignments.Count -ne 1) { throw "Expected one $Variable assignment in $Label" }
    $literals = @($assignments[0].Right.FindAll({
        param($node)
        $node -is [Management.Automation.Language.StringConstantExpressionAst] -and
        $node.Value.EndsWith('.exe', [StringComparison]::OrdinalIgnoreCase)
    }, $true))
    if ($literals.Count -ne 1) { throw "Expected one literal executable path for $Variable in $Label" }
    return $literals[0].Value
}

function Assert-ConfiguredName([string]$Name, [string]$Expected, [string]$Label) {
    Assert-ProcessName (Test-ExecutableName $Name) "$Label collides with fifaconfig's game-process prefix"
    Assert-ProcessName ([string]::Equals($Name, $Expected, [StringComparison]::OrdinalIgnoreCase)) "$Label disagrees with the configured build artifact"
}

try {
    foreach ($oldName in @('FIFA21-LocalFUT-Launcher.exe', 'FIFA21-LocalAuthBridge.exe', 'fIfA21-helper.exe', 'FIFA21.exe')) {
        Assert-ProcessName (-not (Test-ExecutableName $oldName)) "Reject colliding helper fixture: $oldName"
    }
    foreach ($safeName in @('Project21-LocalFUT-Launcher.exe', 'Project21-LocalAuthBridge.exe', 'C:\FIFA21\Project21-helper.exe')) {
        Assert-ProcessName (Test-ExecutableName $safeName) "Accept noncolliding helper fixture: $safeName"
    }
    Assert-ProcessName (Test-ExecutableName 'C:\Games\FIFA 21\fIfA21.exe' 'Game') 'The actual game remains allowed'
    Assert-ProcessName (-not (Test-ExecutableName 'FIFA21-LocalAuthBridge.exe' 'Game')) 'A helper cannot masquerade as the actual game'
    Assert-ProcessName (-not (Test-ExecutableName 'Project21-helper.cmd')) 'Require an executable artifact'
    $fixtureName = Get-ConfiguredExecutable '$OutputPath = Join-Path $PSScriptRoot ''FIFA21-LocalFUT-Launcher.exe''' 'OutputPath' 'old-name fixture'
    Assert-ProcessName (-not (Test-ExecutableName $fixtureName)) 'Reject an old name extracted from a real assignment shape'
    $absentBridge = Get-ConfiguredExecutable '$OutputPath = ''Project21-LocalFUT-Launcher.exe''' 'AuthOutputPath' 'launcher-only fixture' -Optional
    Assert-ProcessName ($null -eq $absentBridge) 'A launcher-only build does not require an auth bridge'
    if ($SelfTest) {
        Write-Host "PASS: $script:checks process-name fixtures; no application processes started."
        exit 0
    }

    $launcherName = 'Project21-LocalFUT-Launcher.exe'
    $bridgeName = 'Project21-LocalAuthBridge.exe'
    $bridgeConfigured = $false
    $bridgeLaunchName = $null
    $configured = @(
        @{ File = 'Build-FIFA21-Launcher.ps1'; Variable = 'OutputPath'; Expected = $launcherName },
        @{ File = 'Build-FIFA21-Launcher.ps1'; Variable = 'AuthOutputPath'; Expected = $bridgeName; Optional = $true },
        @{ File = 'Launch-LocalFUT.ps1'; Variable = 'outputPath'; Expected = $bridgeName; Optional = $true },
        @{ File = 'Test-LauncherSettings.ps1'; Variable = 'assemblyPath'; Expected = $launcherName }
    )
    foreach ($entry in $configured) {
        $source = [IO.File]::ReadAllText((Join-Path $PSScriptRoot $entry.File))
        $name = Get-ConfiguredExecutable $source $entry.Variable $entry.File -Optional:([bool]$entry.Optional)
        if ($null -eq $name) { continue }
        Assert-ConfiguredName $name $entry.Expected "$($entry.File):$($entry.Variable)"
        if ($entry.Variable -eq 'AuthOutputPath') { $bridgeConfigured = $true }
        if ($entry.File -eq 'Launch-LocalFUT.ps1') { $bridgeLaunchName = $name }
    }
    if ($bridgeConfigured) { Assert-ProcessName ($null -ne $bridgeLaunchName) 'The configured auth bridge also has a launch path' }
    $runSource = [IO.File]::ReadAllText((Join-Path $PSScriptRoot 'RUN-FIFA21.cmd'))
    $runMatches = [regex]::Matches($runSource, '(?im)^\s*set\s+"LAUNCHER=%SCRIPT_DIR%([^"\r\n]+)"\s*$')
    Assert-ProcessName ($runMatches.Count -eq 1) 'RUN-FIFA21.cmd has one explicit launcher path'
    Assert-ConfiguredName $runMatches[0].Groups[1].Value $launcherName 'RUN-FIFA21.cmd:LAUNCHER'

    $artifactNames = @($launcherName)
    if ($bridgeConfigured) { $artifactNames += $bridgeName }
    foreach ($name in $artifactNames) {
        $artifactPath = Join-Path $PSScriptRoot $name
        Assert-ProcessName (Test-Path -LiteralPath $artifactPath -PathType Leaf) "Build artifact is missing: $name"
        $artifact = Get-Item -LiteralPath $artifactPath
        Assert-ProcessName (Test-ExecutableName $artifact.FullName) "Built artifact collides: $($artifact.Name)"
        Assert-ProcessName ($artifact.Length -gt 0) "Build artifact is empty: $name"
    }
    Write-Host "PASS: $script:checks checks; configured launcher/helper paths and $($artifactNames.Count) built artifacts are collision-free."
} catch {
    Write-Error $_ -ErrorAction Continue
    exit 1
}
