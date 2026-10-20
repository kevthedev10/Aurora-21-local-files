#!/usr/bin/env node
// Mutating integration checks for a disposable, explicitly attested test server.
// Never starts/stops servers, creates/resets databases, or controls the game.
import assert from 'node:assert/strict';
import http from 'node:http';
import { existsSync, readFileSync, realpathSync, statSync } from 'node:fs';
import { homedir } from 'node:os';
import path from 'node:path';
import { TextDecoder } from 'node:util';

const ROOT = '/ut/game/fifa21/';
const TEST_STATUS = '/localfut/test/status';
const MASS = `${ROOT}userMassInfo?screen=futlivemsggen4`;
const LIVE_PORTS = new Set([8090, 8081, 44321, 42230, 42231, 42232, 17502, 10853, 3216]);
const XI = ['GK', 'RB', 'CB', 'CB', 'LB', 'RM', 'CM', 'CM', 'LM', 'ST', 'ST'];
const isObject = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);
const int = (value) => Number.isSafeInteger(value);
const id = (value) => int(value) && value > 0;
const expect = (condition, message) => { if (!condition) throw new Error(message); };
const same = (left, right, message) => expect(JSON.stringify(left) === JSON.stringify(right), message);
const clone = (value) => structuredClone(value);
const normalized = (value) => process.platform === 'win32' ? value.toLowerCase() : value;
function inside(root, target) {
  const relative = path.relative(normalized(root), normalized(target));
  return relative === '' || (relative !== '..' && !relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative));
}

function options(argv) {
  const result = { timeoutMs: 5000 };
  const seen = new Set();
  for (let index = 0; index < argv.length; index++) {
    const flag = argv[index];
    expect(!seen.has(flag), 'Duplicate command-line option'); seen.add(flag);
    if (['--allow-mutations', '--resume-check', '--self-test', '--help'].includes(flag)) result[flag.slice(2)] = true;
    else if (['--base-url', '--state-root', '--timeout-ms'].includes(flag)) {
      const value = argv[++index];
      expect(value && !value.startsWith('--'), `Missing value for ${flag}`);
      result[flag.slice(2)] = value;
    } else throw new Error('Unsupported option; use --help');
  }
  if (result.help || result['self-test']) return result;
  expect(result['resume-check'] === true || result['allow-mutations'] === true, 'Refusing mutations without --allow-mutations');
  const match = /^http:\/\/127\.0\.0\.1:([0-9]+)\/?$/.exec(result['base-url'] || '');
  expect(match, 'Require explicit --base-url http://127.0.0.1:<test-port>; no DNS, redirects, or remote hosts');
  result.port = Number(match[1]);
  expect(int(result.port) && result.port >= 1024 && result.port <= 65535 && !LIVE_PORTS.has(result.port), 'Refusing a live/reserved server port');
  expect(typeof result['state-root'] === 'string' && path.isAbsolute(result['state-root'])
    && !/^[/\\]{2}/.test(result['state-root']), 'Require --state-root with an absolute local test directory');
  const timeout = result['timeout-ms'] ?? '5000';
  expect(/^[0-9]+$/.test(timeout) && Number(timeout) >= 1 && Number(timeout) <= 60000, 'Invalid --timeout-ms');
  result.timeoutMs = Number(timeout);
  return result;
}

function validateIsolation(status, args, canonical = realpathSync.native, stat = statSync, exists = existsSync) {
  expect(isObject(status) && status.version === 1 && status.testMode === true, 'Server did not attest isolated HTTP test mode');
  const localPath = (value) => {
    expect(typeof value === 'string' && path.isAbsolute(value) && !/^[/\\]{2}/.test(value), 'Test attestation contains a nonlocal path');
    return canonical(value);
  };
  const expectedRoot = localPath(args['state-root']);
  const runtimeRoot = localPath(status.runtimeRoot), database = localPath(status.dbPath);
  expect(normalized(runtimeRoot) === normalized(expectedRoot), 'Test runtimeRoot does not match --state-root');
  expect(inside(expectedRoot, database) && normalized(database) !== normalized(expectedRoot), 'Test database escapes the isolated runtime root');
  const productionRoots = new Set([
    path.join(homedir(), 'AppData', 'Local', 'FIFA21LocalFUT'),
    path.join(process.env.LOCALAPPDATA || path.join(homedir(), 'AppData', 'Local'), 'FIFA21LocalFUT'),
  ]);
  for (const productionPath of productionRoots) {
    const production = exists(productionPath) ? canonical(productionPath) : path.resolve(productionPath);
    expect(!inside(production, expectedRoot) && !inside(expectedRoot, production), 'Refusing a production runtime root or its ancestor/descendant');
    expect(!inside(production, database), 'Refusing the live database');
    const liveDatabase = path.join(production, 'data', 'localfut15.sqlite3');
    if (exists(liveDatabase)) {
      const live = stat(liveDatabase), test = stat(database);
      expect(!(live.ino && live.ino === test.ino && live.dev === test.dev), 'Test database is a hard link to the live database');
    }
  }
}

