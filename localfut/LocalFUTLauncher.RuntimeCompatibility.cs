using System.IO;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace Project21Launcher;

internal sealed record RuntimeSnapshot(
    string Root,
    bool IsCompatible,
    string ProfileName,
    string ClubName,
    string ServerState,
    string GameState,
    string ConnectionState);

internal sealed record LauncherSettingsSnapshot(
    string GamePath,
    string SourcePath,
    string ClubName,
    string ClubAbbreviation,
    string RuntimeConfigPath,
    string SettingsPath);

internal static class RuntimeCompatibility
{
    public static RuntimeSnapshot Discover()
    {
        string root = ResolveRuntimeRoot();
        bool hasLauncher = File.Exists(Path.Combine(root, "Launch-LocalFUT.ps1"));
        bool hasStopper = File.Exists(Path.Combine(root, "Stop-LocalFUT.ps1"));
        bool hasServer = File.Exists(Path.Combine(root, "runtime-source", "localfut21", "server.py"));
        bool hasRedirector = File.Exists(Path.Combine(root, "runtime-source", "redirector_tls.py"));
        bool compatible = hasLauncher && hasStopper && hasServer && hasRedirector;

        string profileName = "Local Player";
        string clubName = "Local FC";
        string serverState = compatible ? "Ready" : "Runtime missing";
        string gameState = "Not running";
        string connectionState = "Inactive";

        ReadProfile(root, ref profileName, ref clubName);
        ReadRuntimeStatus(root, ref serverState, ref gameState, ref connectionState);

        return new RuntimeSnapshot(
            root,
            compatible,
            profileName,
            clubName,
            serverState,
            gameState,
            connectionState);
    }

    public static LauncherSettingsSnapshot LoadSettings(string root)
    {
        string settingsPath = Path.Combine(ResolveSettingsRoot(), "launcher-settings.json");
        string configPath = Path.Combine(ResolveSettingsRoot(), "config.json");
        string gamePath = string.Empty;
        string sourcePath = Path.Combine(root, "runtime-source");
        string clubName = "Local FC";
        string clubAbbreviation = "LCL";

        if (File.Exists(settingsPath))
        {
            try
            {
                JsonNode? parsed = JsonNode.Parse(File.ReadAllText(settingsPath));
                if (parsed is JsonObject settings)
                {
                    gamePath = settings["gamePath"]?.GetValue<string>() ?? string.Empty;
                    sourcePath = settings["sourcePath"]?.GetValue<string>() ?? sourcePath;
                }
            }
            catch (JsonException)
            {
                // Present the defaults and let Save replace an invalid settings document.
            }
            catch (IOException)
            {
                // The settings screen remains usable if another process briefly locks the file.
            }
        }

        if (File.Exists(configPath))
        {
            try
            {
                if (JsonNode.Parse(File.ReadAllText(configPath)) is JsonObject config)
                {
                    clubName = config["club_name"]?.GetValue<string>() ?? clubName;
                    clubAbbreviation = config["club_abbr"]?.GetValue<string>() ?? clubAbbreviation;
                }
            }
            catch (JsonException) { }
            catch (IOException) { }
        }

        return new LauncherSettingsSnapshot(
            gamePath, sourcePath, clubName, clubAbbreviation, configPath, settingsPath);
    }

