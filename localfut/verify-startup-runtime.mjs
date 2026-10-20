#!/usr/bin/env node
// Runs the existing server, never Python analysis code or the live launcher.
// All requests use a child-owned, attested loopback test listener and fixture state.
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import { mkdir, mkdtemp, readFile, writeFile, access } from 'node:fs/promises';
import http from 'node:http';
import net from 'node:net';
import { tmpdir } from 'node:os';
import { isAbsolute, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';

const args = process.argv.slice(2);
assert(args.length === 2 && args[0] === '--python' && isAbsolute(args[1]),
  'Usage: node localfut/verify-startup-runtime.mjs --python <absolute Python 3 executable>');
const server = fileURLToPath(new URL('./runtime-source/localfut21/server.py', import.meta.url));
const sourceHash = createHash('sha256').update(await readFile(server)).digest('hex');
const scratch = await mkdtemp(join(tmpdir(), 'Project21-startup-test-'));
const clientIds = ['localfut21-blaze-server', 'localfut21-blaze-sdk', 'localfut21-ssf'];
let requests = 0;

function request(port, path, body) {
  requests++;
  return new Promise((done, fail) => {
    const outgoing = http.request({ hostname: '127.0.0.1', family: 4, port, path, agent: false,
      method: body === undefined ? 'GET' : 'POST', headers: body === undefined ? {} : {
        'Content-Type': 'application/x-www-form-urlencoded', 'Content-Length': Buffer.byteLength(body),
      } }, (response) => {
      let data = '';
      response.on('data', (part) => { data += part; if (data.length > 65536) outgoing.destroy(new Error('Oversized test response')); });
      response.on('error', fail);
      response.on('end', () => {
        try { done({ status: response.statusCode, headers: response.headers, json: JSON.parse(data) }); }
        catch (error) { fail(error); }
      });
    });
    outgoing.setTimeout(2000, () => outgoing.destroy(new Error('Test HTTP timeout')));
    outgoing.on('error', fail);
    outgoing.end(body);
  });
}

async function freePort() {
  const socket = net.createServer();
  await new Promise((done, fail) => { socket.once('error', fail); socket.listen(0, '127.0.0.1', done); });
  const port = socket.address().port;
  await new Promise((done) => socket.close(done));
  assert(![8090, 8081, 42230, 42231, 42232, 44321, 10853, 17502, 3216].includes(port));
  return port;
}

async function checkProfile(name, expectedUserId, expectedPersonaId, custom = false) {
  const root = join(scratch, name);
  const configRoot = join(root, 'config');
  const configFile = join(configRoot, 'config.json');
  await mkdir(configRoot, { recursive: true });
  if (custom) await writeFile(configFile, JSON.stringify({ nucleus_id: expectedUserId,
    persona_id: expectedPersonaId, display_name: 'Portability Fixture', custom_setting: 'preserve-me' }));
  const before = custom ? await readFile(configFile, 'utf8') : null;
  const port = await freePort();
  let stderr = '';
  let childError;
  const child = spawn(args[1], ['-u', server, '--http-only-test-port', String(port), '--lsx-mode', 'off'], {
    cwd: scratch, windowsHide: true, stdio: ['ignore', 'ignore', 'pipe'],
    env: { ...process.env, PYTHONDONTWRITEBYTECODE: '1', LOCALFUT_RUNTIME_ROOT: root, LOCALFUT21_USER_CONFIG_ROOT: configRoot },
  });
  child.stderr.on('data', (data) => { stderr = (stderr + data).slice(-4000); });
  child.on('error', (error) => { childError = error; });
  const closed = new Promise((done) => child.once('close', done));
  try {
    let status;
    const readyDeadline = Date.now() + 10000;
    for (let attempt = 0; attempt < 50 && Date.now() < readyDeadline; attempt++) {
      if (childError || child.exitCode !== null) throw new Error(`Isolated server failed: ${childError || stderr}`);
      try { status = await request(port, '/localfut/test/status'); break; } catch { await delay(100); }
    }
    assert.equal(status?.status, 200, 'Isolated server did not become ready');
    assert.equal(status.json.testMode, true);
    assert.equal(resolve(status.json.runtimeRoot), resolve(root), 'Listener belongs to a different runtime');
    assert.equal(resolve(status.json.dbPath), resolve(join(root, 'data', 'localfut15.sqlite3')));
    const profile = await request(port, '/localfut/auth/bootstrap');
    assert.equal(profile.status, 200);
    assert.equal(profile.json.serverSourceSha256, sourceHash);
    assert.deepEqual(profile.json.clientIds, clientIds);
    assert.equal(profile.json.authCode, 'LOCAL-FUT21-AUTH-CODE');
    assert.equal(profile.json.config.nucleusConnect, 'http://127.0.0.1:8090/');
    assert.equal(profile.json.config.identityRedirectUri, 'http://127.0.0.1:8090/localfut/callback');
    assert.match(profile.headers['cache-control'], /no-store/);
    for (const client_id of clientIds) {
      const form = new URLSearchParams({ grant_type: 'authorization_code', client_id, code: 'LOCAL-FUT21-AUTH-CODE' });
      const token = await request(port, '/connect/token', form.toString());
      assert.equal(token.status, 200);
      assert.equal(token.json.access_token, 'LOCAL-FUT21-ACCESS');
      const info = await request(port, '/connect/tokeninfo?' + new URLSearchParams({ access_token: token.json.access_token, client_id }));
      assert.equal(info.status, 200);
      assert.equal(info.json.user_id, String(expectedUserId));
      assert.equal(info.json.persona_id, String(expectedPersonaId));
    }
    const invalid = await request(port, '/connect/token', 'grant_type=authorization_code&client_id=not-local&code=LOCAL-FUT21-AUTH-CODE');
    assert.equal(invalid.status, 400);
    assert.deepEqual(invalid.json, { error: 'invalid_client' });
    const after = await readFile(configFile, 'utf8');
    if (custom) assert.equal(after, before, 'Existing preferences were overwritten');
    else assert.equal(JSON.parse(after).nucleus_id, expectedUserId, 'Portable default not created');
    // Pack preferences are created lazily on the first read of the local store.
    assert.equal((await request(port, '/ut/game/fifa21/store/purchasegroup/all')).status, 200);
    await access(join(configRoot, 'pack_settings.json'));
    console.log(`PASS ${name}: isolated startup, per-user config, three local auth clients and rejection cases`);
  } finally {
    if (child.exitCode === null && !childError) child.kill();
    await closed;
  }
}

try {
  await checkProfile('fresh-profile', 1000000001, 1000000002);
  await checkProfile('custom-profile', 9000000101, 9000000102, true);
  await checkProfile('fresh-profile', 1000000001, 1000000002);
  console.log(`PASS startup runtime smoke test (${requests} loopback requests; fixture state retained at ${scratch})`);
} catch (error) {
  console.error(`FAIL ${error.message}\nFixture state: ${scratch}`);
  process.exitCode = 1;
}