function allowed(method, route) {
  if (method === 'GET') return route === TEST_STATUS || route === MASS || [
    'v2/user/accountinfo', 'user/accountinfo', 'user/historical', 'club?count=10000', 'purchased/items', 'user/credits',
    'user', 'squad/active', 'loan/players', 'onboarding/kits', 'onboarding/badges',
    'clientdata/onboarding', 'clientdata/loanplayerreward', 'clientdata/squad',
  ].some((suffix) => route === ROOT + suffix);
  if (!['POST', 'PUT'].includes(method) || !route.startsWith(ROOT)) return false;
  return /^(?:user|user\/action\/ONBOARDING_COUNTRY_[0-9]+_SELECTED|loan\/player\/[0-9]+|item|squad\/[0-9]+\?onboarding=true|onboarding\/kits|onboarding\/badge\/[0-9]+|clientdata\/(?:onboarding|squad))$/.test(route.slice(ROOT.length));
}

function request(args, method, route, payload) {
  expect(allowed(method, route), 'Refusing a non-allowlisted test request');
  expect(method === 'GET' || (args['allow-mutations'] === true && !args['resume-check']), 'Refusing mutations in read-only mode');
  const body = payload === undefined ? '' : JSON.stringify(payload);
  return new Promise((resolve, reject) => {
    let finished = false, outgoing;
    const timer = setTimeout(() => finish(new Error('Isolated HTTP request timed out')), args.timeoutMs);
    function finish(error, value) {
      if (finished) return;
      finished = true; clearTimeout(timer); outgoing?.destroy();
      if (error) reject(error); else resolve(value);
    }
    try {
      outgoing = http.request({ hostname: '127.0.0.1', family: 4, port: args.port, method, path: route, agent: false,
        headers: { Accept: 'application/json', 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body) } }, (incoming) => {
        const chunks = []; let size = 0;
        incoming.on('data', (chunk) => {
          if (finished) return;
          size += chunk.length;
          if (size > 16 * 1024 * 1024) { finish(new Error('Isolated response exceeds 16 MiB')); return; }
          chunks.push(chunk);
        });
        incoming.once('end', () => {
          if (finished) return;
          try {
            expect(!incoming.headers['x-localfut15-unknown'], `${method} ${route}: unknown-route fallback`);
            expect(/^application\/json(?:\s*;|$)/i.test(incoming.headers['content-type'] || ''), `${route}: response is not JSON`);
            const value = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(Buffer.concat(chunks)));
            expect(isObject(value), `${route}: expected a JSON object`);
            finish(null, { status: incoming.statusCode, value });
          } catch (error) { finish(new Error(error instanceof SyntaxError ? `${route}: invalid JSON` : error.message)); }
        });
        incoming.once('aborted', () => finish(new Error('Isolated response was truncated')));
        incoming.once('error', () => finish(new Error('Isolated response failed')));
      });
      outgoing.once('error', () => finish(new Error('Isolated loopback connection failed')));
      outgoing.end(body);
    } catch { finish(new Error('Cannot create isolated loopback request')); }
  });
}

