# FIFA 21 local database miner

The native helpers extract FIFA 21's shipped legacy databases, export every
table as JSON, and build the normalized player catalog consumed by the local FUT
server. A separate, optional WeFUT importer adds the historical FUT card-revision
and rarity layer when that external dataset is explicitly desired.

Raw databases and table exports stay under the ignored
`localfut/runtime/mined/` workspace. The catalog builder promotes only the
reviewable runtime catalog, input manifest, and quarantine report into tracked
locations. Neither helper patches the executable or modifies the installed game.

## Prerequisite

Clone and build the open-source Frostbite Modding Tool beside this repository:

```powershell
git clone --depth 1 https://github.com/na632/FrostbiteModdingTool.git C:\Games\FrostbiteModdingTool
dotnet restore C:\Games\FrostbiteModdingTool\FrostbiteModdingUI.sln
dotnet build C:\Games\FrostbiteModdingTool\FrostbiteModdingUI.sln -c Debug --no-restore
```

FMT's README identifies FIFA 21 as fully supported. The miner references the
locally built FMT libraries and plugin; it does not redistribute them.

## Run

```powershell
dotnet build C:\Games\Project-21-V2\tools\fifa21-data-miner\Fifa21DataMiner.csproj
dotnet C:\Games\FrostbiteModdingTool\FrostbiteModdingUI\bin\Debug\net7.0-windows\Fifa21DataMiner.dll `
  --game "C:\Program Files\EA Games\FIFA 21" `
  --output "C:\Games\Project-21-V2\localfut\runtime\mined"
```

The first run builds FMT's local Frostbite asset cache and can take several
minutes. Later runs reuse that cache.

FMT currently indexes FIFA 21's native `ChunkFileCollector`, but its public
legacy-asset manager does not expose the collector payloads. Until that upstream
path is completed, an already extracted native DB/XML directory can be parsed
without rescanning the archives:

```powershell
dotnet C:\Games\FrostbiteModdingTool\FrostbiteModdingUI\bin\Debug\net7.0-windows\Fifa21DataMiner.dll `
  --input "C:\Games\Project-21-V2\localfut\runtime\mined\data" `
  --output "C:\Games\Project-21-V2\localfut\runtime\mined"
```

Build the server catalog after the tables have been exported:

```powershell
node C:\Games\Project-21-V2\tools\fifa21-data-miner\build-player-catalog.mjs `
  --source-archive "C:\path\to\the-source-archive.zip"
```

The optional source archive is hashed for provenance; its path and contents are
not copied into the repository.

## Optional WeFUT FIFA 21 card catalog

To rebuild the full historical card-edition catalog and its rarity map:

```powershell
node C:\Games\Project-21-V2\tools\fifa21-data-miner\import-wefut-player-catalog.mjs
```

The importer uses WeFUT's FIFA 21 database endpoint and card-detail metadata,
throttles and retries requests, and checkpoints its resumable cache beneath the
ignored `localfut/runtime/mined/` directory. `--refresh` discards that cache and
performs a fresh web import; `--delay-ms N` changes the delay between bounded
request batches.

Base IDs and definition IDs exposed by WeFUT are retained exactly. When WeFUT
does not expose a distinct definition ID, the importer assigns a collision-free
local revision ID whose low FIFA base-ID bits remain intact. The generated
manifest counts those fallbacks explicitly; they are local emulator IDs, not
claimed EA IDs. No WeFUT images are copied.

## Output

- `legacy-catalog-candidates.json`: relevant legacy asset inventory.
- `data/db/*.db` and matching metadata, including `fifa_ng_db` and
  `cards_ng_db`: authoritative game and FUT-card databases.
- `data/loc/eng_us.db` and matching metadata when shipped as legacy assets.
- `tables/<database>/<table>.json`: lossless table exports.
- `tables/<database>/manifest.json`: row counts and column names.
- `runtime-source/localfut21/players.json`: normalized base-player catalog used
  directly by the local server.
- `native-data/player-catalog-manifest.json`: hashes, row counts, join rules,
  output hashes, and quarantine totals.
- `native-data/player-catalog-quarantine.json`: native player rows rejected for
  missing or ambiguous identity/affiliation data.
- `native-data/wefut-fifa21-rarities.json`: WeFUT rarity IDs, names, styles, and
  card counts.
- `native-data/wefut-player-catalog-manifest.json`: WeFUT source, output hashes,
  totals, resource-ID provenance, and fallback counts.

The game database remains authoritative for native base-player and relationship
IDs. The optional WeFUT output is a separately attributed external card layer;
its locally assigned fallback IDs remain visibly distinguished in each card's
`wefutCard.resourceIdSource` field and in the manifest.
