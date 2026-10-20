#!/usr/bin/env node
// Read-only Fire2/TDF diagnostic; wire layout follows localfut21/server.py.
// Usage: node localfut/verify-auth-bootstrap.mjs [--port 44321] [--http-port 8090] [--timeout-ms 3000]
// Offline checks only: node localfut/verify-auth-bootstrap.mjs --self-test
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { createHash, randomUUID } from 'node:crypto';
import { closeSync, fstatSync, openSync, readFileSync, readSync, statSync } from 'node:fs';
import http from 'node:http';
import net from 'node:net';
import { homedir } from 'node:os';
import { isAbsolute, join } from 'node:path';
import { TextDecoder } from 'node:util';

const HOST = '127.0.0.1'; // Deliberately not configurable; never resolve a hostname.
const MAX_PAYLOAD = 1024 * 1024;
const MAX_METADATA = 16 * 1024;
const MAX_FRAME = 16 + MAX_METADATA + MAX_PAYLOAD;
const TYPES = { INT: 0, STRING: 1, BLOB: 2, GROUP: 3, LIST: 4, MAP: 5 };
const REQUIRED = [
  'blazeServerClientId', 'blazeSdkClientId', 'blazeSdkClientSecret',
  'nucleusConnect', 'identityRedirectUri',
];
const CLIENT_IDS = ['localfut21-blaze-server', 'localfut21-blaze-sdk', 'localfut21-ssf'];
// Public, synthetic emulator fixtures only; never substitute EA credentials.
const LOCAL_AUTH_CODE = 'LOCAL-FUT21-AUTH-CODE';
const LOCAL_ACCESS_TOKEN = 'LOCAL-FUT21-ACCESS';
const LOCAL_REFRESH_TOKEN = 'LOCAL-FUT21-REFRESH';
const HTTP_PATHS = new Set(['/localfut/auth/bootstrap', '/connect/token', '/connect/tokeninfo', '/localfut/callback']);

function expect(condition, message) {
  // Avoid assertion-library diagnostics that can print actual response values.
  if (!condition) throw new Error(message);
}

function expectedServerState(read = readFileSync, env = process.env, home = homedir()) {
  let source;
  try { source = read(new URL('./runtime-source/localfut21/server.py', import.meta.url)); }
  catch { throw new Error('Cannot read the local server source for startup-hash verification'); }
  // Match user_config.py without creating, migrating, or modifying preferences.
  const root = env.LOCALFUT21_USER_CONFIG_ROOT
    || join(env.LOCALAPPDATA || join(home, 'AppData', 'Local'), 'Project21', 'LocalFUT');
  expect(isAbsolute(root) && !/^[/\\]{2}/.test(root), 'Local mock config root must be an absolute local filesystem path');
  let config;
  try {
    config = JSON.parse(read(join(root, 'config.json'), 'utf8').replace(/^\uFEFF/, ''));
  } catch { throw new Error('Cannot read local mock configuration; start the launcher to initialize preferences'); }
  expect(config && typeof config === 'object' && !Array.isArray(config), 'Local mock configuration must be an object');
  const accountId = (key, fallback) => {
    const value = Object.hasOwn(config, key) ? config[key] : fallback;
    expect((typeof value === 'string' && /^[1-9]\d*$/.test(value))
      || (typeof value === 'number' && Number.isSafeInteger(value) && value > 0), 'Local mock account IDs are not positive decimal integers');
    return String(value);
  };
  const userId = accountId('nucleus_id', 1000000001);
  const personaId = accountId('persona_id', 1000000002);
  return { sourceHash: createHash('sha256').update(source).digest('hex'), userId, personaId };
}

function validateBootstrap(profile, state, port, preauth) {
  expect(profile && typeof profile === 'object' && !Array.isArray(profile), 'Bootstrap profile must be an object');
  expect(profile.version === 1, 'Bootstrap profile version must be 1');
  expect(typeof profile.serverSourceSha256 === 'string' && /^[a-f0-9]{64}$/.test(profile.serverSourceSha256)
    && profile.serverSourceSha256 === state.sourceHash, 'Running server source hash differs from disk; restart the updated server');
  expect(profile.authCode === LOCAL_AUTH_CODE, 'Bootstrap profile must contain the synthetic local auth code');
  expect(Array.isArray(profile.clientIds) && profile.clientIds.length === CLIENT_IDS.length
    && profile.clientIds.every((value, index) => value === CLIENT_IDS[index]), 'Bootstrap client IDs differ from the local contract');
  expect(profile.config && typeof profile.config === 'object' && !Array.isArray(profile.config), 'Bootstrap native config is missing');
  const values = [CLIENT_IDS[0], CLIENT_IDS[1], '', `http://${HOST}:${port}/`, `http://${HOST}:${port}/localfut/callback`];
  for (let index = 0; index < REQUIRED.length; index++) {
    const key = REQUIRED[index];
    expect(profile.config[key] === values[index], `Bootstrap config differs from local contract: ${key}`);
    expect(preauth.get(key) === profile.config[key], `PreAuth and HTTP profile disagree: ${key}`);
  }
}

