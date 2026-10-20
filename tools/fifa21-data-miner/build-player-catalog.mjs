import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, renameSync, statSync, writeFileSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const TOOL_ROOT = dirname(fileURLToPath(import.meta.url));
const REPOSITORY_ROOT = resolve(TOOL_ROOT, "../..");

const ATTRIBUTES = [
  "acceleration", "sprintspeed", "agility", "balance", "jumping", "stamina",
  "strength", "reactions", "aggression", "interceptions", "positioning",
  "vision", "ballcontrol", "crossing", "dribbling", "finishing",
  "freekickaccuracy", "headingaccuracy", "longpassing", "shortpassing",
  "marking", "shotpower", "longshots", "standingtackle", "slidingtackle",
  "volleys", "curve", "penalties", "gkdiving", "gkhandling", "gkkicking",
  "gkreflexes", "gkpositioning",
];

const OUTFIELD_FACE = ["speed", "shooting", "passing", "dribbling", "defending", "physical"];
const GOALKEEPER_FACE = ["gkdiving", "gkhandling", "gkkicking", "gkreflexes", "gkacceleration", "gkpositioning"];
const POSITION_NAMES = new Map([
  [0, "GK"], [2, "RWB"], [3, "RB"], [5, "CB"], [7, "LB"], [8, "LWB"],
  [10, "CDM"], [12, "RM"], [14, "CM"], [16, "LM"], [18, "CAM"],
  [20, "RF"], [21, "CF"], [22, "LF"], [23, "RW"], [25, "ST"], [27, "LW"],
]);
const NATIONAL_TEAM_LEAGUE_ID = 78;
const UNUSED_TEAM_POSITION = 29;

function parseOptions(argv) {
  const defaults = {
    tables: join(REPOSITORY_ROOT, "localfut/runtime/mined/tables/cards_ng_db"),
    inputManifest: join(REPOSITORY_ROOT, "localfut/runtime/mined/input-assets.json"),
    rawData: join(REPOSITORY_ROOT, "localfut/runtime/mined/data"),
    runtimeCatalog: join(REPOSITORY_ROOT, "localfut/runtime-source/localfut21/players.json"),
    nativeData: join(REPOSITORY_ROOT, "localfut/native-data"),
    sourceArchive: null,
  };
  const optionNames = new Map([
    ["--tables", "tables"],
    ["--input-manifest", "inputManifest"],
    ["--raw-data", "rawData"],
    ["--runtime-catalog", "runtimeCatalog"],
    ["--native-data", "nativeData"],
    ["--source-archive", "sourceArchive"],
  ]);

  for (let index = 0; index < argv.length; index += 2) {
    const property = optionNames.get(argv[index]);
    const value = argv[index + 1];
    if (!property || !value) {
      throw new Error(`Unknown or incomplete argument: ${argv[index] ?? "<missing>"}`);
    }
    defaults[property] = resolve(value);
  }
  return defaults;
}

function readJson(path) {
  if (!existsSync(path)) {
    throw new Error(`Required input is missing: ${path}`);
  }
  return JSON.parse(readFileSync(path, "utf8"));
}

function canonicalJson(value) {
  return `${JSON.stringify(value, null, 2)}\n`;
}

function sha256Bytes(bytes) {
  return createHash("sha256").update(bytes).digest("hex").toUpperCase();
}

function sha256File(path) {
  return sha256Bytes(readFileSync(path));
}

function writeJsonAtomic(path, value) {
  const bytes = Buffer.from(canonicalJson(value), "utf8");
  const temporary = `${path}.tmp-${process.pid}`;
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(temporary, bytes);
  renameSync(temporary, path);
  return { byteLength: bytes.length, sha256: sha256Bytes(bytes) };
}

function tablePath(options, tableName) {
  return join(options.tables, `${tableName}.json`);
}

function tableInput(options, tableName) {
  const path = tablePath(options, tableName);
  return {
    path,
    rows: readJson(path),
    byteLength: statSync(path).size,
    sha256: sha256File(path),
  };
}

function normalizeName(value) {
  return String(value ?? "").trim().replace(/\s+/g, " ");
}