function entries(value, label) {
  expect(Array.isArray(value?.entries), `${label}: missing entries array`);
  const seen = new Set();
  return value.entries.map((entry) => {
    expect(isObject(entry) && int(entry.key) && int(entry.value)
      && entry.key >= -2147483648 && entry.key <= 2147483647
      && entry.value >= -2147483648 && entry.value <= 2147483647 && !seen.has(entry.key), `${label}: invalid or duplicate int32 entry`);
    seen.add(entry.key); return [entry.key, entry.value];
  }).sort((a, b) => a[0] - b[0]);
}
const entryValue = (pairs, key) => new Map(pairs).get(key);
function item(value, label) {
  expect(isObject(value) && id(value.id) && id(value.resourceId) && typeof value.itemType === 'string', `${label}: invalid native item`);
  return value;
}
function squadSlots(value) {
  expect(Array.isArray(value?.players), 'Squad response lacks players');
  return value.players.map((slot) => [slot.index, slot.itemData?.id ?? slot.itemId ?? 0,
    slot.itemData?.resourceId ?? 0]).sort((a, b) => a[0] - b[0]);
}
function validateStarter(value, catalog) {
  expect(value?.formation === 'f442' && value.players?.length === 23, 'Local starter policy requires f442 and all 23 slots');
  const indices = new Set(), ids = new Set(), resources = new Set(); let chosenNation = 0;
  for (const slot of value.players) {
    expect(int(slot.index) && slot.index >= 0 && slot.index < 23 && !indices.has(slot.index), 'Starter has an invalid or duplicate index');
    indices.add(slot.index);
    const player = item(slot.itemData, 'starter player'), asset = player.assetId ?? (player.resourceId & 0xffffff);
    const definition = catalog[String(asset)];
    expect(player.itemType === 'player' && !ids.has(player.id) && !resources.has(asset), 'Starter duplicates a player or item');
    ids.add(player.id); resources.add(asset);
    expect(definition && definition.rating < 75 && player.rating === definition.rating, 'Starter is not a modest mined player');
    expect(player.preferredPosition === definition.preferredPosition, 'Starter position differs from mined definition');
    if (slot.index < XI.length) expect(player.preferredPosition === XI[slot.index], 'Starter XI is not position-aware');
    if (definition.nation === 14) chosenNation++;
  }
  expect(chosenNation > 0, 'England country choice did not influence the starter squad');
}

function cosmeticGrants(before, after, grants) {
  const expectedIds = new Set(before.items.map((entry) => entry[0]));
  for (const grant of grants) {
    expect(id(grant.id), 'Cosmetic grant lacks an owned instance ID');
    const alreadyOwned = before.items.filter((entry) => entry[1] === grant.resourceId);
    expect(alreadyOwned.length <= 1 && (!alreadyOwned.length || alreadyOwned[0][0] === grant.id), 'Cosmetic grant duplicated an already-owned definition');
    expect(after.items.filter((entry) => entry[0] === grant.id && entry[1] === grant.resourceId
      && entry[3] === 'club' && entry[4] === grant.state).length === 1, 'Cosmetic grant lacks the exact owned/resource/active-state binding');
    expectedIds.add(grant.id);
  }
  same(after.items.map((entry) => entry[0]), [...expectedIds].sort((a, b) => a - b), 'Cosmetic grant added or removed unrelated owned IDs');
}