function localHttpRequest(spec, { httpPort = 8090, timeoutMs = 3000 } = {}, request = http.request) {
  expect(typeof spec.path === 'string' && HTTP_PATHS.has(spec.path.split('?')[0]), 'HTTP diagnostic route is not allowlisted');
  return new Promise((resolve, reject) => {
    let finished = false;
    let outgoing;
    const timer = setTimeout(() => finish(new Error('Local HTTP request timed out')), timeoutMs);
    function finish(error, value) {
      if (finished) return;
      finished = true;
      clearTimeout(timer);
      if (outgoing) outgoing.destroy();
      if (error) reject(error);
      else resolve(value);
    }
    try {
      const body = spec.body || '';
      outgoing = request({
        hostname: HOST, family: 4, port: httpPort, method: spec.method || 'GET', path: spec.path,
        agent: false, headers: { ...spec.headers, 'Content-Length': Buffer.byteLength(body) },
      }, (incoming) => {
        const chunks = [];
        let size = 0;
        incoming.on('data', (chunk) => {
          if (finished) return;
          size += chunk.length;
          if (size > 16384) { finish(new Error('Local HTTP response exceeds diagnostic size limit')); return; }
          chunks.push(chunk);
        });
        incoming.once('end', () => finish(null, { status: incoming.statusCode, headers: incoming.headers, body: Buffer.concat(chunks) }));
        incoming.once('aborted', () => finish(new Error('Local HTTP response was truncated')));
        incoming.once('error', () => finish(new Error('Local HTTP response failed')));
      });
      outgoing.once('error', () => finish(new Error('Loopback HTTP connection failed')));
      outgoing.end(body);
    } catch { finish(new Error('Cannot create loopback HTTP request')); }
  });
}

function verifyHttpResponse(response, status, label, empty = false) {
  expect(response.status === status, `${label}: unexpected HTTP status`);
  expect(/(?:^|,)\s*no-store\s*(?:,|$)/i.test(String(response.headers['cache-control'] || ''))
    && String(response.headers.pragma || '').toLowerCase() === 'no-cache', `${label}: missing no-store/no-cache headers`);
  if (empty) {
    expect(response.body.length === 0, `${label}: expected an empty response`);
    return null;
  }
  expect(/^application\/json(?:\s*;|$)/i.test(String(response.headers['content-type'] || '')), `${label}: expected JSON content type`);
  let value;
  try { value = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(response.body)); }
  catch { throw new Error(`${label}: invalid JSON response`); }
  expect(value && typeof value === 'object' && !Array.isArray(value), `${label}: expected a JSON object`);
  return value;
}

async function verifyHttpContract(args, state, preauth, sentinel, request = localHttpRequest) {
  let requests = 0;
  const send = async (spec, status, label, empty = false) => {
    const response = await request(spec, args);
    requests++;
    return verifyHttpResponse(response, status, label, empty);
  };
  const profile = await send({ path: '/localfut/auth/bootstrap' }, 200, 'bootstrap profile');
  validateBootstrap(profile, state, args.httpPort, preauth);
  const form = (clientId = CLIENT_IDS[1]) => new URLSearchParams({ grant_type: 'authorization_code', code: LOCAL_AUTH_CODE, client_id: clientId });
  const post = (body, headers = {}) => ({
    method: 'POST', path: '/connect/token', body,
    headers: { 'Content-Type': 'application/x-www-form-urlencoded', ...headers },
  });
  const verifyInfo = (value, clientId) => {
    expect(value.user_id === state.userId && value.persona_id === state.personaId, 'tokeninfo: local account IDs differ from configuration');
    expect(value.client_id === clientId && value.expires_in === 3600, 'tokeninfo: local client or expiry differs from contract');
  };
  for (const clientId of CLIENT_IDS) {
    const body = form(clientId);
    if (clientId !== CLIENT_IDS[0]) body.set('client_secret', ''); // Both absent and empty secrets are supported.
    body.set('redirect_uri', profile.config.identityRedirectUri);
    const value = await send(post(body.toString()), 200, 'synthetic token exchange');
    expect(value.access_token === LOCAL_ACCESS_TOKEN && value.refresh_token === LOCAL_REFRESH_TOKEN
      && value.token_type === 'Bearer' && value.expires_in === 3600, 'Token response differs from the synthetic local contract');
    const query = new URLSearchParams({ access_token: LOCAL_ACCESS_TOKEN, client_id: clientId });
    verifyInfo(await send({ path: `/connect/tokeninfo?${query}` }, 200, 'tokeninfo'), clientId);
  }
  const rejected = async (spec, expectedError, label) => {
    const value = await send(spec, 400, label);
    expect(value.error === expectedError && Object.keys(value).length === 1, `${label}: expected a credential-free error response`);
  };
  const negatives = [
    ['code', 'NONLOCAL-CODE-FIXTURE', 'invalid_grant'],
    ['client_id', 'unknown-client', 'invalid_client'],
    ['client_id', 'FC26_PC_CLIENT', 'invalid_client'],
    ['client_secret', 'NONLOCAL-SECRET-FIXTURE', 'invalid_client'],
    ['grant_type', 'refresh_token', 'unsupported_grant_type'],
    ['redirect_uri', 'https://example.com/callback', 'invalid_request'],
  ];
  for (const [key, value, expectedError] of negatives) {
    const body = form(); body.set(key, value);
    await rejected(post(body.toString()), expectedError, `rejected ${key} fixture`);
  }
  for (const key of ['code', 'client_id', 'client_secret']) {
    const body = form(); body.append(key, ''); body.append(key, '');
    await rejected(post(body.toString()), 'invalid_request', `duplicate ${key} fixture`);
  }
  await rejected(post(form().toString(), { Authorization: 'Bearer NONLOCAL-AUTHORIZATION-FIXTURE' }), 'invalid_client', 'authorization-header fixture');
  await rejected(post(form().toString(), { 'Content-Type': 'application/json' }), 'invalid_request', 'wrong-content-type fixture');
  await rejected(post('grant_type'), 'invalid_request', 'malformed-form fixture');
  await rejected({ path: `/connect/tokeninfo?access_token=${sentinel}` }, 'invalid_token', 'nonlocal-token and query-redaction fixture');
  const duplicateToken = new URLSearchParams([['access_token', LOCAL_ACCESS_TOKEN], ['access_token', LOCAL_ACCESS_TOKEN]]);
  await rejected({ path: `/connect/tokeninfo?${duplicateToken}` }, 'invalid_request', 'duplicate-token fixture');
  const unknownClient = new URLSearchParams({ access_token: LOCAL_ACCESS_TOKEN, client_id: 'unknown-client' });
  await rejected({ path: `/connect/tokeninfo?${unknownClient}` }, 'invalid_client', 'tokeninfo-client fixture');
  verifyInfo(await send({ path: `/connect/tokeninfo?access_token=${LOCAL_ACCESS_TOKEN}` }, 200, 'default-client tokeninfo'), CLIENT_IDS[1]);
  await send({ path: '/localfut/callback' }, 200, 'local callback', true);
  return requests;
}

