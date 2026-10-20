using System.Data;
using System.Globalization;
using System.Security.Cryptography;
using System.Text.Json;
using FifaLibrary;
using FrostySdk.Frostbite;
using FrostySdk.Interfaces;
using FrostySdk.Managers;

internal sealed class ConsoleLogger : ILogger
{
    private readonly object sync = new();
    private readonly Dictionary<string, int> progressBuckets = new(StringComparer.Ordinal);
    private string? lastMessage;

    public void Log(string text, params object[] vars) => Write("INFO", text, vars);

    public void LogWarning(string text, params object[] vars) => Write("WARN", text, vars);

    public void LogError(string text, params object[] vars) => Write("ERROR", text, vars);

    private void Write(string level, string text, object[] vars)
    {
        var message = vars.Length == 0 ? text : string.Format(text, vars);
        lock (sync)
        {
            if (message == lastMessage)
            {
                return;
            }

            var percentMarker = message.LastIndexOf('%');
            if (percentMarker > 0)
            {
                var numberStart = percentMarker - 1;
                while (numberStart >= 0 && (char.IsDigit(message[numberStart]) || message[numberStart] == '.'))
                    numberStart--;
                numberStart++;

                if (double.TryParse(
                        message.AsSpan(numberStart, percentMarker - numberStart),
                        NumberStyles.Float,
                        CultureInfo.InvariantCulture,
                        out var percent))
                {
                    var progressKey = message[..numberStart];
                    var bucket = (int)(percent / 5);
                    if (progressBuckets.TryGetValue(progressKey, out var previousBucket) && previousBucket == bucket)
                    {
                        return;
                    }
                    progressBuckets[progressKey] = bucket;
                }
            }

            lastMessage = message;
            Console.WriteLine($"[{DateTime.Now:HH:mm:ss}] {level,-5} {message}");
        }
    }
}