async function resumeCheck(args) {
  let count = 0;
  const get = async (route) => {
    const response = await request(args, 'GET', route); count++;
    expect(response.status === 200, `Resume GET ${route}: unexpected status ${response.status}`);
    return response.value;
  };
  validateIsolation(await get(TEST_STATUS), args);
  const mass = await get(MASS), active = await get(ROOT + 'squad/active'), user = await get(ROOT + 'user');
  const loan = entries(await get(ROOT + 'clientdata/loanplayerreward'), 'resume loan data');
  const onboarding = entries(await get(ROOT + 'clientdata/onboarding'), 'resume onboarding data');
  same(entries(mass.loanPlayerClientData, 'bulk loan data'), loan, 'Resume loan progress differs in bulk');
  same(entries(mass.onboardingClientData, 'bulk onboarding data'), onboarding, 'Resume onboarding progress differs in bulk');
  expect(id(entryValue(loan, 0)) && entryValue(onboarding, 0) === 3
    && [1, 2, 3].every((key) => id(entryValue(onboarding, key))), 'Completed loan/cosmetic/acknowledgement markers were not retained');
  same(squadSlots(mass.squad), squadSlots(active), 'Resume bulk squad bindings differ');
  same(mass.userInfo?.currencies, user.currencies, 'Resume bulk currencies differ');
  const club = await get(ROOT + 'club?count=10000');
  expect(Array.isArray(club.itemData) && club.clubCount === club.itemData.length, 'Incomplete resume club snapshot');
  const owned = new Map(club.itemData.map((value) => [item(value, 'resume owned item').id, value]));
  expect(owned.size === club.itemData.length, 'Resume club has duplicate instance IDs');
  const slots = squadSlots(active), indices = new Set(), ids = new Set();
  expect(slots.length === 23, 'Resume squad lacks 23 populated slots');
  for (const [index, ownedId, resourceId] of slots) {
    expect(int(index) && index >= 0 && index < 23 && !indices.has(index) && !ids.has(ownedId)
      && owned.get(ownedId)?.resourceId === resourceId, 'Resume squad binding is missing, duplicated, or not owned');
    indices.add(index); ids.add(ownedId);
  }
  expect(active.players.some((slot) => slot.index < 11
    && (slot.itemData.resourceId & 0xffffff) === entryValue(loan, 0)), 'Completed loan marker has no matching active-XI item');
  for (const state of ['activeHomeKit', 'activeAwayKit', 'activeBadge']) {
    const equipped = user.actives?.filter((value) => value.itemState === state);
    expect(equipped?.length === 1 && owned.get(equipped[0].id)?.resourceId === equipped[0].resourceId, 'Resume cosmetic is not backed by one owned item');
    expect(mass.userInfo.actives?.some((value) => value.id === equipped[0].id && value.itemState === state), 'Resume bulk cosmetic differs');
  }
  validateIsolation(await get(TEST_STATUS), args);
  console.log(`PASS read-only resume markers, loan-XI/cosmetic ownership and bulk consistency (${count} GETs; no mutations)`);
}