function logCheckpoint() {
  const root = process.env.LOCALAPPDATA || join(homedir(), 'AppData', 'Local');
  const path = join(root, 'FIFA21LocalFUT', 'logs', 'localfut15.log');
  try {
    const status = statSync(path);
    return { path, size: status.size, inode: status.ino };
  } catch { return null; }
}

function inspectRedactionDelta(bytes, sentinel) {
  expect(!bytes.includes(Buffer.from(sentinel)), 'Auth query redaction failed: the synthetic probe appeared in the runtime log');
  return bytes.includes(Buffer.from('local auth GET /connect/tokeninfo'));
}

function verifyLogRedaction(checkpoint, sentinel) {
  if (!checkpoint) return false;
  let descriptor;
  try {
    descriptor = openSync(checkpoint.path, 'r');
    const status = fstatSync(descriptor);
    const size = status.size - checkpoint.size;
    if (status.ino !== checkpoint.inode || size < 0 || size > 1024 * 1024) return false;
    const bytes = Buffer.alloc(size);
    let read = 0;
    while (read < size) {
      const count = readSync(descriptor, bytes, read, size - read, checkpoint.size + read);
      if (count === 0) return false;
      read += count;
    }
    return inspectRedactionDelta(bytes, sentinel);
  } catch (error) {
    if (error.message.startsWith('Auth query redaction failed:')) throw error;
    return false;
  } finally { if (descriptor !== undefined) closeSync(descriptor); }
}

function preAuthRequest() {
  const frame = Buffer.alloc(16);
  frame.writeUInt16BE(9, 6);
  frame.writeUInt16BE(7, 8);
  frame.writeUIntBE(1, 10, 3);
  return frame;
}

function readFrame(buffer, final = false) {
  if (buffer.length < 16) {
    if (final) throw new Error('Truncated Fire2 header');
    return null;
  }
  const payloadSize = buffer.readUInt32BE(0);
  const metadataSize = buffer.readUInt16BE(4);
  if (payloadSize > MAX_PAYLOAD || metadataSize > MAX_METADATA) {
    throw new Error('Fire2 frame exceeds diagnostic size limit');
  }
  const size = 16 + metadataSize + payloadSize;
  if (buffer.length < size) {
    if (final) throw new Error('Truncated Fire2 payload or metadata');
    return null;
  }
  return {
    size,
    component: buffer.readUInt16BE(6),
    command: buffer.readUInt16BE(8),
    message: buffer.readUIntBE(10, 3),
    type: buffer[13] >>> 5,
    payload: buffer.subarray(16 + metadataSize, size),
  };
}

class TdfReader {
  constructor(buffer) {
    this.buffer = buffer;
    this.offset = 0;
    this.nodes = 0;
    this.utf8 = new TextDecoder('utf-8', { fatal: true });
  }

  take(size) {
    if (size < 0 || size > this.buffer.length - this.offset) {
      throw new Error('Truncated TDF value');
    }
    const value = this.buffer.subarray(this.offset, this.offset + size);
    this.offset += size;
    return value;
  }

  byte() { return this.take(1)[0]; }

  integer() {
    let part = this.byte();
    const negative = Boolean(part & 0x40);
    let value = BigInt(part & 0x3f);
    let shift = 6n;
    let count = 1;
    while (part & 0x80) {
      if (++count > 10) throw new Error('Oversized TDF varint');
      part = this.byte();
      value |= BigInt(part & 0x7f) << shift;
      shift += 7n;
    }
    if (value > 0xffffffffffffffffn) throw new Error('Oversized TDF integer');
    return negative ? -value : value;
  }

  size(limit) {
    const value = this.integer();
    if (value < 0n || value > BigInt(limit)) throw new Error('Invalid TDF length or count');
    return Number(value);
  }

  field(depth) {
    const header = this.take(4);
    const bits = header.readUIntBE(0, 3);
    let tag = '';
    let padded = false;
    for (const shift of [18, 12, 6, 0]) {
      const letter = (bits >>> shift) & 0x3f;
      if (letter === 0) padded = true;
      else {
        if (padded) throw new Error('Malformed TDF tag');
        tag += String.fromCharCode(letter + 32);
      }
    }
    if (!tag) throw new Error('Empty TDF tag');
    return { tag, type: header[3], value: this.value(header[3], depth) };
  }

  fields(group = false, depth = 0) {
    const fields = [];
    while (this.offset < this.buffer.length) {
      if (group && this.buffer[this.offset] === 0) {
        this.offset++;
        return fields;
      }
      fields.push(this.field(depth));
    }
    if (group) throw new Error('Unterminated TDF group');
    return fields;
  }

