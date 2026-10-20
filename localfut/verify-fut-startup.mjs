#!/usr/bin/env node
// GET-only emulator contract check, not proof that the FIFA client consumed it.
// Offline: node localfut/verify-fut-startup.mjs --self-test
import assert from 'node:assert/strict';
import http from 'node:http';
import { TextDecoder } from 'node:util';

const ROOT = '/ut/game/fifa21';
const MASS_ROUTE = 'userMassInfo?screen=futlivemsggen4';
const ROUTES = ['club?count=10000', 'user/credits', 'user/accountinfo', 'v2/user/accountinfo', 'user', 'squad/active', 'loan/players', MASS_ROUTE];
const POSITIONS = ['ST', 'LM', 'CM', 'RM', 'LB', 'CB', 'RB', 'GK'];
const SQUAD_TYPES = ['REGULAR_SQUAD', 'DREAM_SQUAD', 'DRAFT_SQUAD'];
const object = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);
const integer = (value) => Number.isSafeInteger(value) && value >= 0;
const positive = (value) => integer(value) && value > 0;
function expect(condition, message) { if (!condition) throw new Error(message); }
function fields(value, names, predicate, label) {
  expect(object(value), `${label}: expected an object`);
  for (const key of names.split(' ')) expect(predicate(value[key]), `${label}: invalid ${key}`);
}

function getJson(route, { httpPort = 8090, timeoutMs = 3000 } = {}) {
  expect(ROUTES.includes(route), 'Route is not allowlisted');
  return new Promise((resolve, reject) => {
    let done = false, outgoing;
    const timer = setTimeout(() => finish(new Error('Loopback GET timed out')), timeoutMs);
    function finish(error, value) {
      if (done) return;
      done = true; clearTimeout(timer); outgoing?.destroy();
      if (error) reject(error); else resolve(value);
    }
    outgoing = http.request({ hostname: '127.0.0.1', family: 4, port: httpPort, method: 'GET',
      path: `${ROOT}/${route}`, agent: false, headers: { Accept: 'application/json' } }, (incoming) => {
      let size = 0;
      const chunks = [];
      incoming.on('data', (chunk) => {
        if (done) return;
        size += chunk.length;
        if (size > 8 * 1024 * 1024) { finish(new Error('Loopback response exceeds 8 MiB')); return; }
        chunks.push(chunk);
      });
      incoming.once('end', () => {
        if (done) return;
        try {
          expect(incoming.statusCode === 200, `${route}: expected HTTP 200 (redirects are not followed)`);
          expect(/^application\/json(?:\s*;|$)/i.test(incoming.headers['content-type'] || ''), `${route}: expected JSON`);
          const value = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(Buffer.concat(chunks)));
          expect(object(value) && Object.keys(value).length > 0, `${route}: empty or invalid response object`);
          finish(null, value);
        } catch { finish(new Error(`${route}: invalid HTTP/JSON response`)); }
      });
      incoming.once('aborted', () => finish(new Error(`${route}: truncated response`)));
      incoming.once('error', () => finish(new Error(`${route}: response failed`)));
    });
    outgoing.once('error', () => finish(new Error('Loopback GET connection failed')));
    outgoing.end(); // No request body, auth exchange, grant, reset, or returned-URL fetch.
  });
}

function identity(value) {
  expect(object(value?.userAccountInfo), 'accountinfo: missing userAccountInfo');
  const personas = value.userAccountInfo.personas;
  expect(Array.isArray(personas) && personas.length === 1, 'accountinfo: expected one local persona');
  const persona = personas[0];
  fields(persona, 'personaId', positive, 'accountinfo persona');
  fields(persona, 'personaName', (x) => typeof x === 'string' && x.length > 0, 'accountinfo persona');
  expect(Array.isArray(persona.userClubList) && persona.userClubList.length === 1, 'accountinfo: missing local club');
  const club = persona.userClubList[0];
  fields(club, 'assetId', positive, 'accountinfo club');
  fields(club, 'clubName clubAbbr', (x) => typeof x === 'string' && x.length > 0, 'accountinfo club');
  return { personaId: persona.personaId, personaName: persona.personaName,
    clubId: club.assetId, clubName: club.clubName, clubAbbr: club.clubAbbr };
}