function groupBy(rows, key) {
  const grouped = new Map();
  for (const row of rows) {
    const value = Number(row[key]);
    const entries = grouped.get(value) ?? [];
    entries.push(row);
    grouped.set(value, entries);
  }
  return grouped;
}

function roundToEven(value) {
  const floor = Math.floor(value);
  const fraction = value - floor;
  if (Math.abs(fraction - 0.5) < Number.EPSILON * Math.max(1, Math.abs(value))) {
    return floor % 2 === 0 ? floor : floor + 1;
  }
  return Math.round(value);
}

function buildWeights(mappingRows) {
  if (mappingRows.length !== ATTRIBUTES.length) {
    throw new Error(`Expected ${ATTRIBUTES.length} attribute-weight rows, found ${mappingRows.length}.`);
  }
  const byId = new Map(mappingRows.map((row) => [Number(row.attributeid), row]));
  for (let attributeId = 0; attributeId < ATTRIBUTES.length; attributeId += 1) {
    if (!byId.has(attributeId)) {
      throw new Error(`Attribute-weight row ${attributeId} is missing.`);
    }
  }
  for (const stat of [...OUTFIELD_FACE, ...GOALKEEPER_FACE]) {
    const total = [...byId.values()].reduce((sum, row) => sum + Math.max(0, Number(row[stat])), 0);
    if (total !== 100) {
      throw new Error(`Native weights for ${stat} sum to ${total}, not 100.`);
    }
  }
  return byId;
}

function cardFace(player, weights) {
  const faceNames = Number(player.preferredposition1) === 0 ? GOALKEEPER_FACE : OUTFIELD_FACE;
  return faceNames.map((stat) => {
    let total = 0;
    for (let attributeId = 0; attributeId < ATTRIBUTES.length; attributeId += 1) {
      const weight = Number(weights.get(attributeId)[stat]);
      const attribute = Number(player[ATTRIBUTES[attributeId]]);
      if (!Number.isFinite(attribute)) {
        throw new Error(`Player ${player.playerid} has an invalid ${ATTRIBUTES[attributeId]} value.`);
      }
      if (weight > 0) {
        total += weight * attribute;
      }
    }
    return Math.max(1, Math.min(99, roundToEven(total / 100)));
  });
}

function selectAffiliation(playerId, linksByPlayer, leagueByTeam, knownTeams) {
  const links = (linksByPlayer.get(playerId) ?? [])
    .filter((link) => knownTeams.has(Number(link.teamid)))
    .map((link) => ({
      artificialKey: Number(link.artificialkey),
      leagueId: leagueByTeam.get(Number(link.teamid)),
      position: Number(link.position),
      teamId: Number(link.teamid),
    }))
    .filter((link) => Number.isInteger(link.leagueId));

  let candidates = links.filter((link) => link.leagueId !== NATIONAL_TEAM_LEAGUE_ID);
  if (candidates.length === 0) {
    return { affiliation: null, reason: "no native club/league affiliation" };
  }
  const activeCandidates = candidates.filter((link) => link.position !== UNUSED_TEAM_POSITION);
  if (activeCandidates.length > 0) {
    candidates = activeCandidates;
  }

  const affiliations = new Map();
  for (const candidate of candidates) {
    affiliations.set(`${candidate.teamId}:${candidate.leagueId}`, candidate);
  }
  const unique = [...affiliations.values()].sort(
    (left, right) => left.artificialKey - right.artificialKey || left.teamId - right.teamId,
  );
  if (unique.length !== 1) {
    return { affiliation: null, reason: "ambiguous native club/league affiliation" };
  }
  return { affiliation: unique[0], reason: null };
}