  value(type, depth) {
    if (depth > 16 || ++this.nodes > 16384) throw new Error('TDF nesting or item limit exceeded');
    switch (type) {
      case TYPES.INT: return this.integer();
      case TYPES.STRING: {
        const size = this.size(MAX_PAYLOAD);
        if (size === 0) throw new Error('TDF string lacks NUL terminator');
        const bytes = this.take(size);
        if (bytes[size - 1] !== 0 || bytes.subarray(0, -1).includes(0)) {
          throw new Error('Malformed TDF string terminator');
        }
        try { return this.utf8.decode(bytes.subarray(0, -1)); }
        catch { throw new Error('Invalid UTF-8 in TDF string'); }
      }
      case TYPES.BLOB: return this.take(this.size(MAX_PAYLOAD));
      case TYPES.GROUP: return this.fields(true, depth + 1);
      case TYPES.LIST: {
        const itemType = this.byte();
        const count = this.size(1024);
        return Array.from({ length: count }, () => this.value(itemType, depth + 1));
      }
      case TYPES.MAP: {
        const keyType = this.byte();
        const valueType = this.byte();
        const count = this.size(1024);
        const entries = Array.from({ length: count }, () => [
          this.value(keyType, depth + 1), this.value(valueType, depth + 1),
        ]);
        return { keyType, valueType, entries };
      }
      case 6: // Union: unset discriminator is 0x7f; otherwise one tagged member.
        return this.byte() === 0x7f ? null : this.field(depth + 1);
      case 8: return [this.integer(), this.integer()]; // ObjectType
      case 9: return [this.integer(), this.integer(), this.integer()]; // ObjectId
      case 10: return this.take(4).readFloatBE(0);
      default: throw new Error('Unsupported TDF value type');
    }
  }
}

function uniqueField(fields, tag, type, label) {
  const matches = fields.filter((field) => field.tag === tag);
  if (matches.length !== 1 || matches[0].type !== type) {
    throw new Error(`${label} must occur exactly once with the expected TDF type`);
  }
  return matches[0].value;
}

function authConfig(payload) {
  const root = new TdfReader(payload).fields();
  const group = uniqueField(root, 'CONF', TYPES.GROUP, 'PreAuth CONF group');
  const map = uniqueField(group, 'CONF', TYPES.MAP, 'PreAuth CONF.CONF map');
  if (map.keyType !== TYPES.STRING || map.valueType !== TYPES.STRING) {
    throw new Error('PreAuth CONF.CONF must be a string-to-string map');
  }
  const config = new Map();
  for (const [key, value] of map.entries) {
    if (config.has(key)) throw new Error('Duplicate key in PreAuth CONF.CONF map');
    config.set(key, value);
  }
  return config;
}

function isLocalHttpUri(value) {
  // Require literal loopback spelling, not DNS, shorthand IPs, or URL credentials.
  if (!/^http:\/\/(?:127\.0\.0\.1|\[::1\])(?::[0-9]+)?(?:\/|$)/i.test(value)) return false;
  try {
    const url = new URL(value);
    return url.protocol === 'http:' && !url.username && !url.password
      && ['127.0.0.1', '[::1]'].includes(url.hostname);
  } catch { return false; }
}

function validateConfig(config) {
  const failures = [];
  for (const key of REQUIRED) {
    if (!config.has(key)) failures.push(`missing ${key}`);
  }
  for (const key of ['blazeServerClientId', 'blazeSdkClientId']) {
    if (config.has(key)) {
      const value = config.get(key);
      if (!/^localfut21(?:[-_.][a-z0-9]+)*$/i.test(value) || /fc26/i.test(value)) {
        failures.push(`${key} must use an explicit localfut21 synthetic namespace`);
      }
    }
  }
  // The secret's presence is required; an empty value is valid for local-only auth.
  for (const key of ['nucleusConnect', 'identityRedirectUri']) {
    if (config.has(key) && !isLocalHttpUri(config.get(key))) {
      failures.push(`${key} must be an http URL on literal loopback without credentials`);
    }
  }
  if (failures.length) throw new Error(`PreAuth auth bootstrap: ${failures.join('; ')}`);
}

function receivePreAuth({ port = 44321, timeoutMs = 3000 } = {}, connect = net.createConnection) {
  return new Promise((resolve, reject) => {
    const socket = connect({ host: HOST, port });
    let buffer = Buffer.alloc(0);
    let finished = false;
    let unrelated = 0;
    const timer = setTimeout(() => finish(new Error('Timed out waiting for PreAuth reply')), timeoutMs);
    function finish(error, payload) {
      if (finished) return;
      finished = true;
      clearTimeout(timer);
      socket.destroy();
      if (error) reject(error);
      else resolve(payload);
    }
    socket.once('connect', () => socket.write(preAuthRequest()));
    socket.on('data', (chunk) => {
      if (finished) return;
      try {
        if (buffer.length + chunk.length > MAX_FRAME * 2) throw new Error('Excessive Fire2 receive buffer');
        buffer = Buffer.concat([buffer, chunk]);
        while (buffer.length) {
          const frame = readFrame(buffer);
          if (!frame) break;
          buffer = buffer.subarray(frame.size);
          if (frame.component === 9 && frame.command === 7 && frame.message === 1) {
            if (frame.type !== 1) throw new Error('PreAuth returned a non-success Fire2 frame');
            finish(null, frame.payload);
            return;
          }
          if (++unrelated > 16) throw new Error('Too many unrelated Fire2 frames');
        }
      } catch (error) { finish(error); }
    });
    socket.once('error', () => finish(new Error('Loopback Blaze connection failed')));
    socket.once('close', () => {
      if (finished) return;
      try { if (buffer.length) readFrame(buffer, true); }
      catch (error) { finish(error); return; }
      finish(new Error('Blaze closed before the PreAuth reply'));
    });
  });
}