function currencies(value) {
  expect(Array.isArray(value?.currencies) && value.currencies.length > 0, 'credits: missing currencies');
  const seen = new Set();
  const result = value.currencies.map((entry) => {
    fields(entry, 'funds finalFunds', integer, 'currency');
    expect(typeof entry.name === 'string' && entry.name && !seen.has(entry.name), 'credits: duplicate or invalid currency');
    seen.add(entry.name);
    return [entry.name, entry.funds, entry.finalFunds];
  }).sort(([a], [b]) => a.localeCompare(b));
  expect(seen.has('coins') && seen.has('points'), 'credits: missing native coins/points currencies');
  return result;
}

function inventory(value) {
  expect(Array.isArray(value?.itemData) && value.itemData.length > 0, 'club: missing itemData');
  expect(integer(value.clubCount) && value.clubCount === value.itemData.length, 'club: incomplete inventory snapshot');
  const seen = new Set();
  return value.itemData.map((item) => {
    fields(item, 'id resourceId assetId', positive, 'club item');
    expect(!seen.has(item.id), 'club: duplicate item ID'); seen.add(item.id);
    return [item.id, item.resourceId, item.assetId];
  }).sort((a, b) => a[0] - b[0]);
}

function player(item, label) {
  fields(item, 'id resourceId assetId', positive, label);
  fields(item, 'rating contract fitness loans', integer, label);
  fields(item, 'preferredPosition itemState', (x) => typeof x === 'string' && x.length > 0, label);
  fields(item, 'attributeList statsList lifetimeStats posMods', Array.isArray, label);
  expect(item.itemType === 'player' && typeof item.untradeable === 'boolean', `${label}: wrong native item types`);
}

function nativeUser(value, account, balances, activeSquad) {
  fields(value, 'personaId clubPlayers pileClubPlayers pileSquadPlayers pileClubItems pileClubTotal pilePack', integer, 'user');
  fields(value, 'clubName clubAbbr established', (x) => typeof x === 'string', 'user');
  fields(value.bidTokens, 'count updateTime', integer, 'user.bidTokens');
  fields(value.reliability, 'reliability matchUnfinishedTime', integer, 'user.reliability');
  expect(object(value.squadList) && Array.isArray(value.squadList.squad), 'user: squadList.squad must be an array');
  const summaryIds = new Set();
  for (const entry of value.squadList.squad) {
    fields(entry, 'id', positive, 'squad summary');
    fields(entry, 'rating chemistry', integer, 'squad summary');
    expect(SQUAD_TYPES.includes(entry.squadType), 'squad summary: unsupported squadType string enum');
    fields(entry, 'squadName formation', (x) => typeof x === 'string' && x.length > 0, 'squad summary');
    expect(!summaryIds.has(entry.id), 'user: duplicate squad summary ID'); summaryIds.add(entry.id);
  }
  const summary = value.squadList.squad.find((entry) => entry.id === activeSquad.id);
  expect(summary, 'user: squadList lacks the active squad summary');
  for (const key of ['squadName', 'formation', 'chemistry']) expect(summary[key] === activeSquad[key], `user: active squad summary ${key} mismatch`);
  expect(Array.isArray(value.actives) && value.actives.length > 0 && value.actives.every(object), 'user: actives must contain item objects');
  expect(typeof value.purchased === 'boolean', 'user: purchased must be boolean');
  expect(value.personaId === account.personaId && value.clubName === account.clubName
    && value.clubAbbr === account.clubAbbr, 'user: account identity mismatch');
  expect(JSON.stringify(currencies(value)) === JSON.stringify(balances), 'user: currency mismatch');
}

