using Microsoft.Win32;
using System.Diagnostics;
using System.IO;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Threading;

namespace Project21Launcher;

public partial class MainWindow : Window
{
    private readonly RuntimeSnapshot runtimeSnapshot;
    private readonly DispatcherTimer statusTimer;
    private Process? startProcess;
    private Process? stopProcess;
    private Process? restartProcess;
    private Process? cardGrantProcess;
    private Process? customisationGrantProcess;
    private readonly List<CardCatalogEntry> cardCatalog = [];
    private bool cardCatalogLoaded;

    public MainWindow()
    {
        InitializeComponent();
        runtimeSnapshot = RuntimeCompatibility.Discover();
        ApplyRuntimeSnapshot(runtimeSnapshot);
        LoadSettings();
        statusTimer = new DispatcherTimer { Interval = TimeSpan.FromSeconds(1) };
        statusTimer.Tick += (_, _) => RefreshRuntimeStatus();
        statusTimer.Start();
    }

    private void LoadSettings()
    {
        LauncherSettingsSnapshot settings = RuntimeCompatibility.LoadSettings(runtimeSnapshot.Root);
        GamePathTextBox.Text = settings.GamePath;
        RuntimeSourceTextBox.Text = settings.SourcePath;
        ClubNameTextBox.Text = settings.ClubName;
        ClubAbbreviationTextBox.Text = settings.ClubAbbreviation;
        SettingsFileText.Text = $"{settings.SettingsPath}  •  {settings.RuntimeConfigPath}";
    }

    private void ShowView(FrameworkElement view, Button activeButton)
    {
        OverviewView.Visibility = Visibility.Collapsed;
        CardsView.Visibility = Visibility.Collapsed;
        ServerView.Visibility = Visibility.Collapsed;
        SettingsView.Visibility = Visibility.Collapsed;
        view.Visibility = Visibility.Visible;

        OverviewNavButton.Style = (Style)FindResource("NavButtonStyle");
        CardsNavButton.Style = (Style)FindResource("NavButtonStyle");
        ServerNavButton.Style = (Style)FindResource("NavButtonStyle");
        SettingsNavButton.Style = (Style)FindResource("NavButtonStyle");
        activeButton.Style = (Style)FindResource("ActiveNavButtonStyle");
    }

    private void OverviewNavButton_Click(object sender, RoutedEventArgs e) => ShowView(OverviewView, OverviewNavButton);

    private void CardsNavButton_Click(object sender, RoutedEventArgs e)
    {
        ShowView(CardsView, CardsNavButton);
        EnsureCardCatalogLoaded();
    }

    private void ServerNavButton_Click(object sender, RoutedEventArgs e) => ShowView(ServerView, ServerNavButton);

    private void SettingsNavButton_Click(object sender, RoutedEventArgs e)
    {
        LoadSettings();
        SetSettingsStatus("Changes are saved to launcher-settings.json.", false);
        ShowView(SettingsView, SettingsNavButton);
    }