function options(args) {
  const result = { port: 44321, httpPort: 8090, timeoutMs: 3000, selfTest: false, help: false };
  for (let index = 0; index < args.length; index++) {
    const flag = args[index];
    if (flag === '--self-test') result.selfTest = true;
    else if (flag === '--help') result.help = true;
    else if (flag === '--port' || flag === '--http-port' || flag === '--timeout-ms') {
      const raw = args[++index] || '';
      const value = /^\d+$/.test(raw) ? Number(raw) : NaN;
      const maximum = flag === '--timeout-ms' ? 60000 : 65535;
      if (!Number.isSafeInteger(value) || value < 1 || value > maximum) {
        throw new Error(`Invalid ${flag}; expected an integer from 1 to ${maximum}`);
      }
      result[flag === '--port' ? 'port' : flag === '--http-port' ? 'httpPort' : 'timeoutMs'] = value;
    } else throw new Error('Unsupported option; use --help (remote hosts are not supported)');
  }
  return result;
}

async function selfTest() {
  let passed = 0;
  async function check(name, action) {
    await action();
    passed++;
    console.log(`PASS ${name}`);
  }
  // Fixture encoders are independent of the decoder and mirror server.py's wire helpers.
  const integer = (value) => {
    const bytes = [value < 64 ? value : (value & 63) | 128];
    value = Math.floor(value / 64);
    while (value) {
      bytes.push(value < 128 ? value : (value & 127) | 128);
      value = Math.floor(value / 128);
    }
    return Buffer.from(bytes);
  };
  const tag = (name, type) => {
    const bytes = Buffer.alloc(4);
    let packed = 0;
    for (let index = 0; index < 4; index++) packed = (packed << 6) | (index < name.length ? name.charCodeAt(index) - 32 : 0);
    bytes.writeUIntBE(packed, 0, 3);
    bytes[3] = type;
    return bytes;
  };
  const string = (value) => {
    const raw = Buffer.from(value);
    return Buffer.concat([integer(raw.length + 1), raw, Buffer.from([0])]);
  };
  const field = (name, type, body) => Buffer.concat([tag(name, type), body]);
  const group = (name, body) => field(name, TYPES.GROUP, Buffer.concat([body, Buffer.from([0])]));
  const map = (entries) => field('CONF', TYPES.MAP, Buffer.concat([
    Buffer.from([TYPES.STRING, TYPES.STRING]), integer(entries.length),
    ...entries.flatMap(([key, value]) => [string(key), string(value)]),
  ]));
  const valid = [
    ['blazeServerClientId', 'localfut21-server'], ['blazeSdkClientId', 'LOCALFUT21_PC_SDK'],
    ['blazeSdkClientSecret', ''], ['nucleusConnect', 'http://127.0.0.1:8090/connect'],
    ['identityRedirectUri', 'http://127.0.0.1:8090/callback'],
  ];
  const payload = (entries = valid) => Buffer.concat([
    field('CIDS', TYPES.LIST, Buffer.from([TYPES.INT, 2, 1, 9])),
    group('CONF', map(entries)),
    group('QOSS', field('LTPS', TYPES.MAP, Buffer.concat([
      Buffer.from([TYPES.STRING, TYPES.GROUP, 1]), string('local'),
      field('PSA', TYPES.STRING, string('127.0.0.1')), Buffer.from([0]),
    ]))),
  ]);
  const frame = (body = payload(), metadata = Buffer.alloc(0)) => {
    const header = preAuthRequest();
    header.writeUInt32BE(body.length, 0);
    header.writeUInt16BE(metadata.length, 4);
    header[13] = 1 << 5;
    return Buffer.concat([header, metadata, body]);
  };
  const verify = (body) => validateConfig(authConfig(body));

  await check('request matches independent Fire2 wire fixture', () => {
    assert.equal(preAuthRequest().toString('hex'), '00000000000000090007000001000000');
    assert.equal(tag('CONF', TYPES.GROUP).toString('hex'), '8efba603');
  });
  await check('nested CONF string map with empty local secret', () => verify(payload()));
  await check('long string varints and present nonempty secret', () => verify(payload([
    ...valid.filter(([key]) => key !== 'blazeSdkClientSecret'), ['blazeSdkClientSecret', 'x'.repeat(200)],
  ])));
  await check('all five missing keys are individually reported', () => {
    for (const key of REQUIRED) assert.throws(() => verify(payload(valid.filter(([name]) => name !== key))), new RegExp(`missing ${key}`));
  });
  await check('legacy timing-only PreAuth fixture fails on all native auth keys', () => {
    const timingOnly = [
      ['connIdleTimeout', '120s'], ['defaultRequestTimeout', '80s'], ['pingPeriod', '20s'],
      ['voipHeadsetUpdateRate', '1000'], ['xlspConnectionIdleTimeout', '300'],
    ];
    assert.throws(() => verify(payload(timingOnly)), (error) => REQUIRED.every((key) => error.message.includes(`missing ${key}`)));
  });
  await check('key bytes outside CONF.CONF cannot satisfy validation', () => {
    assert.throws(() => verify(group('OTHR', map(valid))), /CONF group/);
    assert.throws(() => verify(field('DATA', TYPES.BLOB, Buffer.concat([integer(map(valid).length), map(valid)]))), /CONF group/);
    assert.throws(() => verify(Buffer.concat([group('CONF', map([])), group('OTHR', map(valid))])), /missing/);
  });
  await check('wrong nested map types and duplicate keys are rejected', () => {
    assert.throws(() => verify(map(valid)), /CONF group/);
    assert.throws(() => verify(group('CONF', field('CONF', TYPES.MAP, Buffer.from([0, 1, 0])))), /string-to-string/);
    assert.throws(() => verify(payload([...valid, valid[0]])), /Duplicate key/);
    assert.throws(() => verify(Buffer.concat([payload(), group('CONF', map(valid))])), /exactly once/);
  });
  await check('nonlocal and cross-title client IDs are rejected without echoing values', () => {
    for (const value of ['', 'FC26_PC_CLIENT', 'localfut21-fc26', 'other-client', 'localfut21evil']) {
      const changed = valid.map(([key, old]) => [key, key === 'blazeSdkClientId' ? value : old]);
      assert.throws(() => verify(payload(changed)), (error) => error.message.includes('blazeSdkClientId') && (value === '' || !error.message.includes(value)));
    }
  });
  await check('URLs require literal HTTP loopback and never permit credentials', () => {
    for (const value of ['https://127.0.0.1/', 'http://example.com/', 'http://localhost/', 'http://127.0.0.1.example.com/', 'http://user:pass@127.0.0.1/', 'http://2130706433/', 'http://127.0.0.1:99999/']) {
      for (const key of ['nucleusConnect', 'identityRedirectUri']) {
        assert.throws(() => verify(payload(valid.map(([name, old]) => [name, name === key ? value : old]))), /http URL on literal loopback/);
      }
    }
    assert.equal(isLocalHttpUri('http://[::1]:8090/callback'), true);
  });
  await check('Fire2 metadata is skipped before decoding payload', () => verify(readFrame(frame(payload(), Buffer.from([1, 2, 3])), true).payload));
  await check('truncated and oversized Fire2 frames are rejected', () => {
    const complete = frame();
    for (let length = 0; length < complete.length; length++) {
      assert.equal(readFrame(complete.subarray(0, length)), null);
      assert.throws(() => readFrame(complete.subarray(0, length), true), /Truncated Fire2/);
    }
    const oversized = preAuthRequest();
    oversized.writeUInt32BE(MAX_PAYLOAD + 1, 0);
    assert.throws(() => readFrame(oversized), /size limit/);
  });
  await check('truncated TDF strings, maps, varints and groups are rejected', () => {
    const complete = group('CONF', map(valid));
    for (let length = 0; length < complete.length; length++) assert.throws(() => verify(complete.subarray(0, length)));
    assert.throws(() => verify(field('DATA', TYPES.STRING, Buffer.from([2, 65, 66]))), /terminator/);
    assert.throws(() => verify(field('DATA', TYPES.INT, Buffer.from([128]))), /Truncated TDF/);
    assert.throws(() => verify(field('DATA', TYPES.STRING, Buffer.from([2, 255, 0]))), /UTF-8/);
    assert.throws(() => verify(field('DATA', 255, Buffer.alloc(0))), /Unsupported TDF/);
  });
  await check('command-line arguments cannot select a remote host', () => {
    assert.throws(() => options(['--host', 'example.com']), /remote hosts are not supported/);
    assert.throws(() => options(['--port', '44321@example.com']), /Invalid --port/);
    assert.throws(() => options(['--http-port', '0']), /Invalid --http-port/);
    assert.throws(() => options(['--http-port', '65536']), /Invalid --http-port/);
    assert.throws(() => options(['--timeout-ms', '0']), /Invalid --timeout-ms/);
  });
  await check('fragmented reply uses only injected loopback socket; no real network', async () => {
    let connections = 0;
    const fakeConnect = (address) => {
      connections++;
      assert.deepEqual(address, { host: '127.0.0.1', port: 44321 });
      const socket = new EventEmitter();
      socket.destroy = () => {};
      socket.write = (request) => {
        assert.deepEqual(request, preAuthRequest());
        const reply = frame();
        for (const chunk of [reply.subarray(0, 8), reply.subarray(8, 19), reply.subarray(19)]) socket.emit('data', chunk);
      };
      queueMicrotask(() => socket.emit('connect'));
      return socket;
    };
    verify(await receivePreAuth({}, fakeConnect));
    assert.equal(connections, 1);
  });
  await check('socket rejects error replies and truncated replies on close', async () => {
    const errorReply = frame();
    errorReply[13] = 3 << 5;
    for (const [reply, expected] of [[errorReply, /non-success/], [frame().subarray(0, -1), /Truncated Fire2/]]) {
      const fakeConnect = (address) => {
        assert.equal(address.host, HOST);
        const socket = new EventEmitter();
        socket.destroy = () => {};
        socket.write = () => { socket.emit('data', reply); socket.emit('close'); };
        queueMicrotask(() => socket.emit('connect'));
        return socket;
      };
      await assert.rejects(receivePreAuth({}, fakeConnect), expected);
    }
  });
  await check('read-only expected state follows per-user config precedence and fails without echoing values', () => {
    const home = join(process.cwd(), 'fixture-home');
    const appData = join(home, 'fixture-appdata');
    const explicit = join(home, 'fixture-config');
    for (const [env, root] of [
      [{}, join(home, 'AppData', 'Local', 'Project21', 'LocalFUT')],
      [{ LOCALAPPDATA: appData }, join(appData, 'Project21', 'LocalFUT')],
      [{ LOCALAPPDATA: appData, LOCALFUT21_USER_CONFIG_ROOT: explicit }, explicit],
    ]) {
      const reads = [];
      const read = (path) => { reads.push(path); return path instanceof URL ? Buffer.from('source fixture') : '\uFEFF{"nucleus_id":"123","persona_id":456}'; };
      const value = expectedServerState(read, env, home);
      assert.equal(reads.length, 2);
      assert.equal(reads[1], join(root, 'config.json'));
      assert.equal(value.userId, '123');
      assert.equal(value.personaId, '456');
      assert.equal(value.sourceHash, createHash('sha256').update('source fixture').digest('hex'));
    }
    const withConfig = (text) => (path) => path instanceof URL ? Buffer.from('source fixture') : text;
    const defaults = expectedServerState(withConfig('{}'), {}, home);
    assert.equal(defaults.userId, '1000000001');
    assert.equal(defaults.personaId, '1000000002');
    assert.throws(() => expectedServerState(withConfig('{}'), { LOCALFUT21_USER_CONFIG_ROOT: 'relative' }, home), /absolute local filesystem path/);
    assert.throws(() => expectedServerState(withConfig('{}'), { LOCALFUT21_USER_CONFIG_ROOT: '//example.com/config' }, home), /absolute local filesystem path/);
    for (const text of ['{', '[]', '{"nucleus_id":null}', '{"persona_id":0}', '{"persona_id":9007199254740992}', '{"nucleus_id":"NONLOCAL-ID-FIXTURE"}']) {
      assert.throws(() => expectedServerState(withConfig(text), {}, home), (error) => !error.message.includes('NONLOCAL-ID-FIXTURE'));
    }
  });
  const state = { sourceHash: 'a'.repeat(64), userId: '1000000001', personaId: '1000000002' };
  const profile = {
    version: 1, serverSourceSha256: state.sourceHash, authCode: LOCAL_AUTH_CODE, clientIds: [...CLIENT_IDS],
    config: {
      blazeServerClientId: CLIENT_IDS[0], blazeSdkClientId: CLIENT_IDS[1], blazeSdkClientSecret: '',
      nucleusConnect: `http://${HOST}:8090/`, identityRedirectUri: `http://${HOST}:8090/localfut/callback`,
    },
  };
  const preauth = new Map(Object.entries(profile.config));
  const response = (value, status = 200) => ({
    status, headers: { 'cache-control': 'no-store', pragma: 'no-cache', 'content-type': 'application/json' },
    body: Buffer.from(value === null ? '' : JSON.stringify(value)),
  });
  const token = { access_token: LOCAL_ACCESS_TOKEN, refresh_token: LOCAL_REFRESH_TOKEN, token_type: 'Bearer', expires_in: 3600 };
  const info = (clientId) => ({ user_id: state.userId, persona_id: state.personaId, client_id: clientId, expires_in: 3600 });
  const fixtureResponses = () => [
    response(profile),
    ...CLIENT_IDS.flatMap((clientId) => [response(token), response(info(clientId))]),
    ...[
      'invalid_grant', 'invalid_client', 'invalid_client', 'invalid_client', 'unsupported_grant_type', 'invalid_request',
      'invalid_request', 'invalid_request', 'invalid_request', 'invalid_client', 'invalid_request', 'invalid_request',
      'invalid_token', 'invalid_request', 'invalid_client',
    ].map((error) => response({ error }, 400)),
    response(info(CLIENT_IDS[1])), response(null),
  ];
  const runHttpFixture = async (replies, calls = []) => verifyHttpContract(
    { httpPort: 8090, timeoutMs: 3000 }, state, preauth, 'LOCALFUT21-REDACTION-SELFTEST', async (spec, args) => {
      assert.equal(args.httpPort, 8090);
      assert.equal(HTTP_PATHS.has(spec.path.split('?')[0]), true);
      calls.push(spec);
      assert.ok(replies.length, 'unexpected extra fixture request');
      return replies.shift();
    },
  );
  await check('bootstrap validates startup hash, exact IDs, code and matching native config', () => {
    validateBootstrap(profile, state, 8090, preauth);
    assert.throws(() => validateBootstrap({ ...profile, version: 2 }, state, 8090, preauth), /version must be 1/);
    assert.throws(() => validateBootstrap({ ...profile, serverSourceSha256: 'b'.repeat(64) }, state, 8090, preauth), /restart the updated server/);
    assert.throws(() => validateBootstrap({ ...profile, authCode: 'NONLOCAL-CODE-FIXTURE' }, state, 8090, preauth), /synthetic local auth code/);
    assert.throws(() => validateBootstrap({ ...profile, clientIds: ['FC26_PC_CLIENT'] }, state, 8090, preauth), /client IDs/);
    assert.throws(() => validateBootstrap({ ...profile, config: { ...profile.config, nucleusConnect: 'https://example.com/' } }, state, 8090, preauth), /local contract: nucleusConnect/);
    assert.throws(() => validateBootstrap(profile, state, 8090, new Map()), /PreAuth and HTTP profile disagree/);
  });
  await check('HTTP response validation rejects missing cache controls and malformed JSON', () => {
    const noCache = response(profile); delete noCache.headers['cache-control'];
    assert.throws(() => verifyHttpResponse(noCache, 200, 'fixture'), /no-store/);
    const malformed = response(profile); malformed.body = Buffer.from('{');
    assert.throws(() => verifyHttpResponse(malformed, 200, 'fixture'), /invalid JSON/);
    assert.throws(() => verifyHttpResponse(response(null, 302), 200, 'fixture'), /HTTP status/);
    assert.throws(() => verifyHttpResponse(response({}), 200, 'fixture', true), /empty response/);
  });
  await check('offline HTTP suite covers three clients and all rejection cases', async () => {
    const replies = fixtureResponses();
    const calls = [];
    assert.equal(await runHttpFixture(replies, calls), 24);
    assert.equal(replies.length, 0);
    const positivePosts = calls.filter((spec) => spec.method === 'POST').slice(0, 3);
    assert.deepEqual(positivePosts.map((spec) => new URLSearchParams(spec.body).get('client_id')), CLIENT_IDS);
    assert.equal(new URLSearchParams(positivePosts[0].body).has('client_secret'), false);
    assert.equal(new URLSearchParams(positivePosts[1].body).get('client_secret'), '');
  });
  await check('HTTP suite rejects nonlocal token output without displaying it', async () => {
    const replies = fixtureResponses();
    replies[1] = response({ ...token, access_token: 'NONLOCAL-RESPONSE-FIXTURE' });
    await assert.rejects(runHttpFixture(replies), (error) => error.message.includes('synthetic local contract') && !error.message.includes('NONLOCAL-RESPONSE-FIXTURE'));
    const wrongIds = fixtureResponses();
    wrongIds[2] = response({ ...info(CLIENT_IDS[0]), user_id: '999' });
    await assert.rejects(runHttpFixture(wrongIds), /account IDs differ/);
    const reflectedError = fixtureResponses();
    reflectedError[7] = response({ error: 'invalid_grant', code: 'NONLOCAL-CODE-FIXTURE' }, 400);
    await assert.rejects(runHttpFixture(reflectedError), /credential-free error/);
  });
  await check('HTTP transport is literal loopback and does not follow redirects', async () => {
    let connections = 0;
    const fakeRequest = (settings, callback) => {
      connections++;
      assert.equal(settings.hostname, HOST);
      assert.equal(settings.family, 4);
      assert.equal(settings.port, 8090);
      assert.equal(settings.agent, false);
      const outgoing = new EventEmitter();
      outgoing.destroy = () => {};
      outgoing.end = () => queueMicrotask(() => {
        const incoming = new EventEmitter();
        incoming.statusCode = 302;
        incoming.headers = { location: 'https://example.com/' };
        callback(incoming);
        incoming.emit('end');
      });
      return outgoing;
    };
    const value = await localHttpRequest({ path: '/localfut/auth/bootstrap' }, {}, fakeRequest);
    assert.equal(value.status, 302);
    assert.equal(connections, 1);
    assert.throws(() => localHttpRequest({ path: 'https://example.com/' }, {}, fakeRequest), /not allowlisted/);
    assert.equal(connections, 1);
  });
  await check('HTTP transport rejects truncated and oversized responses without real network', async () => {
    for (const [failure, expected] of [['aborted', /truncated/], ['oversized', /size limit/]]) {
      const fakeRequest = (settings, callback) => {
        assert.equal(settings.hostname, HOST);
        const outgoing = new EventEmitter();
        outgoing.destroy = () => {};
        outgoing.end = () => queueMicrotask(() => {
          const incoming = new EventEmitter();
          callback(incoming);
          if (failure === 'oversized') incoming.emit('data', Buffer.alloc(16385));
          else incoming.emit('aborted');
        });
        return outgoing;
      };
      await assert.rejects(localHttpRequest({ path: '/localfut/auth/bootstrap' }, {}, fakeRequest), expected);
    }
  });
  await check('query-redaction verification detects leaks without returning log text', () => {
    const sentinel = 'LOCALFUT21-REDACTION-SELFTEST';
    assert.equal(inspectRedactionDelta(Buffer.from('INFO FUT8080 127.0.0.1 - local auth GET /connect/tokeninfo\n'), sentinel), true);
    assert.equal(inspectRedactionDelta(Buffer.from('unrelated log record\n'), sentinel), false);
    assert.throws(() => inspectRedactionDelta(Buffer.from(`GET /connect/tokeninfo?access_token=${sentinel}`), sentinel),
      (error) => error.message.startsWith('Auth query redaction failed:') && !error.message.includes(sentinel));
  });
  console.log(`PASS self-test (${passed} groups; no network connections made)`);
}