function squad(value, account, items) {
  expect(value?.personaId === account.personaId, 'squad: persona mismatch');
  fields(value, 'id', positive, 'squad');
  fields(value, 'squadName formation', (x) => typeof x === 'string' && x.length > 0, 'squad');
  fields(value, 'chemistry', integer, 'squad');
  expect(Array.isArray(value.players) && value.players.length === 23, 'squad: expected all 23 slots');
  const indices = new Set(), ids = new Set(), club = new Map(items.map((entry) => [entry[0], entry]));
  for (const slot of value.players) {
    expect(object(slot) && integer(slot.index) && slot.index < 23 && !indices.has(slot.index), 'squad: invalid or duplicate slot index');
    indices.add(slot.index);
    if (!Object.hasOwn(slot, 'itemData')) continue;
    player(slot.itemData, 'squad item');
    const item = slot.itemData, stored = club.get(item.id);
    expect(!ids.has(item.id), 'squad: duplicate item ID'); ids.add(item.id);
    expect(stored && stored[1] === item.resourceId && stored[2] === item.assetId, 'squad: missing club item binding');
  }
  expect(ids.size >= 11, 'squad: fewer than eleven populated slots');
  return ids.size;
}

function loans(value) {
  expect(Array.isArray(value?.loans) && value.loans.length === 40, 'loans: expected 40 choices');
  const counts = new Map(POSITIONS.map((position) => [position, 0])), ids = new Set();
  for (const entry of value.loans) {
    player(entry?.itemData, 'loan item');
    const item = entry.itemData;
    expect(item.loans > 0 && counts.has(item.preferredPosition) && !ids.has(item.resourceId), 'loans: invalid or duplicate choice');
    ids.add(item.resourceId); counts.set(item.preferredPosition, counts.get(item.preferredPosition) + 1);
  }
  expect([...counts.values()].every((count) => count === 5), 'loans: expected five choices per onboarding position');
}

function same(left, right, label) { expect(JSON.stringify(left) === JSON.stringify(right), label); }
// Explicit stable fields only: established/timestamp can change between GETs.
const stablePlayer = (item) => item ? ['id', 'resourceId', 'assetId', 'itemType', 'preferredPosition',
  'rating', 'contract', 'fitness', 'loans', 'untradeable'].map((key) => item[key]) : null;
const stableSquad = (value) => ({ fields: ['id', 'personaId', 'squadName', 'formation', 'chemistry'].map((key) => value[key]),
  players: value.players.map((slot) => [slot.index, slot.kitNumber ?? 0, stablePlayer(slot.itemData)]).sort((a, b) => a[0] - b[0]) });
const stableLoans = (value) => value.loans.map((entry) => stablePlayer(entry.itemData)).sort((a, b) => a[1] - b[1]);
const stableUser = (value) => ({
  fields: ['personaId', 'clubName', 'clubAbbr', 'clubPlayers', 'pileClubPlayers', 'pileSquadPlayers',
    'pileClubItems', 'pileClubTotal', 'pilePack', 'purchased'].map((key) => value[key]),
  currencies: currencies(value), bidTokens: [value.bidTokens.count, value.bidTokens.updateTime],
  reliability: [value.reliability.reliability, value.reliability.matchUnfinishedTime],
  actives: value.actives.map((item) => [item.id, item.resourceId, item.assetId]).sort((a, b) => a[0] - b[0]),
  squads: value.squadList.squad.map((entry) => ['id', 'squadName', 'rating', 'chemistry', 'formation', 'squadType']
    .map((key) => entry[key])).sort((a, b) => a[0] - b[0]),
});