    public static void SaveSettings(
        string root, string gamePath, string sourcePath, string clubName, string clubAbbreviation)
    {
        string settingsRoot = ResolveSettingsRoot();
        Directory.CreateDirectory(settingsRoot);
        string settingsPath = Path.Combine(settingsRoot, "launcher-settings.json");
        JsonObject settings = new();

        if (File.Exists(settingsPath))
        {
            try
            {
                if (JsonNode.Parse(File.ReadAllText(settingsPath)) is JsonObject existing)
                {
                    settings = existing;
                }
            }
            catch (JsonException)
            {
                // Replace malformed JSON with a valid settings object.
            }
        }

        settings["gamePath"] = gamePath;
        settings["sourcePath"] = sourcePath;
        settings["gameReadyDelaySeconds"] ??= 5;

        string temporaryPath = settingsPath + ".tmp";
        File.WriteAllText(temporaryPath, settings.ToJsonString(new JsonSerializerOptions { WriteIndented = true }));
        File.Move(temporaryPath, settingsPath, true);

        string configPath = Path.Combine(settingsRoot, "config.json");
        JsonObject config = new();
        if (File.Exists(configPath))
        {
            if (JsonNode.Parse(File.ReadAllText(configPath)) is JsonObject existingConfig)
            {
                config = existingConfig;
            }
        }
        config["club_name"] = clubName;
        config["club_abbr"] = clubAbbreviation;
        string temporaryConfigPath = configPath + ".tmp";
        File.WriteAllText(temporaryConfigPath,
            config.ToJsonString(new JsonSerializerOptions { WriteIndented = true }));
        File.Move(temporaryConfigPath, configPath, true);
    }

    private static string ResolveRuntimeRoot()
    {
        string[] args = Environment.GetCommandLineArgs();
        for (int index = 1; index + 1 < args.Length; index++)
        {
            if (string.Equals(args[index], "--runtime-root", StringComparison.OrdinalIgnoreCase))
            {
                return Path.GetFullPath(args[index + 1]);
            }
        }

        return AppContext.BaseDirectory;
    }

    private static void ReadProfile(string root, ref string profileName, ref string clubName)
    {
        string configPath = Path.Combine(ResolveSettingsRoot(), "config.json");
        if (!File.Exists(configPath))
        {
            return;
        }

        try
        {
            using JsonDocument config = JsonDocument.Parse(File.ReadAllText(configPath));
            JsonElement rootElement = config.RootElement;
            if (rootElement.TryGetProperty("display_name", out JsonElement displayName) &&
                !string.IsNullOrWhiteSpace(displayName.GetString()))
            {
                profileName = displayName.GetString()!;
            }

            if (rootElement.TryGetProperty("club_name", out JsonElement configuredClub) &&
                !string.IsNullOrWhiteSpace(configuredClub.GetString()))
            {
                clubName = configuredClub.GetString()!;
            }
        }
        catch (JsonException)
        {
            // A malformed optional profile file must not prevent the shell opening.
        }
        catch (IOException)
        {
            // Runtime files can be momentarily locked while the server starts.
        }
    }

    private static void ReadRuntimeStatus(
        string root,
        ref string serverState,
        ref string gameState,
        ref string connectionState)
    {
        string statusPath = Path.Combine(root, "runtime", "status.json");
        if (!File.Exists(statusPath))
        {
            return;
        }

        try
        {
            using JsonDocument status = JsonDocument.Parse(File.ReadAllText(statusPath));
            JsonElement statusRoot = status.RootElement;
            serverState = ReadStatusValue(statusRoot, "server", serverState);
            gameState = ReadStatusValue(statusRoot, "game", gameState);
            connectionState = ReadStatusValue(statusRoot, "connection", connectionState);
        }
        catch (JsonException)
        {
            // Keep the discovered fallback values when a partial status write is read.
        }
        catch (IOException)
        {
            // Keep the shell available even if the runtime status is temporarily locked.
        }
    }

    private static string ReadStatusValue(JsonElement status, string propertyName, string fallback)
    {
        if (status.TryGetProperty(propertyName, out JsonElement value) && value.ValueKind == JsonValueKind.String)
        {
            string? text = value.GetString();
            if (!string.IsNullOrWhiteSpace(text))
            {
                return text;
            }
        }

        return fallback;
    }

    private static string ResolveSettingsRoot()
    {
        string? configured = Environment.GetEnvironmentVariable("LOCALFUT21_USER_CONFIG_ROOT");
        return !string.IsNullOrWhiteSpace(configured)
            ? Path.GetFullPath(configured)
            : Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Project21", "LocalFUT");
    }
}