async function main() {
  const args = options(process.argv.slice(2));
  if (args.help) {
    console.log('Usage: node localfut/verify-auth-bootstrap.mjs [--port 44321] [--http-port 8090] [--timeout-ms 3000] [--self-test]');
    console.log('Live mode checks PreAuth plus synthetic local HTTP auth on 127.0.0.1 only; returned URLs are never fetched.');
    console.log('Verifies startup source hash and, when available, only newly appended runtime-log bytes for query redaction. No auth values or log contents are printed.');
    return;
  }
  if (args.selfTest) { await selfTest(); return; }
  const state = expectedServerState();
  const preauth = authConfig(await receivePreAuth(args));
  validateConfig(preauth);
  console.log('PASS PreAuth native auth bootstrap: five local-only settings verified in nested CONF.CONF');
  const checkpoint = logCheckpoint();
  const sentinel = `LOCALFUT21-REDACTION-${randomUUID()}`;
  const requests = await verifyHttpContract(args, state, preauth, sentinel);
  console.log(`PASS local auth HTTP contract (${requests} loopback requests): startup hash, three client flows, rejection cases, tokeninfo and cache headers`);
  console.log(verifyLogRedaction(checkpoint, sentinel)
    ? 'PASS auth query redaction: synthetic probe absent from newly appended runtime-log bytes'
    : 'SKIP auth query redaction: local runtime log unavailable, rotated, or probe request not observed');
}

main().catch((error) => {
  console.error(`FAIL ${error.message}`);
  process.exitCode = 1;
});