async function verify(args) {
  const catalog = JSON.parse(readFileSync(new URL('./runtime-source/localfut21/players.json', import.meta.url), 'utf8'));
  let calls = 0;
  async function send(method, route, payload, rejected = false) {
    if (method !== 'GET') await attest();
    const response = await request(args, method, route, payload); calls++;
    expect(typeof rejected === 'number' ? response.status === rejected
      : rejected ? response.status >= 400 && response.status < 500 : response.status >= 200 && response.status < 300,
      `${method} ${route}: unexpected HTTP status ${response.status}`);
    return response.value;
  }
  async function attest() { validateIsolation(await send('GET', TEST_STATUS), args); }
  const get = (route) => send('GET', ROOT + route);
  const put = (route, value) => send('PUT', ROOT + route, value);
  const post = (route, value) => send('POST', ROOT + route, value);
  async function snapshot() {
    const club = await get('club?count=10000'), unassigned = await get('purchased/items');
    expect(Array.isArray(club.itemData) && club.clubCount === club.itemData.length && Array.isArray(unassigned.itemData), 'Incomplete owned-item snapshot');
    const owned = [...club.itemData.map((value) => ({ ...value, pile: 'club' })),
      ...unassigned.itemData.map((value) => ({ ...value, pile: 'unassigned' }))];
    const ids = new Set();
    const items = owned.map((value) => {
      item(value, 'owned item'); expect(!ids.has(value.id), 'Owned item occurs in multiple piles'); ids.add(value.id);
      return [value.id, value.resourceId, value.assetId ?? 0, value.pile, value.itemState ?? ''];
    }).sort((a, b) => a[0] - b[0]);
    const credit = await get('user/credits');
    expect(Array.isArray(credit.currencies), 'Missing currency snapshot');
    const currencies = credit.currencies.map((currency) => {
      expect(typeof currency.name === 'string' && int(currency.funds) && int(currency.finalFunds), 'Invalid currency snapshot');
      return [currency.name, currency.funds, currency.finalFunds];
    }).sort(([a], [b]) => a.localeCompare(b));
    const active = await get('squad/active');
    return { items, currencies, slots: active.players ? squadSlots(active) : [],
      loan: entries(await get('clientdata/loanplayerreward'), 'loan client data'),
      onboarding: entries(await get('clientdata/onboarding'), 'onboarding client data'),
      squadData: entries(await get('clientdata/squad'), 'squad client data') };
  }
  async function invalid(method, route, payload) {
    const before = await snapshot();
    await send(method, ROOT + route, payload, true);
    same(await snapshot(), before, `${route}: invalid request changed owned state/progress`);
  }
  async function repeat(method, route, payload, expected) {
    const before = await snapshot(), response = await send(method, ROOT + route, payload);
    if (expected) for (const key of Object.keys(expected)) expect(response[key] === expected[key], `${route}: retry changed ${key}`);
    same(await snapshot(), before, `${route}: retry changed owned state/progress`);
  }
  async function bulkConsistency() {
    const mass = await send('GET', MASS), user = await get('user'), active = await get('squad/active');
    expect(isObject(mass.userInfo) && isObject(mass.squad) && isObject(mass.loanPlayers), 'Missing bulk bootstrap sections');
    same(squadSlots(mass.squad), squadSlots(active), 'Bulk and standalone squad bindings differ');
    same(mass.userInfo.currencies, user.currencies, 'Bulk and standalone currencies differ');
    same(entries(mass.loanPlayerClientData, 'bulk loan data'), entries(await get('clientdata/loanplayerreward'), 'loan data'), 'Bulk loan progress differs');
    same(entries(mass.onboardingClientData, 'bulk onboarding data'), entries(await get('clientdata/onboarding'), 'onboarding data'), 'Bulk onboarding progress differs');
    return mass;
  }

  await attest();
  const first = await snapshot(), account = await get('v2/user/accountinfo');
  expect(first.items.length === 0 && first.slots.every((slot) => slot[1] === 0), 'Refusing a nonempty test club; use a new isolated database');
  expect(Array.isArray(account.userAccountInfo?.personas) && account.userAccountInfo.personas.length > 0
    && account.userAccountInfo.personas.every((persona) => Array.isArray(persona.userClubList) && persona.userClubList.length === 0), 'Fresh account already exposes a club');
  await send('GET', MASS, undefined, 465);
  const historical = await get('user/historical');
  expect(typeof historical.clubName === 'string' && historical.clubName.length > 0
    && typeof historical.clubAbbr === 'string' && historical.clubAbbr.length > 0
    && historical.isReturningUser === false && Array.isArray(historical.returningUserRewards), 'Fresh historical response has the wrong native shape');
  await invalid('POST', 'user/action/ONBOARDING_COUNTRY_999_SELECTED');
  await post('user/action/ONBOARDING_COUNTRY_1_SELECTED');
  const createBody = { useFut1Data: false, clubName: 'Test FC', clubAbbr: 'TST', purchased: false };
  const created = await post('user', createBody);
  expect(typeof created.bonusPacks === 'boolean' && isObject(created.login) && isObject(created.userData)
    && Array.isArray(created.starterPack) && created.starterPack.length > 0, 'CreateUser response has the wrong native envelope');
  validateStarter(created.squad, catalog);
  const afterCreation = await snapshot(), ownedIds = new Set(afterCreation.items.map((entry) => entry[0]));
  expect(created.starterPack.every((value) => ownedIds.has(item(value, 'starter pack item').id)), 'StarterPack contains unpersisted item IDs');
  const starterIds = new Set(created.starterPack.map((value) => value.id));
  expect(starterIds.size === created.starterPack.length && created.squad.players.every((slot) => starterIds.has(slot.itemData.id)), 'StarterPack omits squad players or repeats item IDs');
  same(squadSlots(created.squad), squadSlots(await get('squad/active')), 'CreateUser squad was not persisted');
  await repeat('POST', 'user', createBody);
  await bulkConsistency();
  console.log('PASS empty club, country action, native creation envelope, modest position-aware 23-player squad and retry safety');

  const offers = await get('loan/players');
  expect(Array.isArray(offers.loans) && offers.loans.length >= 5, 'Loan choices are missing');
  const definitions = new Set();
  for (const offer of offers.loans) {
    expect(id(offer.defId) && !definitions.has(offer.defId) && int(offer.ranking) && offer.ranking >= 1 && offer.ranking <= 10
      && int(offer.loans) && offer.loans > 0, 'Loan offer lacks unique defId/eligible ranking/duration');
    definitions.add(offer.defId); expect(item(offer.itemData, 'loan preview').itemType === 'player', 'Loan preview is not a player');
  }
  const active = await get('squad/active');
  const selectedSlot = active.players.find((slot) => slot.index === 0);
  const eligible = offers.loans.filter((offer) => offer.itemData.preferredPosition === selectedSlot.itemData.preferredPosition);
  expect(eligible.length >= 5, 'Selected occupied position lacks five eligible loan choices');
  const offer = eligible[0], invalidId = 2147483647;
  expect(!definitions.has(invalidId), 'Invalid loan fixture collides with an offer');
  await invalid('PUT', `loan/player/${invalidId}`);
  const signed = item(await put(`loan/player/${offer.defId}`), 'signed loan');
  expect(!Object.hasOwn(signed, 'itemData') && signed.itemType === 'player'
    && signed.resourceId === offer.itemData.resourceId && !ownedIds.has(signed.id)
    && signed.loans === offer.loans && signed.preferredPosition === offer.itemData.preferredPosition, 'SignLoanPlayer must return the chosen bare owned loan item and duration');
  let state = await snapshot();
  expect(state.items.length === afterCreation.items.length + 1 && !entryValue(state.loan, 0), 'Loan signing prematurely completed selection or granted extra items');
  await repeat('PUT', `loan/player/${offer.defId}`, undefined, { id: signed.id });
  await put('item', { itemData: [{ id: signed.id, pile: 'club' }] });
  state = await snapshot();
  expect(state.items.some((entry) => entry[0] === signed.id && entry[3] === 'club') && !entryValue(state.loan, 0), 'Loan club move failed or prematurely completed selection');
  const replacedId = selectedSlot.itemData.id;
  const save = { id: active.id, squadName: active.squadName, formation: active.formation, active: true,
    players: active.players.map((slot) => ({ index: slot.index, kitNumber: slot.kitNumber ?? 0,
      itemData: { id: slot.index === selectedSlot.index ? signed.id : slot.itemData.id, dream: false } })) };
  await put(`squad/${active.id}?onboarding=true`, save);
  state = await snapshot();
  expect(state.slots.length === 23 && state.slots.find((slot) => slot[0] === 0)?.[1] === signed.id
    && !state.slots.some((slot) => slot[1] === replacedId)
    && state.items.some((entry) => entry[0] === replacedId && entry[3] === 'club'), 'Loan swap lost the replaced starter or failed to bind the loan');
  same(state.slots.map((slot) => slot.slice(0, 2)), save.players.map((slot) => [slot.index, slot.itemData.id]).sort((a, b) => a[0] - b[0]), 'Loan swap changed unrelated squad bindings');
  same(state.items.map((entry) => entry[0]), [...ownedIds, signed.id].sort((a, b) => a - b), 'Loan workflow lost or duplicated owned items');
  expect(entryValue(state.loan, 0) === (offer.itemData.resourceId & 0xffffff), 'Loan completion marker is not the native 32-bit player definition ID');
  await repeat('PUT', `squad/${active.id}?onboarding=true`, save);
  await bulkConsistency();
  console.log('PASS ranked defId loan choices, bare grant, club move, saved swap, progress and retries');

  const kits = await get('onboarding/kits');
  expect(Array.isArray(kits.homeItemDataList) && kits.homeItemDataList.length > 0
    && Array.isArray(kits.awayItemDataList) && kits.awayItemDataList.length > 0, 'Native kit choice arrays are missing');
  const home = item(kits.homeItemDataList[0], 'home choice'), away = item(kits.awayItemDataList[0], 'away choice');
  expect(home.itemType === 'kit' && away.itemType === 'kit' && home.resourceId !== away.resourceId, 'Invalid paired kit choices');
  await invalid('POST', 'onboarding/kits', { homeKitId: home.resourceId, awayKitId: invalidId });
  const beforeKits = await snapshot(), kitBody = { homeKitId: home.resourceId, awayKitId: away.resourceId };
  const grantedKits = await post('onboarding/kits', kitBody);
  expect(id(grantedKits.homeKitId) && id(grantedKits.awayKitId) && grantedKits.homeKitId !== grantedKits.awayKitId, 'Kit grant lacks two owned IDs');
  state = await snapshot();
  cosmeticGrants(beforeKits, state, [
    { id: grantedKits.homeKitId, resourceId: home.resourceId, state: 'activeHomeKit' },
    { id: grantedKits.awayKitId, resourceId: away.resourceId, state: 'activeAwayKit' },
  ]);
  expect(entryValue(state.onboarding, 1) && entryValue(state.onboarding, 2), 'Paired kit grant lacks resume markers');
  await repeat('POST', 'onboarding/kits', kitBody, grantedKits);
  await bulkConsistency();

  const badges = await get('onboarding/badges');
  expect(Array.isArray(badges.badgeItemDataList) && badges.badgeItemDataList.length > 0, 'Native badge choice array is missing');
  const badge = item(badges.badgeItemDataList[0], 'badge choice');
  await invalid('POST', `onboarding/badge/${invalidId}`);
  const beforeBadge = await snapshot(), grantedBadge = await post(`onboarding/badge/${badge.resourceId}`);
  expect(id(grantedBadge.badgeId), 'Badge grant lacks an owned ID');
  state = await snapshot();
  cosmeticGrants(beforeBadge, state, [{ id: grantedBadge.badgeId, resourceId: badge.resourceId, state: 'activeBadge' }]);
  expect(entryValue(state.onboarding, 3) && entryValue(state.onboarding, 0) === 1, 'Badge grant lacks completion/just-finished markers');
  await repeat('POST', `onboarding/badge/${badge.resourceId}`, undefined, grantedBadge);
  const completed = await bulkConsistency(), actives = completed.userInfo.actives;
  expect(Array.isArray(actives) && [grantedKits.homeKitId, grantedKits.awayKitId, grantedBadge.badgeId]
    .every((ownedId) => actives.some((value) => value.id === ownedId)), 'Granted cosmetics are not equipped in native user bootstrap');
  console.log('PASS paired kits, badge, owned/equipped IDs, progress and retry-safe grants');

  await put('clientdata/squad', { entries: [{ key: 5, value: 0 }] });
  expect(entryValue(entries(await get('clientdata/squad'), 'squad client data'), 5) === 0, 'Observed squad client-data write was not persisted');
  const acknowledgement = { entries: [{ key: 0, value: 3 }] };
  await put('clientdata/onboarding', acknowledgement);
  state = await snapshot();
  expect(entryValue(state.onboarding, 0) === 3 && entryValue(state.onboarding, 3), 'Just-finished acknowledgement lost completion state');
  await repeat('PUT', 'clientdata/onboarding', acknowledgement);
  await invalid('PUT', 'clientdata/onboarding', { entries: [{ key: 0, value: '3' }] });
  await bulkConsistency();
  console.log(`PASS typed client-data persistence and bulk consistency (${calls} isolated HTTP requests)`);
  console.log('Test state was intentionally retained. Native UI transitions and restart-resume still require separate verification.');
}