internal static class Program
{
    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        WriteIndented = true,
    };

    private static int Main(string[] args)
    {
        try
        {
            var options = Options.Parse(args);
            return Run(options);
        }
        catch (Exception error)
        {
            Console.Error.WriteLine($"ERROR: {error.Message}");
            Console.Error.WriteLine(error);
            return 1;
        }
    }

    private static int Run(Options options)
    {
        var outputRoot = Path.GetFullPath(options.OutputRoot);
        Directory.CreateDirectory(outputRoot);
        if (!string.IsNullOrWhiteSpace(options.InputDirectory))
            return ExportInputDirectory(Path.GetFullPath(options.InputDirectory), outputRoot);

        var gameRoot = Path.GetFullPath(options.GameRoot);
        var executable = Path.Combine(gameRoot, "FIFA21.exe");
        if (!File.Exists(executable))
            throw new FileNotFoundException("FIFA21.exe was not found in the selected game directory.", executable);

        Console.WriteLine("FIFA 21 local database miner");
        Console.WriteLine($"Game:   {gameRoot}");
        Console.WriteLine($"Output: {outputRoot}");
        Console.WriteLine("Loading the read-only Frostbite asset index. The first run can take several minutes.");

        var cache = new CacheManager();
        if (!cache.LoadData("FIFA21", gameRoot, new ConsoleLogger(), forceDeleteOfOld: false, loadSDK: true))
            throw new InvalidOperationException("Frostbite Modding Tool could not load the FIFA 21 asset index.");

        var ebxAssets = AssetManager.Instance.EnumerateEbx().ToArray();
        var collectorCandidates = ebxAssets
            .Where(asset =>
                (asset.Name ?? string.Empty).Contains("collector", StringComparison.OrdinalIgnoreCase)
                || (asset.FullPath ?? string.Empty).Contains("collector", StringComparison.OrdinalIgnoreCase)
                || (asset.Type ?? string.Empty).Contains("collector", StringComparison.OrdinalIgnoreCase))
            .Select(asset => new
            {
                asset.Name,
                asset.FullPath,
                asset.Type,
            })
            .OrderBy(asset => asset.FullPath, StringComparer.OrdinalIgnoreCase)
            .ToArray();
        WriteJson(Path.Combine(outputRoot, "legacy-collector-candidates.json"), collectorCandidates);

        var untypedNativeCollector = ebxAssets.SingleOrDefault(asset =>
            string.Equals(
                asset.Name,
                "core/chunkfiles/chunkfilecollector",
                StringComparison.OrdinalIgnoreCase)
            && string.IsNullOrWhiteSpace(asset.Type));
        if (untypedNativeCollector is not null)
        {
            untypedNativeCollector.Type = "ChunkFileCollector";
            Console.WriteLine("Recovered the known FIFA 21 native ChunkFileCollector type missing from FMT's cache index.");
        }

        var collectorAssets = AssetManager.Instance.EnumerateEbx("ChunkFileCollector").ToArray();
        Console.WriteLine($"Indexed {ebxAssets.Length:N0} EBX assets; {collectorAssets.Length:N0} are typed ChunkFileCollector assets.");

        var assets = AssetManager.Instance.EnumerateCustomAssets("legacy")
            .OrderBy(asset => asset.Name, StringComparer.OrdinalIgnoreCase)
            .ToArray();
        Console.WriteLine($"Indexed {assets.Length:N0} legacy assets.");

        var candidateManifest = assets
            .Where(asset => IsCatalogCandidate(asset.Name))
            .Select(asset => new
            {
                asset.Name,
                asset.Filename,
                asset.Type,
                asset.Size,
                asset.OriginalSize,
            })
            .ToArray();
        WriteJson(Path.Combine(outputRoot, "legacy-catalog-candidates.json"), candidateManifest);

        var logicalDatabasePaths = assets
            .Select(asset => asset.Name.Replace('\\', '/'))
            .Where(IsExtractableDatabaseAsset)
            .Concat(new[]
            {
                "data/db/cards_ng_db.db",
                "data/db/cards_ng_db-meta.xml",
                "data/db/fifa_ng_db.db",
                "data/db/fifa_ng_db-meta.xml",
                "data/loc/eng_us.db",
                "data/loc/eng_us-meta.xml",
            })
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .OrderBy(path => path, StringComparer.OrdinalIgnoreCase)
            .ToArray();

        var extracted = new List<ExtractedAsset>();
        foreach (var logicalPath in logicalDatabasePaths)
        {
            var entry = AssetManager.Instance.GetCustomAssetEntry("legacy", logicalPath);
            if (entry is null)
            {
                Console.WriteLine($"[WARN ] Legacy asset not present: {logicalPath}");
                continue;
            }

            using var stream = AssetManager.Instance.GetCustomAsset("legacy", entry);
            if (stream is null)
            {
                Console.WriteLine($"[WARN ] Legacy asset could not be read: {logicalPath}");
                continue;
            }

            var destination = Path.Combine(outputRoot, logicalPath.Replace('/', Path.DirectorySeparatorChar));
            Directory.CreateDirectory(Path.GetDirectoryName(destination)!);
            using (var file = File.Create(destination))
                stream.CopyTo(file);
            extracted.Add(new ExtractedAsset(logicalPath, destination, new FileInfo(destination).Length));
            Console.WriteLine($"Extracted {logicalPath} ({new FileInfo(destination).Length:N0} bytes)");
        }

        WriteJson(Path.Combine(outputRoot, "extracted-assets.json"), extracted);
        foreach (var database in extracted.Where(asset => asset.LogicalPath.EndsWith(".db", StringComparison.OrdinalIgnoreCase)))
        {
            var metadataLogicalPath = database.LogicalPath[..^3] + "-meta.xml";
            var metadata = extracted.FirstOrDefault(asset =>
                string.Equals(asset.LogicalPath, metadataLogicalPath, StringComparison.OrdinalIgnoreCase));
            if (metadata is null)
            {
                Console.WriteLine($"[WARN ] Matching metadata is missing for {database.LogicalPath}");
                continue;
            }

            var databaseName = Path.GetFileNameWithoutExtension(database.LocalPath);
            ExportDatabase(database.LocalPath, metadata.LocalPath, Path.Combine(outputRoot, "tables", databaseName));
        }

        Console.WriteLine("Mining complete. Raw game assets and JSON table exports are under the output directory.");
        return 0;
    }

    private static int ExportInputDirectory(string inputDirectory, string outputRoot)
    {
        if (!Directory.Exists(inputDirectory))
            throw new DirectoryNotFoundException($"Database input directory was not found: {inputDirectory}");

        Console.WriteLine("FIFA 21 local database exporter");
        Console.WriteLine($"Input:  {inputDirectory}");
        Console.WriteLine($"Output: {outputRoot}");

        var inputs = new List<DatabaseInput>();
        foreach (var databasePath in Directory.EnumerateFiles(inputDirectory, "*.db", SearchOption.AllDirectories)
                     .OrderBy(path => path, StringComparer.OrdinalIgnoreCase))
        {
            var metadataPath = Path.Combine(
                Path.GetDirectoryName(databasePath)!,
                $"{Path.GetFileNameWithoutExtension(databasePath)}-meta.xml");
            if (!File.Exists(metadataPath))
            {
                Console.WriteLine($"[WARN ] Matching metadata is missing for {Path.GetFileName(databasePath)}");
                continue;
            }

            inputs.Add(new DatabaseInput(
                Path.GetRelativePath(inputDirectory, databasePath).Replace('\\', '/'),
                new FileInfo(databasePath).Length,
                Sha256(databasePath),
                Path.GetRelativePath(inputDirectory, metadataPath).Replace('\\', '/'),
                new FileInfo(metadataPath).Length,
                Sha256(metadataPath)));
            ExportDatabase(
                databasePath,
                metadataPath,
                Path.Combine(outputRoot, "tables", Path.GetFileNameWithoutExtension(databasePath)));
        }

        if (inputs.Count == 0)
            throw new InvalidOperationException("No complete FIFA database and metadata pairs were found in the input directory.");

        WriteJson(Path.Combine(outputRoot, "input-assets.json"), inputs);
        Console.WriteLine($"Exported {inputs.Count:N0} database pair(s) from the supplied directory.");
        return 0;
    }

    private static void ExportDatabase(string databasePath, string metadataPath, string outputDirectory)
    {
        if (!File.Exists(databasePath) || !File.Exists(metadataPath))
        {
            Console.WriteLine($"[WARN ] Skipping table export; database pair is incomplete: {databasePath}");
            return;
        }

        Directory.CreateDirectory(outputDirectory);
        using var databaseStream = File.OpenRead(databasePath);
        using var metadataStream = File.OpenRead(metadataPath);
        using var database = new DbFile(databaseStream, metadataStream);
        var tableManifest = new List<TableManifest>();

        foreach (var table in database.GetTables().OrderBy(table => table.ToString(), StringComparer.OrdinalIgnoreCase))
        {
            var data = table.ConvertToDataTable();
            var rows = ConvertRows(data);
            var tableName = data.TableName;
            WriteJson(Path.Combine(outputDirectory, $"{SanitizeFileName(tableName)}.json"), rows);
            tableManifest.Add(new TableManifest(
                tableName,
                rows.Count,
                data.Columns.Cast<DataColumn>().Select(column => column.ColumnName).ToArray()));
        }

        WriteJson(Path.Combine(outputDirectory, "manifest.json"), tableManifest);
        Console.WriteLine($"Exported {tableManifest.Count:N0} tables from {Path.GetFileName(databasePath)}.");
    }

    private static List<Dictionary<string, object?>> ConvertRows(DataTable table)
    {
        var rows = new List<Dictionary<string, object?>>(table.Rows.Count);
        foreach (DataRow row in table.Rows)
        {
            var values = new Dictionary<string, object?>(table.Columns.Count, StringComparer.Ordinal);
            foreach (DataColumn column in table.Columns)
                values[column.ColumnName] = row[column] is DBNull ? null : row[column];
            rows.Add(values);
        }
        return rows;
    }

    private static bool IsCatalogCandidate(string path) =>
        path.Contains("fifa_ng_db", StringComparison.OrdinalIgnoreCase)
        || path.Contains("eng_us", StringComparison.OrdinalIgnoreCase)
        || path.Contains("/fut", StringComparison.OrdinalIgnoreCase)
        || path.Contains("card", StringComparison.OrdinalIgnoreCase)
        || path.Contains("item", StringComparison.OrdinalIgnoreCase)
        || path.Contains("rarity", StringComparison.OrdinalIgnoreCase)
        || path.Contains("player", StringComparison.OrdinalIgnoreCase);

    private static bool IsExtractableDatabaseAsset(string path)
    {
        var normalized = path.Replace('\\', '/');
        var isDatabaseFile = normalized.EndsWith(".db", StringComparison.OrdinalIgnoreCase)
            || normalized.EndsWith("-meta.xml", StringComparison.OrdinalIgnoreCase);
        if (!isDatabaseFile)
            return false;
        return normalized.StartsWith("data/db/", StringComparison.OrdinalIgnoreCase)
            || normalized.StartsWith("data/loc/eng_us", StringComparison.OrdinalIgnoreCase);
    }

    private static string SanitizeFileName(string name)
    {
        var invalid = Path.GetInvalidFileNameChars();
        return string.Concat(name.Select(character => invalid.Contains(character) ? '_' : character));
    }

    private static void WriteJson<T>(string path, T value)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        File.WriteAllText(path, JsonSerializer.Serialize(value, JsonOptions));
    }

    private static string Sha256(string path)
    {
        using var stream = File.OpenRead(path);
        return Convert.ToHexString(SHA256.HashData(stream));
    }

    private sealed record ExtractedAsset(string LogicalPath, string LocalPath, long Bytes);

    private sealed record TableManifest(string Name, int Rows, string[] Columns);

    private sealed record DatabaseInput(
        string Database,
        long DatabaseBytes,
        string DatabaseSha256,
        string Metadata,
        long MetadataBytes,
        string MetadataSha256);

    private sealed record Options(string GameRoot, string OutputRoot, string? InputDirectory)
    {
        public static Options Parse(string[] args)
        {
            var gameRoot = @"C:\Program Files\EA Games\FIFA 21";
            var outputRoot = Path.GetFullPath(Path.Combine(AppContext.BaseDirectory, "..", "..", "..", "..", "..", "Project-21-V2", "localfut", "runtime", "mined"));
            string? inputDirectory = null;
            for (var index = 0; index < args.Length; index++)
            {
                switch (args[index])
                {
                    case "--game" when index + 1 < args.Length:
                        gameRoot = args[++index];
                        break;
                    case "--output" when index + 1 < args.Length:
                        outputRoot = args[++index];
                        break;
                    case "--input" when index + 1 < args.Length:
                        inputDirectory = args[++index];
                        break;
                    case "--help":
                    case "-h":
                        Console.WriteLine("Usage: Fifa21DataMiner [--game <FIFA 21 directory> | --input <database directory>] [--output <directory>]");
                        Environment.Exit(0);
                        break;
                    default:
                        throw new ArgumentException($"Unknown or incomplete argument: {args[index]}");
                }
            }
            return new Options(gameRoot, outputRoot, inputDirectory);
        }
    }
}