function validateNativeInputs(options, inputManifest) {
  const cards = inputManifest.find((entry) => entry.Database === "db/cards_ng_db.db");
  if (!cards) {
    throw new Error("input-assets.json does not describe db/cards_ng_db.db.");
  }
  const pairs = [
    { relativePath: cards.Database, expectedBytes: cards.DatabaseBytes, expectedSha256: cards.DatabaseSha256 },
    { relativePath: cards.Metadata, expectedBytes: cards.MetadataBytes, expectedSha256: cards.MetadataSha256 },
  ];
  return pairs.map((entry) => {
    const path = join(options.rawData, entry.relativePath);
    if (!existsSync(path)) {
      throw new Error(`Pinned native input is missing: ${path}`);
    }
    const actualBytes = statSync(path).size;
    const actualSha256 = sha256File(path);
    if (actualBytes !== Number(entry.expectedBytes) || actualSha256 !== entry.expectedSha256) {
      throw new Error(`Pinned native input does not match input-assets.json: ${path}`);
    }
    return {
      path: entry.relativePath.replaceAll("\\", "/"),
      byteLength: actualBytes,
      sha256: actualSha256,
    };
  });
}

function main() {
  const options = parseOptions(process.argv.slice(2));
  const inputManifest = readJson(options.inputManifest);
  const nativeInputs = validateNativeInputs(options, inputManifest);
  const tableNames = [
    "players", "playernames", "playerattributesmapping", "teamplayerlinks",
    "leagueteamlinks", "teams", "leagues", "nations",
  ];
  const tables = Object.fromEntries(tableNames.map((name) => [name, tableInput(options, name)]));

  const players = tables.players.rows;
  const playerIds = new Set(players.map((player) => Number(player.playerid)));
  if (playerIds.size !== players.length) {
    throw new Error("The native players table contains duplicate player IDs.");
  }

  const names = new Map(tables.playernames.rows.map((row) => [Number(row.nameid), normalizeName(row.name)]));
  const linksByPlayer = groupBy(tables.teamplayerlinks.rows, "playerid");
  const knownTeams = new Set(tables.teams.rows.map((team) => Number(team.teamid)));
  const knownLeagues = new Set(tables.leagues.rows.map((league) => Number(league.leagueid)));
  const knownNations = new Set(tables.nations.rows.map((nation) => Number(nation.nationid)));
  const leagueByTeam = new Map();
  for (const link of tables.leagueteamlinks.rows) {
    const teamId = Number(link.teamid);
    const leagueId = Number(link.leagueid);
    if (leagueByTeam.has(teamId) && leagueByTeam.get(teamId) !== leagueId) {
      throw new Error(`Team ${teamId} maps to more than one native league.`);
    }
    leagueByTeam.set(teamId, leagueId);
  }
  const weights = buildWeights(tables.playerattributesmapping.rows);

  const catalogEntries = [];
  const quarantine = [];
  const reasonCounts = {};
  for (const player of [...players].sort((left, right) => Number(left.playerid) - Number(right.playerid))) {
    const playerId = Number(player.playerid);
    const commonName = names.get(Number(player.commonnameid)) ?? "";
    const firstName = names.get(Number(player.firstnameid)) ?? "";
    const lastName = names.get(Number(player.lastnameid)) ?? "";
    const jerseyName = names.get(Number(player.playerjerseynameid)) ?? "";
    const displayName = commonName || normalizeName(`${firstName} ${lastName}`) || jerseyName;
    const { affiliation, reason: affiliationReason } = selectAffiliation(
      playerId,
      linksByPlayer,
      leagueByTeam,
      knownTeams,
    );
    const positionCode = Number(player.preferredposition1);
    const nationality = Number(player.nationality);
    const rating = Number(player.overallrating);
    const reasons = [];

    if (!Number.isInteger(playerId) || playerId <= 0 || playerId >= 0x1000000) reasons.push("invalid native player ID");
    if (!displayName) reasons.push("no decodable native display name");
    if (affiliationReason) reasons.push(affiliationReason);
    if (!POSITION_NAMES.has(positionCode)) reasons.push("unknown native preferred-position code");
    if (!Number.isInteger(rating) || rating < 1 || rating > 99) reasons.push("invalid native overall rating");
    if (!knownNations.has(nationality)) reasons.push("unknown native nationality");
    if (!Number.isInteger(Number(player.birthdate)) || Number(player.birthdate) <= 0) reasons.push("missing native birth date");
    if (!Number.isInteger(Number(player.height)) || Number(player.height) < 140 || Number(player.height) > 230) reasons.push("invalid native height");
    if (!Number.isInteger(Number(player.weight)) || Number(player.weight) < 40 || Number(player.weight) > 200) reasons.push("invalid native weight");
    if (affiliation && !knownLeagues.has(affiliation.leagueId)) reasons.push("unknown native league");

    if (reasons.length > 0) {
      for (const reason of reasons) reasonCounts[reason] = (reasonCounts[reason] ?? 0) + 1;
      quarantine.push({
        playerId,
        reasons,
        native: {
          commonNameId: Number(player.commonnameid),
          firstNameId: Number(player.firstnameid),
          lastNameId: Number(player.lastnameid),
          jerseyNameId: Number(player.playerjerseynameid),
          nationality,
          preferredPosition: positionCode,
          rating,
        },
      });
      continue;
    }

    catalogEntries.push([String(playerId), {
      name: displayName,
      rating,
      preferredPosition: POSITION_NAMES.get(positionCode),
      leagueId: affiliation.leagueId,
      teamid: affiliation.teamId,
      nation: nationality,
      assetId: playerId,
      resourceId: playerId,
      cardsubtypeid: 0,
      rareflag: 0,
      face: cardFace(player, weights),
    }]);
  }

  if (catalogEntries.length + quarantine.length !== players.length) {
    throw new Error("Catalog accounting does not cover every native player row.");
  }

  const catalog = Object.fromEntries(catalogEntries);
  const catalogOutput = writeJsonAtomic(options.runtimeCatalog, catalog);
  const quarantinePath = join(options.nativeData, "player-catalog-quarantine.json");
  const quarantineOutput = writeJsonAtomic(quarantinePath, quarantine);
  const manifestPath = join(options.nativeData, "player-catalog-manifest.json");
  const sourceArchive = options.sourceArchive
    ? {
        name: options.sourceArchive.split(/[\\/]/).at(-1),
        byteLength: statSync(options.sourceArchive).size,
        sha256: sha256File(options.sourceArchive),
      }
    : null;
  const manifest = {
    schemaVersion: 1,
    source: {
      description: "FIFA 21 native T3DB tables; no website data",
      archive: sourceArchive,
      inputs: nativeInputs,
      tables: Object.fromEntries(tableNames.map((name) => [name, {
        rows: tables[name].rows.length,
        byteLength: tables[name].byteLength,
        sha256: tables[name].sha256,
      }])),
    },
    rules: {
      baseResourceId: "players.playerid",
      displayName: "common name, then first plus last name, then jersey name",
      affiliation: `exclude league ${NATIONAL_TEAM_LEAGUE_ID}; prefer teamplayerlinks.position != ${UNUSED_TEAM_POSITION}`,
      cardFace: "playerattributesmapping weights over the native 33-attribute order",
      rarity: "base/common only; promotional revisions are not inferred",
    },
    counts: {
      nativePlayers: players.length,
      catalogPlayers: catalogEntries.length,
      quarantinedPlayers: quarantine.length,
      quarantineReasons: Object.fromEntries(Object.entries(reasonCounts).sort(([left], [right]) => left.localeCompare(right))),
    },
    outputs: {
      runtimeCatalog: {
        path: relative(REPOSITORY_ROOT, options.runtimeCatalog).replaceAll("\\", "/"),
        players: catalogEntries.length,
        ...catalogOutput,
      },
      quarantine: {
        path: relative(REPOSITORY_ROOT, quarantinePath).replaceAll("\\", "/"),
        players: quarantine.length,
        ...quarantineOutput,
      },
    },
    generator: "tools/fifa21-data-miner/build-player-catalog.mjs",
  };
  const manifestOutput = writeJsonAtomic(manifestPath, manifest);

  console.log(`Cataloged ${catalogEntries.length.toLocaleString()} native FIFA 21 players.`);
  console.log(`Quarantined ${quarantine.length.toLocaleString()} rows; see ${quarantinePath}.`);
  console.log(`Manifest ${manifestOutput.sha256} written to ${manifestPath}.`);
}

main();