function selfTest() {
  const root = path.resolve('isolated-onboarding-fixture');
  const valid = ['--base-url', 'http://127.0.0.1:18090', '--state-root', root, '--allow-mutations'];
  assert.equal(options(valid).port, 18090);
  assert.equal(options([...valid.slice(0, -1), '--resume-check'])['resume-check'], true);
  for (const port of LIVE_PORTS) assert.throws(() => options(valid.map((value) => value.replace(':18090', `:${port}`))));
  for (const base of ['http://localhost:18090', 'http://127.0.0.1:18090/path', 'http://example.com:18090', 'https://127.0.0.1:18090', 'http://127.0.0.1'])
    assert.throws(() => options(valid.map((value) => value === valid[1] ? base : value)));
  assert.throws(() => options(valid.slice(0, -1)));
  assert.throws(() => options(['--allow-mutations', '--base-url', valid[1]]));
  assert.equal(allowed('POST', ROOT + 'purchased/items'), false);
  assert.equal(allowed('DELETE', ROOT + 'item/1'), false);
  assert.equal(allowed('GET', 'http://example.com/'), false);
  assert.equal(allowed('PUT', ROOT + 'squad/1?onboarding=true'), true);
  assert.throws(() => entries({ entries: [{ key: 0, value: '3' }] }, 'fixture'));
  assert.throws(() => entries({ entries: [{ key: 0, value: 900000000000 }] }, 'fixture'));
  assert.throws(() => entries({ entries: [{ key: 0, value: 1 }, { key: 0, value: 2 }] }, 'fixture'));
  assert.throws(() => item({ itemData: { id: 1, resourceId: 2, itemType: 'player' } }, 'fixture'));
  assert.equal(inside(root, path.join(root, 'data', 'test.db')), true);
  assert.equal(inside(root, path.resolve('other-fixture', 'test.db')), false);
  assert.throws(() => validateIsolation({ version: 1, testMode: false }, { 'state-root': root }));
  const status = { version: 1, testMode: true, runtimeRoot: root, dbPath: path.join(root, 'data', 'test.db') };
  const canonical = (value) => path.resolve(value);
  const fakeStat = (value) => ({ dev: 1, ino: inside(root, value) ? 2 : 1 });
  validateIsolation(status, { 'state-root': root }, canonical, fakeStat, () => true);
  assert.throws(() => validateIsolation({ ...status, runtimeRoot: path.resolve('another-test') }, { 'state-root': root }, canonical, fakeStat, () => true));
  assert.throws(() => validateIsolation({ ...status, dbPath: path.resolve('outside-test.db') }, { 'state-root': root }, canonical, fakeStat, () => true));
  assert.throws(() => validateIsolation(status, { 'state-root': root }, canonical, () => ({ dev: 1, ino: 1 }), () => true));
  const production = path.join(homedir(), 'AppData', 'Local', 'FIFA21LocalFUT');
  assert.throws(() => validateIsolation({ ...status, runtimeRoot: production, dbPath: path.join(production, 'data', 'test.db') },
    { 'state-root': production }, canonical, fakeStat, () => true));
  const catalog = {}, players = Array.from({ length: 23 }, (_, index) => {
    const definition = { rating: 60, preferredPosition: XI[index % 11], nation: index === 0 ? 14 : 18 };
    catalog[String(index + 1)] = definition;
    return { index, itemData: { id: index + 101, resourceId: index + 1, assetId: index + 1, itemType: 'player', ...definition } };
  });
  validateStarter({ formation: 'f442', players }, catalog);
  const wrongPosition = clone(players); wrongPosition[0].itemData.preferredPosition = 'ST';
  assert.throws(() => validateStarter({ formation: 'f442', players: wrongPosition }, catalog));
  const duplicate = clone(players); duplicate[1].itemData.id = duplicate[0].itemData.id;
  assert.throws(() => validateStarter({ formation: 'f442', players: duplicate }, catalog));
  const grant = { id: 1, resourceId: 100, state: 'activeHomeKit' };
  const cosmetic = { items: [[1, 100, 100, 'club', 'activeHomeKit']] };
  cosmeticGrants(cosmetic, cosmetic, [grant]);
  cosmeticGrants({ items: [] }, cosmetic, [grant]);
  assert.throws(() => cosmeticGrants(cosmetic, { items: [...cosmetic.items, [2, 100, 100, 'club', 'activeHomeKit']] }, [{ ...grant, id: 2 }]));
  assert.throws(() => request({ 'resume-check': true, 'allow-mutations': true }, 'PUT', ROOT + 'item', {}));
  console.log('PASS offline isolation/options, request allowlist, int32 progress, bare-item and starter-policy fixtures; no HTTP calls');
}

async function main() {
  const args = options(process.argv.slice(2));
  if (args.help) {
    console.log('Usage: node localfut/verify-fut-onboarding.mjs --base-url http://127.0.0.1:18090 --state-root <isolated-runtime-root> --allow-mutations [--timeout-ms 5000]');
    console.log('After an external restart, replace --allow-mutations with --resume-check for read-only completion/ownership checks.');
    console.log('Offline safety/contract fixtures: node localfut/verify-fut-onboarding.mjs --self-test');
    console.log('Requires a fresh server started in explicit HTTP-only test mode. Never run against a saved club. No process control or resets.');
    return;
  }
  if (args['self-test']) { selfTest(); return; }
  if (args['resume-check']) { await resumeCheck(args); return; }
  await verify(args);
}
main().catch((error) => { console.error(`FAIL ${error.message}`); process.exitCode = 1; });
