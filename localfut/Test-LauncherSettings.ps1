[CmdletBinding()]
param()

# No service startup, UAC, game launch, debugger, or visible UI.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'User-Settings.ps1')
$assemblyPath = Join-Path $PSScriptRoot 'Project21-LocalFUT-Launcher.exe'
if (-not (Test-Path -LiteralPath $assemblyPath)) { throw 'Build the launcher first.' }
$testRoot = Join-Path ([IO.Path]::GetTempPath()) ('Project21-Launcher-Tests-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $testRoot | Out-Null
$previousRoot = $env:LOCALFUT21_USER_CONFIG_ROOT
$checks = 0
function Assert-Launcher($condition, [string]$message) {
    if (-not $condition) { throw "FAIL: $message" }
    $script:checks++
    Write-Host "PASS: $message"
}

try {
    Add-Type -AssemblyName System.Windows.Forms, System.Drawing, System.Web.Extensions
    Add-Type -ReferencedAssemblies System.Drawing,System.Windows.Forms -TypeDefinition @'
using System.Collections.Generic;
using System.Drawing;
using System.Windows.Forms;
public static class LauncherTestValues {
    public static void Set(Dictionary<string, object> values, string key, string value) { values[key] = value; }
    public static void Render(Control root, string path) {
        Prepare(root);
        root.PerformLayout();
        using (Bitmap canvas = new Bitmap(root.ClientSize.Width, root.ClientSize.Height)) {
            using (Graphics graphics = Graphics.FromImage(canvas)) {
                graphics.Clear(root.BackColor);
                Children(root, graphics, Point.Empty);
            }
            canvas.Save(path);
        }
    }
    private static void Prepare(Control control) {
        System.IntPtr handle = control.Handle;
        Control[] children = new Control[control.Controls.Count];
        control.Controls.CopyTo(children, 0);
        foreach (Control child in children) Prepare(child);
        control.PerformLayout();
    }
    private static void Children(Control parent, Graphics graphics, Point origin) {
        System.Drawing.Drawing2D.GraphicsState state = graphics.Save();
        graphics.SetClip(new Rectangle(origin, parent.ClientSize), System.Drawing.Drawing2D.CombineMode.Intersect);
        // Hidden parent forms suppress descendant painting in DrawToBitmap;
        // composite each control explicitly without showing an OS window.
        Control[] children = new Control[parent.Controls.Count];
        parent.Controls.CopyTo(children, 0);
        System.Array.Reverse(children);
        foreach (Control child in children) {
            if (child.Width < 1 || child.Height < 1) continue;
            Point location = new Point(origin.X + child.Left, origin.Y + child.Top);
            using (Bitmap bitmap = new Bitmap(child.Width, child.Height)) {
                child.DrawToBitmap(bitmap, new Rectangle(Point.Empty, child.Size));
                graphics.DrawImageUnscaled(bitmap, location);
            }
            Children(child, graphics, location);
        }
        graphics.Restore(state);
    }
}
'@
    $assembly = [Reflection.Assembly]::LoadFrom($assemblyPath)
    $env:LOCALFUT21_USER_CONFIG_ROOT = Join-Path $testRoot 'User A'
    $store = New-Object Project21.LocalFutLauncher.LauncherSettingsStore($env:LOCALFUT21_USER_CONFIG_ROOT)
    $settings = $store.Load()
    Assert-Launcher ($settings['gamePath'] -eq '') 'First-run defaults contain no machine-specific game path'
    Assert-Launcher (Test-Path -LiteralPath $store.SettingsPath) 'Missing per-user settings are created'
    Assert-Launcher $store.NeedsGameSelection() 'Missing installation opens the file-picker path'
    Assert-Launcher ((Resolve-LocalFutUserConfigRoot '') -eq $store.Root) 'C# and PowerShell resolve the same user root'

    $gameDirectory = Join-Path $testRoot ('Game ' + [char]0x00e9)
    New-Item -ItemType Directory -Path $gameDirectory | Out-Null
    $gamePath = Join-Path $gameDirectory 'FIFA21.exe'
    [IO.File]::WriteAllText($gamePath, 'test fixture; never executed')
    Assert-Launcher ([Project21.LocalFutLauncher.LauncherSettingsStore]::ValidGamePath($gamePath)) 'Existing FIFA21.exe with spaces and Unicode is accepted'
    Assert-Launcher (-not [Project21.LocalFutLauncher.LauncherSettingsStore]::ValidGamePath('relative\FIFA21.exe')) 'Relative executable paths are rejected'
    Assert-Launcher (-not [Project21.LocalFutLauncher.LauncherSettingsStore]::ValidGamePath((Join-Path $testRoot 'missing\FIFA21.exe'))) 'Missing executable paths trigger setup'
    [LauncherTestValues]::Set($settings, 'gamePath', $gamePath)
    [LauncherTestValues]::Set($settings, 'futureAuthSetting', 'preserve me')
    $store.Save($settings)
    $saved = $store.Load()
    Assert-Launcher (-not $store.NeedsGameSelection()) 'A valid saved installation does not prompt again'
    Assert-Launcher ($saved['futureAuthSetting'] -eq 'preserve me') 'Unknown settings survive save/load'
    Assert-Launcher ((Get-LocalFutUserSettings $store.Root).gamePath -eq $gamePath) 'PowerShell reads the UTF-8 settings written by C#'
    Assert-Launcher (@(Get-ChildItem -LiteralPath $store.Root -Filter '*.tmp').Count -eq 0) 'Atomic saves leave no temporary files'

    $second = New-Object Project21.LocalFutLauncher.LauncherSettingsStore((Join-Path $testRoot 'User B'))
    Assert-Launcher ($second.Load()['gamePath'] -eq '') 'A second Windows profile does not inherit the first game path'
    $example = Join-Path $PSScriptRoot 'runtime-source\localfut21\config.example.json'
    $store.InitializeRuntimeConfig($example)
    $userConfig = Join-Path $store.Root 'config.json'
    Assert-Launcher (Test-Path -LiteralPath $userConfig) 'Runtime configuration is created outside the repository'
    [IO.File]::WriteAllText($userConfig, '{"credits":123}')
    $store.InitializeRuntimeConfig($example)
    Assert-Launcher ((Get-Content -LiteralPath $userConfig -Raw) -eq '{"credits":123}') 'Existing local runtime overrides are never overwritten'

    $bad = '{ broken JSON'
    [IO.File]::WriteAllText($second.SettingsPath, $bad)
    $rejected = $false
    try { $null = $second.Load() } catch { $rejected = $true }
    Assert-Launcher $rejected 'Malformed settings report an error instead of silently resetting'
    Assert-Launcher ([IO.File]::ReadAllText($second.SettingsPath) -eq $bad) 'Malformed settings remain recoverable'
    $backup = $second.PrepareForSetup()
    Assert-Launcher ([IO.File]::ReadAllText($backup) -eq $bad) 'First-run recovery preserves malformed JSON in a separate backup'
    Assert-Launcher $second.NeedsGameSelection() 'Recovered settings require an explicit installation selection'
    [IO.File]::Delete($gamePath)
    Assert-Launcher $store.NeedsGameSelection() 'A moved/deleted installation opens selection again'

    $scriptOnly = Join-Path $testRoot 'Script user'
    $fromScript = Get-LocalFutUserSettings $scriptOnly
    $fromCs = New-Object Project21.LocalFutLauncher.LauncherSettingsStore($scriptOnly)
    Assert-Launcher ($fromCs.Load()['lsxMode'] -eq $fromScript.lsxMode) 'C# can read first-run settings written by PowerShell'

    foreach ($file in @('Launch-LocalFUT.ps1', 'Stop-LocalFUT.ps1', 'User-Settings.ps1', 'Build-FIFA21-Launcher.ps1', 'Offline-LocalFUT.ps1')) {
        $tokens = $null; $errors = $null
        $null = [Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot $file), [ref]$tokens, [ref]$errors)
        Assert-Launcher ($errors.Count -eq 0) "$file parses without errors"
    }

    # Construct and paint the real forms off-screen. Never Show/ShowDialog them.
    [Windows.Forms.Application]::EnableVisualStyles()
    $env:LOCALFUT21_USER_CONFIG_ROOT = Join-Path $testRoot 'Preview user'
    $formType = $assembly.GetType('Project21.LocalFutLauncher.LauncherForm', $true)
    $flags = [Reflection.BindingFlags]'Instance,NonPublic'
    $form = [Activator]::CreateInstance($formType, $flags, $null, [object[]]@($true), $null)
    try {
        [LauncherTestValues]::Render($form, (Join-Path $testRoot 'launcher-preview.png'))
        Assert-Launcher (-not $form.Visible) 'Main window renders off-screen without launching anything'
        $logControl = $formType.GetField('outputBox', $flags).GetValue($form)
        $logsButton = $formType.GetField('logsButton', $flags).GetValue($form)
        Assert-Launcher ($logControl.WordWrap -and $logControl.Multiline) 'Long activity lines wrap instead of being cut off horizontally'
        Assert-Launcher ($logControl.Height -ge 240) 'Condensed layout gives the activity output more than twice its previous height'
        foreach ($client in @([Drawing.Size]::new(860, 700), [Drawing.Size]::new(1120, 850))) {
            $form.ClientSize = $client
            $form.PerformLayout()
            $logControl.Parent.PerformLayout()
            Assert-Launcher ($logControl.Bounds.Bottom -le $logControl.Parent.ClientSize.Height - 12 -and $logControl.Height -ge 210) "Log fits inside its panel at $client"
            Assert-Launcher ($logsButton.Bounds.Bottom -lt $logControl.Top) "Log header does not overlap output at $client"
        }
        $form.ClientSize = [Drawing.Size]::new(860, 700)
        $logControl.Text = ('Preview only: a long diagnostic path remains readable: C:\Example installation with spaces\Project21\LocalFUT\logs\launcher-session.log' + [Environment]::NewLine) * 10
        [LauncherTestValues]::Render($form, (Join-Path $testRoot 'launcher-minimum-log-preview.png'))
    } finally { $form.Dispose() }

    $previewStore = New-Object Project21.LocalFutLauncher.LauncherSettingsStore($env:LOCALFUT21_USER_CONFIG_ROOT)
    $dialogType = $assembly.GetType('Project21.LocalFutLauncher.LauncherSettingsDialog', $true)
    $constructor = $dialogType.GetConstructors($flags)[0]
    $dialog = $constructor.Invoke([object[]]@($previewStore.PSObject.BaseObject))
    try {
        [LauncherTestValues]::Render($dialog, (Join-Path $testRoot 'settings-preview.png'))
        Assert-Launcher (-not $dialog.Visible) 'First-run settings dialog renders off-screen'
    } finally { $dialog.Dispose() }
    Write-Host "$checks checks passed. Test fixtures and previews: $testRoot"
} finally {
    $env:LOCALFUT21_USER_CONFIG_ROOT = $previousRoot
}