async function verify(args, get = getJson) {
  const read = (route) => get(route, args);
  const beforeItems = inventory(await read(ROUTES[0])), beforeCredits = currencies(await read(ROUTES[1]));
  const account = identity(await read('user/accountinfo')), v2 = identity(await read('v2/user/accountinfo'));
  expect(JSON.stringify(account) === JSON.stringify(v2), 'accountinfo: old and v2 identities differ');
  const user = await read('user'), activeSquad = await read('squad/active'), loanChoices = await read('loan/players');
  const populated = squad(activeSquad, account, beforeItems);
  nativeUser(user, account, beforeCredits, activeSquad);
  loans(loanChoices);
  const mass = await read(MASS_ROUTE);
  expect(object(mass?.userInfo) && object(mass.squad) && object(mass.loanPlayers), 'userMassInfo: missing native bootstrap sections');
  nativeUser(mass.userInfo, account, beforeCredits, activeSquad);
  squad(mass.squad, account, beforeItems); loans(mass.loanPlayers);
  same(stableUser(mass.userInfo), stableUser(user), 'userMassInfo.userInfo: standalone mismatch');
  same(stableSquad(mass.squad), stableSquad(activeSquad), 'userMassInfo.squad: standalone mismatch');
  same(stableLoans(mass.loanPlayers), stableLoans(loanChoices), 'userMassInfo.loanPlayers: standalone mismatch');
  expect(JSON.stringify(inventory(await read(ROUTES[0]))) === JSON.stringify(beforeItems), 'Club item IDs/resources changed during GET probes');
  expect(JSON.stringify(currencies(await read(ROUTES[1]))) === JSON.stringify(beforeCredits), 'Credits changed during GET probes');
  return { items: beforeItems.length, populated };
}

async function selfTest() {
  const item = (index) => ({ id: index + 1, resourceId: index + 101, assetId: index + 101, rating: 80,
    contract: 7, fitness: 99, loans: 7, preferredPosition: POSITIONS[Math.floor(index / 5) % 8], itemState: 'free',
    itemType: 'player', untradeable: true, attributeList: [], statsList: [], lifetimeStats: [], posMods: [] });
  const account = { userAccountInfo: { personas: [{ personaId: 2, personaName: 'Local',
    userClubList: [{ assetId: 3, clubName: 'Local XI', clubAbbr: 'LOC' }] }] } };
  const credit = { currencies: [{ name: 'coins', funds: 500, finalFunds: 500 }, { name: 'points', funds: 0, finalFunds: 0 }] };
  const fixture = { [ROUTES[0]]: { clubCount: 11, itemData: Array.from({ length: 11 }, (_, i) => item(i)) },
    [ROUTES[1]]: credit, 'user/accountinfo': account, 'v2/user/accountinfo': account,
    user: { personaId: 2, clubPlayers: 11, pileClubPlayers: 0, pileSquadPlayers: 11, pileClubItems: 1,
      pileClubTotal: 12, pilePack: 0, clubName: 'Local XI', clubAbbr: 'LOC', established: '1', purchased: false,
      bidTokens: { count: 0, updateTime: 0 }, reliability: { reliability: 100, matchUnfinishedTime: 0 },
      squadList: { squad: [] }, actives: [{ id: 1000 }], ...credit },
    'squad/active': { id: 1, personaId: 2, squadName: 'Local XI', formation: 'f442', chemistry: 100,
      players: Array.from({ length: 23 }, (_, i) => ({ index: i, ...(i < 11 ? { itemData: item(i) } : {}) })) },
    'loan/players': { loans: Array.from({ length: 40 }, (_, i) => ({ itemData: item(i) })) } };
  fixture.user.squadList.squad = [{ id: 1, squadName: 'Local XI', formation: 'f442', chemistry: 100, rating: 80, squadType: 'REGULAR_SQUAD' }];
  fixture[MASS_ROUTE] = { userInfo: structuredClone(fixture.user), squad: structuredClone(fixture['squad/active']),
    loanPlayers: structuredClone(fixture['loan/players']) };
  const run = (values) => verify({}, async (route) => structuredClone(values[route]));
  await run(fixture);
  for (const squadType of SQUAD_TYPES) {
    const copy = structuredClone(fixture);
    copy.user.squadList.squad[0].squadType = squadType;
    copy[MASS_ROUTE].userInfo.squadList.squad[0].squadType = squadType;
    await run(copy);
  }
  const dynamicTimes = structuredClone(fixture);
  dynamicTimes[MASS_ROUTE].userInfo.established = '2';
  dynamicTimes[MASS_ROUTE].squad.players[0].itemData.timestamp = 999;
  dynamicTimes[MASS_ROUTE].loanPlayers.loans[0].itemData.timestamp = 999;
  await run(dynamicTimes);
  let rejected = 0;
  const bad = async (mutate) => { const copy = structuredClone(fixture); mutate(copy); await assert.rejects(run(copy)); rejected++; };
  for (const route of ROUTES) await bad((copy) => { copy[route] = {}; });
  await bad((copy) => { copy.user.bidTokens = []; });
  await bad((copy) => { copy.user.squadList = []; });
  await bad((copy) => { copy.user.squadList.squad = []; });
  for (const squadType of [0, '0', 'UNKNOWN_SQUAD', null]) await bad((copy) => {
    copy.user.squadList.squad[0].squadType = squadType;
    copy[MASS_ROUTE].userInfo.squadList.squad[0].squadType = squadType;
  });
  await bad((copy) => { copy.user.squadList.squad[0].formation = 'f433'; });
  await bad((copy) => { copy.user.currencies = []; copy.user.credits = 500; });
  await bad((copy) => { copy['v2/user/accountinfo'] = structuredClone(account); copy['v2/user/accountinfo'].userAccountInfo.personas[0].personaId = 99; });
  await bad((copy) => { copy['squad/active'].players[0].itemData.id = 999; });
  await bad((copy) => { copy['squad/active'].players[1].index = 0; });
  await bad((copy) => { copy['squad/active'].players[1].itemData = copy['squad/active'].players[0].itemData; });
  await bad((copy) => { copy['loan/players'].loans[0] = item(0); });
  await bad((copy) => { copy['loan/players'].loans[0].itemData.preferredPosition = 'INVALID'; });
  for (const section of ['userInfo', 'squad', 'loanPlayers']) await bad((copy) => { delete copy[MASS_ROUTE][section]; });
  await bad((copy) => { copy[MASS_ROUTE].squad.players[0].itemData.id = 999; });
  await bad((copy) => { copy[MASS_ROUTE].loanPlayers.loans[0].itemData.rating++; });
  await bad((copy) => { copy[MASS_ROUTE].userInfo.pileClubPlayers++; });
  for (const route of [ROUTES[0], ROUTES[1]]) {
    let visits = 0;
    await assert.rejects(verify({}, async (requested) => {
      const value = structuredClone(fixture[requested]);
      if (requested === route && ++visits === 2) {
        if (route === ROUTES[0]) value.itemData[0].resourceId++;
        else value.currencies[0].funds++;
      }
      return value;
    })); rejected++;
  }
  console.log(`PASS offline startup fixtures: valid contract and ${rejected} rejected empty/type/binding/mutation cases; no network requests`);
}

