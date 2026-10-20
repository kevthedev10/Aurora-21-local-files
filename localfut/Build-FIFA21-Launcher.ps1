[CmdletBinding()]
param([switch]$LauncherOnly, [switch]$IfNeeded)

$ErrorActionPreference = 'Stop'
$ProjectRoot = $PSScriptRoot
$LauncherProject = Join-Path $ProjectRoot 'LocalFUTLauncher.csproj'
$LauncherOutput = Join-Path $ProjectRoot 'Project21-LocalFUT-Launcher.exe'
$PublishedLauncher = Join-Path $ProjectRoot 'bin\Release\net8.0-windows\Project21-LocalFUT-Launcher.exe'
$AuthSource = Join-Path $ProjectRoot 'FIFA21-LocalAuthBridge.cs'
$AuthOutput = Join-Path $ProjectRoot 'Project21-LocalAuthBridge.exe'

$launcherSources = @(
    $LauncherProject,
    (Join-Path $ProjectRoot 'LocalFUTLauncher.App.xaml'),
    (Join-Path $ProjectRoot 'LocalFUTLauncher.App.xaml.cs'),
    (Join-Path $ProjectRoot 'LocalFUTLauncher.MainWindow.xaml'),
    (Join-Path $ProjectRoot 'LocalFUTLauncher.MainWindow.xaml.cs'),
    (Join-Path $ProjectRoot 'LocalFUTLauncher.RuntimeCompatibility.cs'),
    (Join-Path $ProjectRoot 'assets\FIFASetup.png')
)
foreach ($source in $launcherSources + @($AuthSource)) {
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { throw "Launcher source was not found: $source" }
}

if (Get-Process -Name 'Project21-LocalFUT-Launcher' -ErrorAction SilentlyContinue) {
    throw 'Project21-LocalFUT-Launcher is running. Close the launcher before rebuilding it.'
}
if (-not $LauncherOnly -and (Get-Process -Name 'Project21-LocalAuthBridge' -ErrorAction SilentlyContinue)) {
    throw 'Project21-LocalAuthBridge is running. Stop LocalFUT before rebuilding the auth bridge.'
}

$launcherNewest = ($launcherSources | Get-Item | Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1).LastWriteTimeUtc
$launcherStale = -not (Test-Path -LiteralPath $LauncherOutput -PathType Leaf) -or
    (Get-Item -LiteralPath $LauncherOutput).LastWriteTimeUtc -lt $launcherNewest
if (-not $IfNeeded -or $launcherStale) {
    & dotnet build $LauncherProject -c Release --nologo
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $PublishedLauncher -PathType Leaf)) {
        throw "WPF launcher compilation failed with exit code $LASTEXITCODE."
    }
    foreach ($artifact in @(
        'Project21-LocalFUT-Launcher.exe',
        'Project21-LocalFUT-Launcher.dll',
        'Project21-LocalFUT-Launcher.deps.json',
        'Project21-LocalFUT-Launcher.runtimeconfig.json'
    )) {
        $publishedArtifact = Join-Path (Split-Path -Parent $PublishedLauncher) $artifact
        if (-not (Test-Path -LiteralPath $publishedArtifact -PathType Leaf)) {
            throw "WPF launcher build did not produce $artifact."
        }
        Copy-Item -LiteralPath $publishedArtifact -Destination (Join-Path $ProjectRoot $artifact) -Force
    }
    Write-Host "Built: $LauncherOutput"
}
if ($LauncherOnly) { return }

$authStale = -not (Test-Path -LiteralPath $AuthOutput -PathType Leaf) -or
    (Get-Item -LiteralPath $AuthOutput).LastWriteTimeUtc -lt (Get-Item -LiteralPath $AuthSource).LastWriteTimeUtc
if ($IfNeeded -and -not $authStale) { return }

$compilerCandidates = @(
    (Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'),
    (Join-Path $env:WINDIR 'Microsoft.NET\Framework\v4.0.30319\csc.exe')
)
$compiler = @($compilerCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1)[0]
if (-not $compiler) { throw 'The .NET Framework C# compiler was not found.' }

& $compiler /nologo /target:exe /platform:x64 /optimize+ /reference:System.Web.Extensions.dll /reference:System.dll /reference:System.Core.dll ("/out:{0}" -f $AuthOutput) $AuthSource
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $AuthOutput -PathType Leaf)) {
    throw "Local auth bridge compilation failed with exit code $LASTEXITCODE."
}
Write-Host "Built: $AuthOutput"
