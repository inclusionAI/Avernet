#!/usr/bin/env node
/** Contract tests for the h2c BCS Provider -> BaaS bridge. */

import assert from 'node:assert/strict';
import { createServer as createHttpServer } from 'node:http';
import http2 from 'node:http2';
import { createServer as createTcpServer, connect as connectTcp } from 'node:net';
import { spawn } from 'node:child_process';
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { once } from 'node:events';

const ROOT = new URL('.', import.meta.url).pathname;
const BRIDGE = join(ROOT, 'bcs_baas_provider_bridge.mjs');

function listen(server) {
  return new Promise((resolve) => server.listen(0, '127.0.0.1', () => resolve(server.address().port)));
}

function close(server) {
  return new Promise((resolve) => server.close(resolve));
}

function sleep(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

async function waitForHealth(port) {
  for (let attempt = 0; attempt < 40; attempt += 1) {
    try {
      const response = await h2Request(port, { method: 'GET', path: '/health' });
      if (response.status === 200 && JSON.parse(response.body).ok === true) return;
    } catch {
      // The bridge process may still be binding its h2c listener.
    }
    await sleep(50);
  }
  throw new Error('bridge did not become healthy');
}

function h2Request(port, { method = 'POST', path = '/webhook', token = 'bcs-to-provider-test', body } = {}) {
  return new Promise((resolve, reject) => {
    const client = http2.connect(`http://127.0.0.1:${port}`);
    const headers = { ':method': method, ':path': path };
    const encoded = body === undefined ? undefined : JSON.stringify(body);
    if (encoded !== undefined) {
      headers.authorization = `Bearer ${token}`;
      headers['content-type'] = 'application/json';
      headers['content-length'] = Buffer.byteLength(encoded);
      if (body.method === 'chat.send') headers['x-bcn-transport'] = 'sse';
    }
    let status;
    const chunks = [];
    const request = client.request(headers);
    request.on('response', (responseHeaders) => { status = responseHeaders[':status']; });
    request.on('data', (chunk) => chunks.push(chunk));
    request.on('error', reject);
    client.on('error', reject);
    request.on('end', () => {
      client.close();
      resolve({ status, body: Buffer.concat(chunks).toString('utf8') });
    });
    if (encoded !== undefined) request.end(encoded);
    else request.end();
  });
}

async function h2RequestWithSplitPreface(port, options) {
  const proxy = createTcpServer((client) => {
    const upstream = connectTcp(port, '127.0.0.1');
    let firstChunk = true;
    client.on('data', (chunk) => {
      if (firstChunk) {
        firstChunk = false;
        upstream.write(chunk.subarray(0, 8));
        setTimeout(() => upstream.write(chunk.subarray(8)), 10);
      } else {
        upstream.write(chunk);
      }
    });
    upstream.on('data', (chunk) => client.write(chunk));
    upstream.on('error', () => client.destroy());
    client.on('error', () => upstream.destroy());
  });
  const proxyPort = await listen(proxy);
  try {
    return await h2Request(proxyPort, options);
  } finally {
    await close(proxy);
  }
}

async function h1Request(port, body, token = 'bcs-to-provider-test') {
  const response = await fetch(`http://127.0.0.1:${port}/webhook`, {
    method: 'POST',
    headers: {
      authorization: `Bearer ${token}`,
      'content-type': 'application/json',
    },
    body: JSON.stringify(body),
  });
  return { status: response.status, body: await response.text() };
}

function providerBody(method, runId = 'run-1') {
  return {
    type: 'req',
    id: runId,
    method,
    session_id: 'session-1',
    bcn_group_id: 'group-1',
    to_bot: { provider_id: 'provider-1', provider_bot_ref: 'bot-1:mock-user' },
    from: { kind: 'human', id: 'mock-user' },
    message: { role: 'user', content: 'not logged' },
  };
}

const seen = [];
const fakeBaas = createHttpServer(async (request, response) => {
  const chunks = [];
  for await (const chunk of request) chunks.push(chunk);
  const body = JSON.parse(Buffer.concat(chunks).toString('utf8'));
  seen.push({ body, transport: request.headers['x-bcn-transport'], authorization: request.headers.authorization });
  if (request.headers['x-bcn-transport'] === 'sse') {
    response.writeHead(200, { 'content-type': 'text/event-stream' });
    const state = body.id === 'run-error' ? 'error' : 'final';
    response.end(
      `id: 1\nevent: chat\ndata: {"runId":"${body.id}","seq":1,"state":"delta","deltaText":"ok"}\n\n`
      + `id: 2\nevent: chat\ndata: {"runId":"${body.id}","seq":2,"state":"${state}"}\n\n`,
    );
    return;
  }
  response.writeHead(200, { 'content-type': 'application/json' });
  response.end(body.method === 'chat.history' ? '{"ok":true,"messages":[]}' : '{"ok":true}');
});

const tmp = mkdtempSync(join(tmpdir(), 'bcs-baas-bridge-'));
const tokenFile = join(tmp, 'tokens.json');
writeFileSync(tokenFile, JSON.stringify({
  baas_token: 'baas-test',
  bcs_to_provider_token: 'bcs-to-provider-test',
  provider_bots: { planner: { provider_bot_ref: 'bot-1:mock-user' } },
}), { mode: 0o600 });

let bridge;
let bridgeOutput = '';
let baasPort;
let bridgePort;
try {
  baasPort = await listen(fakeBaas);
  const reservation = createHttpServer();
  bridgePort = await listen(reservation);
  await close(reservation);
  bridge = spawn(process.execPath, [BRIDGE, '--port', String(bridgePort), '--baas-url', `http://127.0.0.1:${baasPort}`, '--token-file', tokenFile], {
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  bridge.stdout.on('data', (chunk) => { bridgeOutput += chunk; });
  bridge.stderr.on('data', (chunk) => { bridgeOutput += chunk; });
  await waitForHealth(bridgePort);

  let response = await h2Request(bridgePort, { body: providerBody('chat.send', 'run-preserved') });
  assert.equal(response.status, 200);
  assert.match(response.body, /"runId":"run-preserved"/);
  assert.match(response.body, /event: chat/);
  assert.equal(seen.at(-1).transport, 'sse');
  assert.equal(seen.at(-1).authorization, 'Bearer baas-test');

  response = await h2RequestWithSplitPreface(bridgePort, { body: providerBody('chat.send', 'run-split-preface') });
  assert.equal(response.status, 200);
  assert.match(response.body, /"runId":"run-split-preface"/);

  response = await h2Request(bridgePort, { body: providerBody('chat.send', 'run-error') });
  assert.equal(response.status, 200);
  assert.match(response.body, /"state":"error"/);

  response = await h1Request(bridgePort, providerBody('chat.inject'));
  assert.equal(response.status, 200);
  assert.deepEqual(JSON.parse(response.body), { ok: true });
  response = await h1Request(bridgePort, providerBody('chat.history'));
  assert.equal(response.status, 200);
  assert.deepEqual(JSON.parse(response.body), { ok: true, messages: [] });

  response = await h1Request(bridgePort, { ...providerBody('chat.inject'), to_bot: { provider_id: 'provider-1', provider_bot_ref: 'other-bot:mock-user' } });
  assert.equal(response.status, 403);

  response = await h1Request(bridgePort, providerBody('chat.inject'), 'provider-runtime-test');
  assert.equal(response.status, 401);

  response = await h1Request(bridgePort, providerBody('chat.abort', 'abort-event'));
  assert.equal(response.status, 400);
  assert.deepEqual(JSON.parse(response.body), { error: 'unsupported method' });

  assert.match(bridgeOutput, /bridge\.reject reason=provider_bot_mismatch method=chat\.inject run_id=run-1 provider_bot_ref=other-bot:mock-user/);
  assert.match(bridgeOutput, /bridge\.reject reason=unauthorized method=chat\.inject run_id=run-1 provider_bot_ref=bot-1:mock-user/);
  assert.match(bridgeOutput, /bridge\.reject reason=abort_unsupported provider_bot_ref=bot-1:mock-user/);
  assert.doesNotMatch(bridgeOutput, /not logged/);
  process.stdout.write('BCS h2c Provider bridge contract tests passed\n');
} finally {
  if (bridge && bridge.exitCode === null) {
    bridge.kill('SIGTERM');
    await once(bridge, 'exit');
  }
  await close(fakeBaas);
  rmSync(tmp, { recursive: true, force: true });
}