    private void EnsureCardCatalogLoaded(bool force = false)
    {
        if (cardCatalogLoaded && !force)
        {
            return;
        }

        try
        {
            Dictionary<int, string> rarityNames = LoadRarityNames();
            Dictionary<long, CardCatalogEntry> cards = [];
            string playerRoot = Path.Combine(runtimeSnapshot.Root, "runtime-source", "localfut21");
            foreach (string path in new[] { Path.Combine(playerRoot, "players.json"), Path.Combine(playerRoot, "custom-players.json") })
            {
                if (!File.Exists(path))
                {
                    continue;
                }

                using JsonDocument document = JsonDocument.Parse(File.ReadAllText(path));
                foreach (JsonProperty property in document.RootElement.EnumerateObject())
                {
                    if (!long.TryParse(property.Name, out long resourceId) || property.Value.ValueKind != JsonValueKind.Object)
                    {
                        continue;
                    }

                    JsonElement metadata = property.Value;
                    string name = JsonString(metadata, "name", $"Player {resourceId}");
                    int rating = JsonInt(metadata, "rating");
                    string position = JsonString(metadata, "preferredPosition", "-");
                    int rareFlag = JsonInt(metadata, "rareflag");
                    string promotion = rarityNames.TryGetValue(rareFlag, out string? rarityName)
                        ? rarityName
                        : $"Rarity {rareFlag}";
                    cards[resourceId] = new CardCatalogEntry(name, rating, position, promotion, resourceId);
                }
            }

            cardCatalog.Clear();
            cardCatalog.AddRange(cards.Values
                .OrderBy(card => card.Name, StringComparer.OrdinalIgnoreCase)
                .ThenByDescending(card => card.Rating)
                .ThenBy(card => card.ResourceId));
            cardCatalogLoaded = true;
            ApplyCardSearch();
            CardGrantStatusText.Text = "Select a card to add one copy to your club.";
        }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException or JsonException)
        {
            cardCatalogLoaded = false;
            CardCatalogGrid.ItemsSource = null;
            CardResultsText.Text = "Card catalog could not be loaded.";
            CardGrantStatusText.Text = exception.Message;
        }
    }

    private Dictionary<int, string> LoadRarityNames()
    {
        Dictionary<int, string> names = new()
        {
            [0] = "Non-Rare Gold",
            [1] = "Rare Gold"
        };
        string path = Path.Combine(runtimeSnapshot.Root, "native-data", "wefut-fifa21-rarities.json");
        if (!File.Exists(path))
        {
            return names;
        }

        using JsonDocument document = JsonDocument.Parse(File.ReadAllText(path));
        if (!document.RootElement.TryGetProperty("rarities", out JsonElement rarities))
        {
            return names;
        }

        foreach (JsonElement rarity in rarities.EnumerateArray())
        {
            int id = JsonInt(rarity, "id");
            string name = JsonString(rarity, "name", $"Rarity {id}");
            names[id] = name;
        }
        return names;
    }

    private static int JsonInt(JsonElement element, string propertyName)
    {
        if (!element.TryGetProperty(propertyName, out JsonElement value)) return 0;
        if (value.ValueKind == JsonValueKind.Number && value.TryGetInt32(out int number)) return number;
        return value.ValueKind == JsonValueKind.String && int.TryParse(value.GetString(), out number) ? number : 0;
    }

    private static string JsonString(JsonElement element, string propertyName, string fallback)
    {
        if (!element.TryGetProperty(propertyName, out JsonElement value)) return fallback;
        return value.ValueKind == JsonValueKind.String ? value.GetString() ?? fallback : value.ToString();
    }

    private void ApplyCardSearch()
    {
        string search = CardSearchTextBox.Text.Trim();
        IEnumerable<CardCatalogEntry> matches = cardCatalog;
        if (search.Length > 0)
        {
            matches = matches.Where(card =>
                card.Name.Contains(search, StringComparison.OrdinalIgnoreCase) ||
                card.ResourceId.ToString().Contains(search, StringComparison.OrdinalIgnoreCase));
        }

        List<CardCatalogEntry> allMatches = matches.ToList();
        CardCatalogGrid.ItemsSource = allMatches.Take(250).ToList();
        CardResultsText.Text = allMatches.Count > 250
            ? $"Showing the first 250 of {allMatches.Count:N0} matches ({cardCatalog.Count:N0} cards total)."
            : $"Showing {allMatches.Count:N0} of {cardCatalog.Count:N0} cards.";
        GrantSelectedCardButton.IsEnabled = false;
    }

    private void CardSearchTextBox_TextChanged(object sender, TextChangedEventArgs e)
    {
        if (cardCatalogLoaded) ApplyCardSearch();
    }

    private void CardSearchTextBox_KeyDown(object sender, KeyEventArgs e)
    {
        if (e.Key == Key.Enter) ApplyCardSearch();
    }

    private void RefreshCardsButton_Click(object sender, RoutedEventArgs e) => EnsureCardCatalogLoaded(true);

    private void CardCatalogGrid_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        GrantSelectedCardButton.IsEnabled = CardCatalogGrid.SelectedItem is CardCatalogEntry && cardGrantProcess is not { HasExited: false };
    }

    private void GrantSelectedCardButton_Click(object sender, RoutedEventArgs e)
    {
        if (CardCatalogGrid.SelectedItem is not CardCatalogEntry card) return;
        string script = Path.Combine(runtimeSnapshot.Root, "Grant-LocalFUTCard.ps1");
        StartCardGrant(script, ["-ResourceId", card.ResourceId.ToString()], $"Giving {card.Name} ({card.Promotion}, {card.ResourceId})");
    }

    private void GrantAllCardsButton_Click(object sender, RoutedEventArgs e)
    {
        EnsureCardCatalogLoaded();
        MessageBoxResult answer = MessageBox.Show(
            this,
            $"This will add one copy of every missing player card to your club ({cardCatalog.Count:N0} catalog entries).\n\nFIFA 21 and LocalFUT must be closed. This can take several minutes. Continue?",
            "Give every card",
            MessageBoxButton.YesNo,
            MessageBoxImage.Warning,
            MessageBoxResult.No);
        if (answer != MessageBoxResult.Yes) return;

        string script = Path.Combine(runtimeSnapshot.Root, "Grant-All-LocalFUTCards.ps1");
        StartCardGrant(script, [], "Giving every missing card");
    }

    private void StartCardGrant(string scriptPath, IReadOnlyList<string> arguments, string operation)
    {
        if (cardGrantProcess is { HasExited: false })
        {
            CardGrantStatusText.Text = "A card grant is already running.";
            return;
        }
        if (!File.Exists(scriptPath))
        {
            CardGrantStatusText.Text = $"Missing script: {Path.GetFileName(scriptPath)}";
            return;
        }

        CardGrantStatusText.Text = operation + "…";
        SetCardGrantControlsEnabled(false);
        try
        {
            Process process = CreatePowerShellProcess(scriptPath, arguments);
            cardGrantProcess = process;
            process.OutputDataReceived += (_, args) => QueueCardGrantLine(args.Data, false);
            process.ErrorDataReceived += (_, args) => QueueCardGrantLine(args.Data, true);
            process.EnableRaisingEvents = true;
            process.Exited += (_, _) => Dispatcher.Invoke(() =>
            {
                int exitCode = SafeExitCode(process);
                CardGrantStatusText.Text = exitCode == 0 ? "Card grant completed successfully." : $"Card grant failed (exit code {exitCode}).";
                SetCardGrantControlsEnabled(true);
            });
            process.Start();
            process.BeginOutputReadLine();
            process.BeginErrorReadLine();
        }
        catch (Exception exception) when (exception is InvalidOperationException or System.ComponentModel.Win32Exception or IOException)
        {
            CardGrantStatusText.Text = "Could not start card grant: " + exception.Message;
            SetCardGrantControlsEnabled(true);
        }
    }

    private void QueueCardGrantLine(string? line, bool isError)
    {
        if (string.IsNullOrWhiteSpace(line)) return;
        Dispatcher.BeginInvoke(() =>
        {
            string display = line.Length > 180 ? line[..180] + "…" : line;
            CardGrantStatusText.Text = (isError ? "ERROR: " : string.Empty) + display;
            AppendWorkflowLog((isError ? "ERROR: " : string.Empty) + line);
        });
    }

    private void SetCardGrantControlsEnabled(bool enabled)
    {
        GrantAllCardsButton.IsEnabled = enabled;
        CardSearchTextBox.IsEnabled = enabled;
        CardCatalogGrid.IsEnabled = enabled;
        GrantSelectedCardButton.IsEnabled = enabled && CardCatalogGrid.SelectedItem is CardCatalogEntry;
    }

    private void GrantAllCustomisationButton_Click(object sender, RoutedEventArgs e)
    {
        if (customisationGrantProcess is { HasExited: false })
        {
            CustomisationGrantStatusText.Text = "A customisation grant is already running.";
            return;
        }

        string script = Path.Combine(runtimeSnapshot.Root, "Grant-All-Club-Customisation.ps1");
        if (!File.Exists(script))
        {
            CustomisationGrantStatusText.Text = "Missing script: " + Path.GetFileName(script);
            return;
        }

        MessageBoxResult answer = MessageBox.Show(
            this,
            "This will add every verified bundled kit, stadium choice, badge and ball to your local club. LocalFUT must be running. Continue?",
            "Grant all club customisation",
            MessageBoxButton.YesNo,
            MessageBoxImage.Question,
            MessageBoxResult.No);
        if (answer != MessageBoxResult.Yes) return;

        CustomisationGrantStatusText.Text = "Granting club customisation…";
        GrantAllCustomisationButton.IsEnabled = false;
        AppendWorkflowLog($"[{DateTime.Now:HH:mm:ss}] Granting all verified club customisation assets.");
        try
        {
            Process process = CreatePowerShellProcess(script, []);
            customisationGrantProcess = process;
            process.OutputDataReceived += (_, args) => QueueCustomisationGrantLine(args.Data, false);
            process.ErrorDataReceived += (_, args) => QueueCustomisationGrantLine(args.Data, true);
            process.EnableRaisingEvents = true;
            process.Exited += (_, _) => Dispatcher.Invoke(() =>
            {
                int exitCode = SafeExitCode(process);
                CustomisationGrantStatusText.Text = exitCode == 0
                    ? "Customisation grant completed successfully."
                    : $"Customisation grant failed (exit code {exitCode}).";
                GrantAllCustomisationButton.IsEnabled = true;
                AppendWorkflowLog($"[{DateTime.Now:HH:mm:ss}] Customisation grant exited with code {exitCode}.");
            });
            process.Start();
            process.BeginOutputReadLine();
            process.BeginErrorReadLine();
        }
        catch (Exception exception) when (exception is InvalidOperationException or System.ComponentModel.Win32Exception or IOException)
        {
            CustomisationGrantStatusText.Text = "Could not start customisation grant: " + exception.Message;
            GrantAllCustomisationButton.IsEnabled = true;
            AppendWorkflowLog("ERROR: " + exception.Message);
        }
    }

    private void QueueCustomisationGrantLine(string? line, bool isError)
    {
        if (string.IsNullOrWhiteSpace(line)) return;
        Dispatcher.BeginInvoke(() =>
        {
            string display = line.Length > 180 ? line[..180] + "…" : line;
            CustomisationGrantStatusText.Text = (isError ? "ERROR: " : string.Empty) + display;
            AppendWorkflowLog((isError ? "ERROR: " : string.Empty) + line);
        });
    }

    private sealed record CardCatalogEntry(string Name, int Rating, string Position, string Promotion, long ResourceId);

    private void LaunchLocalFutButton_Click(object sender, RoutedEventArgs e)
    {
        if (!ValidateLaunchPrerequisites(true))
        {
            return;
        }

        ShowView(ServerView, ServerNavButton);
        StartLaunchWorkflow(false);
    }

    private void StartServicesButton_Click(object sender, RoutedEventArgs e)
    {
        if (!ValidateLaunchPrerequisites(false))
        {
            return;
        }

        StartLaunchWorkflow(true);
    }

    private void StartLaunchWorkflow(bool servicesOnly)
    {
        if (startProcess is { HasExited: false })
        {
            AppendWorkflowLog("A start workflow is already running.");
            return;
        }

        string launchScript = Path.Combine(runtimeSnapshot.Root, "Launch-LocalFUT.ps1");
        string operation = servicesOnly ? "Starting local services" : "Launching FIFA 21 and LocalFUT";
        string[] arguments = servicesOnly ? ["-NoGameLaunch"] : [];
        WorkflowStatusText.Text = operation + "…";
        AppendWorkflowLog($"[{DateTime.Now:HH:mm:ss}] {operation}.");
        SetStartControlsEnabled(false);

        try
        {
            Process process = CreatePowerShellProcess(launchScript, arguments);
            startProcess = process;
            AttachProcessOutput(process);
            process.EnableRaisingEvents = true;
            process.Exited += (_, _) => Dispatcher.Invoke(() => CompleteStartWorkflow(process));
            process.Start();
            process.BeginOutputReadLine();
            process.BeginErrorReadLine();
        }
        catch (Exception exception) when (exception is InvalidOperationException or System.ComponentModel.Win32Exception or IOException)
        {
            SetStartControlsEnabled(true);
            WorkflowStatusText.Text = "Could not start";
            AppendWorkflowLog("ERROR: " + exception.Message);
        }
    }

    private void StopServicesButton_Click(object sender, RoutedEventArgs e)
    {
        if (stopProcess is { HasExited: false })
        {
            AppendWorkflowLog("The stop workflow is already running.");
            return;
        }

        string stopScript = Path.Combine(runtimeSnapshot.Root, "Stop-LocalFUT.ps1");
        if (!File.Exists(stopScript))
        {
            WorkflowStatusText.Text = "Stop script missing";
            AppendWorkflowLog("ERROR: " + stopScript + " was not found.");
            return;
        }

        WorkflowStatusText.Text = "Stopping local services…";
        AppendWorkflowLog($"[{DateTime.Now:HH:mm:ss}] Stopping Project 21 services and removing its hosts entry.");
        StopServicesButton.IsEnabled = false;

        try
        {
            Process process = CreatePowerShellProcess(stopScript, []);
            stopProcess = process;
            AttachProcessOutput(process);
            process.EnableRaisingEvents = true;
            process.Exited += (_, _) => Dispatcher.Invoke(() => CompleteStopWorkflow(process));
            process.Start();
            process.BeginOutputReadLine();
            process.BeginErrorReadLine();
        }
        catch (Exception exception) when (exception is InvalidOperationException or System.ComponentModel.Win32Exception or IOException)
        {
            StopServicesButton.IsEnabled = true;
            WorkflowStatusText.Text = "Stop failed";
            AppendWorkflowLog("ERROR: " + exception.Message);
        }
    }

    private void RestartLocalFutButton_Click(object sender, RoutedEventArgs e)
    {
        if (restartProcess is { HasExited: false })
        {
            AppendWorkflowLog("A restart workflow is already running.");
            return;
        }

        string restartScript = Path.Combine(runtimeSnapshot.Root, "Restart-LocalFUT.ps1");
        if (!File.Exists(restartScript))
        {
            AppendWorkflowLog("ERROR: " + restartScript + " was not found.");
            return;
        }

        WorkflowStatusText.Text = "Restarting LocalFUT…";
        AppendWorkflowLog($"[{DateTime.Now:HH:mm:ss}] Stopping FIFA 21 and LocalFUT, then relaunching the launcher.");
        RestartLocalFutButton.IsEnabled = false;
        try
        {
            Process process = CreatePowerShellProcess(restartScript, []);
            restartProcess = process;
            AttachProcessOutput(process);
            process.EnableRaisingEvents = true;
            process.Exited += (_, _) => Dispatcher.Invoke(() =>
            {
                int exitCode = SafeExitCode(process);
                AppendWorkflowLog($"[{DateTime.Now:HH:mm:ss}] Restart workflow exited with code {exitCode}.");
                if (exitCode == 0)
                {
                    Close();
                }
                else
                {
                    RestartLocalFutButton.IsEnabled = true;
                    WorkflowStatusText.Text = "Restart failed";
                }
            });
            process.Start();
            process.BeginOutputReadLine();
            process.BeginErrorReadLine();
        }
        catch (Exception exception) when (exception is InvalidOperationException or System.ComponentModel.Win32Exception or IOException)
        {
            RestartLocalFutButton.IsEnabled = true;
            WorkflowStatusText.Text = "Restart failed";
            AppendWorkflowLog("ERROR: " + exception.Message);
        }
    }

    private Process CreatePowerShellProcess(string scriptPath, IReadOnlyList<string> arguments)
    {
        ProcessStartInfo startInfo = new()
        {
            FileName = "powershell.exe",
            WorkingDirectory = runtimeSnapshot.Root,
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true
        };
        startInfo.ArgumentList.Add("-NoLogo");
        startInfo.ArgumentList.Add("-NoProfile");
        startInfo.ArgumentList.Add("-ExecutionPolicy");
        startInfo.ArgumentList.Add("Bypass");
        startInfo.ArgumentList.Add("-File");
        startInfo.ArgumentList.Add(scriptPath);
        foreach (string argument in arguments)
        {
            startInfo.ArgumentList.Add(argument);
        }

        return new Process { StartInfo = startInfo };
    }

    private void AttachProcessOutput(Process process)
    {
        process.OutputDataReceived += (_, args) => QueueWorkflowLine(args.Data, false);
        process.ErrorDataReceived += (_, args) => QueueWorkflowLine(args.Data, true);
    }

    private void QueueWorkflowLine(string? line, bool isError)
    {
        if (string.IsNullOrWhiteSpace(line))
        {
            return;
        }

        Dispatcher.BeginInvoke(() =>
        {
            AppendWorkflowLog((isError ? "ERROR: " : string.Empty) + line);
            string normalized = line.ToLowerInvariant();
            if (normalized.Contains("lsx:")) WorkflowStatusText.Text = "LSX mode selected";
            if (normalized.Contains("server:")) WorkflowStatusText.Text = "Local services ready";
            if (normalized.Contains("game:")) WorkflowStatusText.Text = "FIFA 21 detected";
            if (normalized.Contains("trust bridge:")) WorkflowStatusText.Text = "Trust bridge checked";
            if (normalized.Contains("connection:")) WorkflowStatusText.Text = "Connection status updated";
        });
    }

    private void CompleteStartWorkflow(Process process)
    {
        int exitCode = SafeExitCode(process);
        SetStartControlsEnabled(true);
        WorkflowStatusText.Text = exitCode == 0 ? "Start workflow completed" : "Launch failed";
        AppendWorkflowLog($"[{DateTime.Now:HH:mm:ss}] Start workflow exited with code {exitCode}.");
        RefreshRuntimeStatus();
    }

    private void CompleteStopWorkflow(Process process)
    {
        int exitCode = SafeExitCode(process);
        StopServicesButton.IsEnabled = true;
        SetStartControlsEnabled(true);
        WorkflowStatusText.Text = exitCode == 0 ? "Local services stopped" : "Stop failed";
        AppendWorkflowLog($"[{DateTime.Now:HH:mm:ss}] Stop workflow exited with code {exitCode}.");
        RefreshRuntimeStatus();
    }

    private static int SafeExitCode(Process process)
    {
        try
        {
            process.WaitForExit();
            return process.ExitCode;
        }
        catch
        {
            return -1;
        }
    }

    private void SetStartControlsEnabled(bool enabled)
    {
        LaunchLocalFutButton.IsEnabled = enabled;
        StartServicesButton.IsEnabled = enabled;
    }

    private void AppendWorkflowLog(string line)
    {
        WorkflowLogTextBox.AppendText(line + Environment.NewLine);
        WorkflowLogTextBox.ScrollToEnd();
    }

    private bool ValidateLaunchPrerequisites(bool requireGame)
    {
        string launchScript = Path.Combine(runtimeSnapshot.Root, "Launch-LocalFUT.ps1");
        LauncherSettingsSnapshot settings = RuntimeCompatibility.LoadSettings(runtimeSnapshot.Root);
        if (!File.Exists(launchScript))
        {
            ShowLaunchConfigurationError("Launch-LocalFUT.ps1 is missing from the runtime root.");
            return false;
        }

        if (requireGame && !File.Exists(settings.GamePath))
        {
            ShowLaunchConfigurationError("Choose a valid FIFA21.exe location in Settings before launching.");
            return false;
        }

        if (!Directory.Exists(settings.SourcePath) ||
            !File.Exists(Path.Combine(settings.SourcePath, "localfut21", "server.py")) ||
            !File.Exists(Path.Combine(settings.SourcePath, "redirector_tls.py")))
        {
            ShowLaunchConfigurationError("Choose a valid LocalFUT runtime source in Settings before launching.");
            return false;
        }

        return true;
    }

    private void ShowLaunchConfigurationError(string message)
    {
        LoadSettings();
        SetSettingsStatus(message, true);
        ShowView(SettingsView, SettingsNavButton);
    }

    private void OpenGameFolderButton_Click(object sender, RoutedEventArgs e)
    {
        LauncherSettingsSnapshot settings = RuntimeCompatibility.LoadSettings(runtimeSnapshot.Root);
        string? gameFolder = Path.GetDirectoryName(settings.GamePath);
        if (gameFolder is null || !Directory.Exists(gameFolder))
        {
            ShowLaunchConfigurationError("Choose a valid FIFA21.exe location before opening its folder.");
            return;
        }

        Process.Start(new ProcessStartInfo { FileName = gameFolder, UseShellExecute = true });
    }

    private void OpenLogsButton_Click(object sender, RoutedEventArgs e)
    {
        string runtimeFolder = Path.Combine(runtimeSnapshot.Root, "runtime");
        Directory.CreateDirectory(runtimeFolder);
        Process.Start(new ProcessStartInfo { FileName = runtimeFolder, UseShellExecute = true });
    }

    private void BrowseGameButton_Click(object sender, RoutedEventArgs e)
    {
        OpenFileDialog dialog = new()
        {
            Title = "Select FIFA 21 executable",
            Filter = "FIFA 21 executable (FIFA21.exe)|FIFA21.exe|Executable files (*.exe)|*.exe",
            CheckFileExists = true,
            Multiselect = false
        };

        string current = GamePathTextBox.Text.Trim();
        if (File.Exists(current))
        {
            dialog.InitialDirectory = Path.GetDirectoryName(current);
            dialog.FileName = Path.GetFileName(current);
        }

        if (dialog.ShowDialog(this) == true)
        {
            GamePathTextBox.Text = dialog.FileName;
            SetSettingsStatus("Game location selected. Save to apply it.", false);
        }
    }

    private void BrowseRuntimeButton_Click(object sender, RoutedEventArgs e)
    {
        OpenFolderDialog dialog = new()
        {
            Title = "Select LocalFUT runtime source folder",
            Multiselect = false
        };

        string current = RuntimeSourceTextBox.Text.Trim();
        if (Directory.Exists(current))
        {
            dialog.InitialDirectory = current;
        }

        if (dialog.ShowDialog(this) == true)
        {
            RuntimeSourceTextBox.Text = dialog.FolderName;
            SetSettingsStatus("Runtime source selected. Save to apply it.", false);
        }
    }

    private void SaveSettingsButton_Click(object sender, RoutedEventArgs e)
    {
        string gamePath = GamePathTextBox.Text.Trim().Trim('"');
        string sourcePath = RuntimeSourceTextBox.Text.Trim().Trim('"');
        string clubName = ClubNameTextBox.Text.Trim();
        string clubAbbreviation = ClubAbbreviationTextBox.Text.Trim().ToUpperInvariant();

        if (clubName.Length < 1 || clubName.Length > 15)
        {
            SetSettingsStatus("Club name must contain between 1 and 15 characters.", true);
            ClubNameTextBox.Focus();
            return;
        }

        if (!System.Text.RegularExpressions.Regex.IsMatch(clubAbbreviation, "^[A-Z0-9]{3}$"))
        {
            SetSettingsStatus("Club abbreviation must be exactly three letters or numbers.", true);
            ClubAbbreviationTextBox.Focus();
            return;
        }

        if (!File.Exists(gamePath))
        {
            SetSettingsStatus("Game location must point to an existing FIFA21.exe file.", true);
            GamePathTextBox.Focus();
            return;
        }

        if (!string.Equals(Path.GetFileName(gamePath), "FIFA21.exe", StringComparison.OrdinalIgnoreCase))
        {
            SetSettingsStatus("Select FIFA21.exe, not another executable.", true);
            GamePathTextBox.Focus();
            return;
        }

        string serverPath = Path.Combine(sourcePath, "localfut21", "server.py");
        string redirectorPath = Path.Combine(sourcePath, "redirector_tls.py");
        if (!Directory.Exists(sourcePath) || !File.Exists(serverPath) || !File.Exists(redirectorPath))
        {
            SetSettingsStatus("Runtime source is missing localfut21/server.py or redirector_tls.py.", true);
            RuntimeSourceTextBox.Focus();
            return;
        }

        try
        {
            gamePath = Path.GetFullPath(gamePath);
            sourcePath = Path.GetFullPath(sourcePath);
            RuntimeCompatibility.SaveSettings(
                runtimeSnapshot.Root, gamePath, sourcePath, clubName, clubAbbreviation);
            GamePathTextBox.Text = gamePath;
            RuntimeSourceTextBox.Text = sourcePath;
            ClubNameTextBox.Text = clubName;
            ClubAbbreviationTextBox.Text = clubAbbreviation;
            SetSettingsStatus("Settings saved. Club identity applies on the next LocalFUT launch.", false, true);
        }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException or JsonException)
        {
            SetSettingsStatus($"Could not save settings: {exception.Message}", true);
        }
    }

    private void SetSettingsStatus(string message, bool isError, bool isSuccess = false)
    {
        SettingsStatusText.Text = message;
        SettingsStatusText.Foreground = isError
            ? new SolidColorBrush(Color.FromRgb(255, 111, 120))
            : isSuccess
                ? (Brush)FindResource("Accent")
                : new SolidColorBrush(Color.FromRgb(112, 112, 119));
    }

    private void ApplyRuntimeSnapshot(RuntimeSnapshot snapshot)
    {
        ProfileName.Text = snapshot.ProfileName;
        ProfileClub.Text = snapshot.ClubName;
        EnvironmentStatusBadge.Text = snapshot.IsCompatible ? "RUNTIME DETECTED" : "RUNTIME MISSING";
        LocalServerState.Text = CondenseState(snapshot.ServerState);
        GameClientState.Text = CondenseState(snapshot.GameState);
        RuntimePath.Text = snapshot.IsCompatible
            ? "Compatible LocalFUT runtime found"
            : "Place this launcher folder inside LocalFUT";
        ServerPageServerState.Text = snapshot.ServerState;
        ServerPageGameState.Text = snapshot.GameState;
        ServerPageConnectionState.Text = snapshot.ConnectionState;
    }

    private void RefreshRuntimeStatus()
    {
        ApplyRuntimeSnapshot(RuntimeCompatibility.Discover());
    }

    private static string CondenseState(string value)
    {
        const int maximumLength = 28;
        return value.Length <= maximumLength ? value : value[..(maximumLength - 1)] + "…";
    }

    private void Window_MouseLeftButtonDown(object sender, MouseButtonEventArgs e)
    {
        if (e.ButtonState == MouseButtonState.Pressed)
        {
            DragMove();
        }
    }

    private void MinimizeButton_Click(object sender, RoutedEventArgs e)
    {
        WindowState = WindowState.Minimized;
    }

    private void CloseButton_Click(object sender, RoutedEventArgs e)
    {
        Close();
    }
}