function options(args) {
  const value = { httpPort: 8090, timeoutMs: 3000 };
  for (let index = 0; index < args.length; index++) {
    const flag = args[index];
    if (flag === '--self-test' || flag === '--help') value[flag.slice(2)] = true;
    else if (flag === '--http-port' || flag === '--timeout-ms') {
      const raw = args[++index] || '', parsed = /^\d+$/.test(raw) ? Number(raw) : NaN;
      expect(Number.isSafeInteger(parsed) && parsed >= 1 && parsed <= (flag === '--http-port' ? 65535 : 60000), `Invalid ${flag}`);
      value[flag === '--http-port' ? 'httpPort' : 'timeoutMs'] = parsed;
    } else throw new Error('Unsupported option; only loopback GET requests are supported');
  }
  return value;
}

async function main() {
  const args = options(process.argv.slice(2));
  if (args.help) { console.log('Usage: node localfut/verify-fut-startup.mjs [--http-port 8090] [--timeout-ms 3000] [--self-test]'); return; }
  if (args['self-test']) { await selfTest(); return; }
  const result = await verify(args);
  console.log(`PASS startup payloads: old/v2 account identity, native user, ${result.populated}/23 squad slots, 40 loan choices and matching userMassInfo sections; ${result.items} club items and credits unchanged`);
  console.log('Verified mass-info contract: userInfo, squad, loanPlayers. HTTP diagnostics alone do not prove native game startup.');
}
main().catch((error) => { console.error(`FAIL ${error.message}`); process.exitCode = 1; });
