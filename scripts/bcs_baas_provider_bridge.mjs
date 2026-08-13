#!/usr/bin/env node
// Loopback-only BCS Provider bridge. BCS stream callbacks use h2c while
// inject/history use HTTP/1, so both protocol listeners share one public port.
import { createServer as createHttp1Server } from 'node:http';
import { createServer as createHttp2Server } from 'node:http2';
import { createServer as createTcpServer, connect as connectTcp } from 'node:net';
import { readFileSync } from 'node:fs';
import { once } from 'node:events';

const MAX_BODY_BYTES = 1024 * 1024;

function parseArgs(argv) {
  const options = { baasUrl: 'http://127.0.0.1:8890' };
  for (let index = 2; index < argv.length; index += 1) {
    const key = argv[index];
    const value = argv[index + 1];
    if (key === '--port') options.port = Number(value);
    if (key === '--baas-url') options.baasUrl = value;
    if (key === '--token-file') options.tokenFile = value;
    if ([ '--port', '--baas-url', '--token-file' ].includes(key)) index += 1;
  }
  if (!Number.isInteger(options.port) || options.port < 1 || options.port > 65535 || !options.tokenFile) {
    throw new Error('usage: bcs_baas_provider_bridge.mjs --port PORT --token-file PATH [--baas-url URL]');
  }
  options.downlinkUrl = `${options.baasUrl.replace(/\/$/, '')}/bcn/downlink`;
  return options;
}

function credentials(tokenFile) {
  let raw;
  try { raw = JSON.parse(readFileSync(tokenFile, 'utf8')); } catch { throw new Error('runtime credentials unavailable'); }
  if (typeof raw.baas_token !== 'string' || typeof raw.bcs_to_provider_token !== 'string') throw new Error('runtime credentials invalid');
  const references = new Set();
  for (const item of Object.values(raw.provider_bots ?? {})) {
    if (!item || typeof item !== 'object' || typeof item.provider_bot_ref !== 'string') throw new Error('runtime provider bot credentials invalid');
    references.add(item.provider_bot_ref);
  }
  return { baasToken: raw.baas_token, bcsToken: raw.bcs_to_provider_token, references };
}

function bearer(headers) {
  const value = headers.authorization;
  if (typeof value !== 'string') return '';
  const [ scheme, token ] = value.split(' ', 2);
  return scheme?.toLowerCase() === 'bearer' && token ? token : '';
}

function safeLog(message) {
  // Never log message bodies, session ids, or any credential.
  process.stdout.write(`${new Date().toISOString()} ${message}\n`);
}

function json(response, status, body) {
  const payload = JSON.stringify(body);
  response.writeHead(status, { 'content-type': 'application/json', 'content-length': Buffer.byteLength(payload) });
  response.end(payload);
}

async function parseRequest(request) {
  const declared = Number(request.headers['content-length'] ?? 0);
  if (!Number.isInteger(declared) || declared <= 0 || declared > MAX_BODY_BYTES) throw new Error('invalid request size');
  const chunks = [];
  let total = 0;
  for await (const chunk of request) {
    total += chunk.length;
    if (total > MAX_BODY_BYTES) throw new Error('invalid request size');
    chunks.push(chunk);
  }
  const body = JSON.parse(Buffer.concat(chunks).toString('utf8'));
  if (!body || typeof body !== 'object' || Array.isArray(body) || body.type !== 'req') throw new Error('invalid provider request');
  return body;
}

function metadata(body) {
  const toBot = body.to_bot;
  return {
    method: typeof body.method === 'string' ? body.method : '',
    runId: typeof body.id === 'string' ? body.id : '',
    providerBotRef: toBot && typeof toBot === 'object' && typeof toBot.provider_bot_ref === 'string' ? toBot.provider_bot_ref : '',
  };
}

async function copyStream(response, upstream) {
  for await (const chunk of upstream.body) {
    if (!response.write(chunk)) await once(response, 'drain');
  }
  response.end();
}

function handler(options) {
  return async (request, response) => {
    const requestPath = request.headers[':path'] ?? request.url;
    const requestMethod = request.headers[':method'] ?? request.method;
    if (requestMethod === 'GET' && requestPath === '/health') return json(response, 200, { ok: true });
    if (requestMethod !== 'POST' || requestPath !== '/webhook') return json(response, 404, { error: 'not found' });

    let body;
    let auth;
    try {
      body = await parseRequest(request);
      auth = credentials(options.tokenFile);
    } catch {
      return json(response, 400, { error: 'invalid request' });
    }
    const meta = metadata(body);
    if (!meta.runId || !meta.providerBotRef) return json(response, 400, { error: 'missing id or provider_bot_ref' });
    if (bearer(request.headers) !== auth.bcsToken) {
      safeLog(`bridge.reject reason=unauthorized method=${meta.method} run_id=${meta.runId} provider_bot_ref=${meta.providerBotRef}`);
      return json(response, 401, { error: 'unauthorized' });
    }
    if (!auth.references.has(meta.providerBotRef)) {
      safeLog(`bridge.reject reason=provider_bot_mismatch method=${meta.method} run_id=${meta.runId} provider_bot_ref=${meta.providerBotRef}`);
      return json(response, 403, { error: 'provider bot mismatch' });
    }
    if (!['chat.send', 'chat.inject', 'chat.history'].includes(meta.method)) {
      safeLog(`bridge.reject reason=unsupported_method method=${meta.method} run_id=${meta.runId} provider_bot_ref=${meta.providerBotRef}`);
      return json(response, 400, { error: 'unsupported method' });
    }

    const isStream = meta.method === 'chat.send' && String(request.headers['x-bcn-transport'] ?? '').toLowerCase() === 'sse';
    const controller = new AbortController();
    if (isStream) response.once('close', () => { if (!response.writableEnded) controller.abort(); });
    try {
      const upstream = await fetch(options.downlinkUrl, {
        method: 'POST',
        headers: { 'content-type': 'application/json', authorization: `Bearer ${auth.baasToken}`, 'x-bcn-transport': isStream ? 'sse' : 'json' },
        body: JSON.stringify(body),
        signal: controller.signal,
      });
      if (!isStream) {
        const payload = Buffer.from(await upstream.arrayBuffer());
        response.writeHead(upstream.status, { 'content-type': upstream.headers.get('content-type') ?? 'application/json', 'content-length': payload.length });
        response.end(payload);
        safeLog(`bridge.forward method=${meta.method} run_id=${meta.runId} provider_bot_ref=${meta.providerBotRef} status=${upstream.status}`);
        return;
      }
      if (!upstream.ok || !upstream.body) {
        safeLog(`bridge.downlink_failed method=${meta.method} run_id=${meta.runId} provider_bot_ref=${meta.providerBotRef} status=${upstream.status}`);
        return json(response, 502, { error: 'local BaaS downlink unavailable' });
      }
      response.writeHead(upstream.status, {
        'content-type': upstream.headers.get('content-type') ?? 'text/event-stream',
        'cache-control': 'no-cache', 'x-bcn-protocol-version': '2.0', 'x-bcn-run-id': meta.runId,
      });
      await copyStream(response, upstream);
      safeLog(`bridge.stream_complete run_id=${meta.runId} provider_bot_ref=${meta.providerBotRef} cancelled=${controller.signal.aborted}`);
    } catch {
      if (!response.headersSent) json(response, 502, { error: 'local BaaS downlink unavailable' });
      else if (!response.writableEnded) response.destroy();
      safeLog(`bridge.downlink_failed method=${meta.method} run_id=${meta.runId} provider_bot_ref=${meta.providerBotRef} cancelled=${controller.signal.aborted}`);
    }
  };
}

const options = parseArgs(process.argv);
const bridgeHandler = handler(options);
const http1 = createHttp1Server(bridgeHandler);
const http2 = createHttp2Server(bridgeHandler);
const h2Preface = Buffer.from('PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n');
const listenLoopback = server => new Promise(resolve => server.listen(0, '127.0.0.1', () => resolve(server.address().port)));
const http1Port = await listenLoopback(http1);
const http2Port = await listenLoopback(http2);
const server = createTcpServer(socket => {
  const chunks = [];
  let bytes = 0;
  const dispatch = targetPort => {
    socket.pause(); socket.removeListener('data', inspect);
    const buffered = Buffer.concat(chunks, bytes);
    const upstream = connectTcp(targetPort, '127.0.0.1', () => { upstream.write(buffered); socket.pipe(upstream); upstream.pipe(socket); socket.resume(); });
    upstream.on('error', () => socket.destroy()); socket.on('error', () => upstream.destroy());
  };
  const inspect = chunk => {
    chunks.push(chunk); bytes += chunk.length;
    const prefixLength = Math.min(bytes, h2Preface.length);
    const prefix = Buffer.concat(chunks, bytes).subarray(0, prefixLength);
    if (!prefix.equals(h2Preface.subarray(0, prefixLength))) dispatch(http1Port);
    else if (bytes >= h2Preface.length) dispatch(http2Port);
  };
  socket.on('data', inspect);
});
http1.on('clientError', error => safeLog(`bridge.http1_client_error code=${error.code ?? 'unknown'}`));
http2.on('sessionError', error => safeLog(`bridge.http2_session_error code=${error.code ?? 'unknown'}`));
server.listen(options.port, '127.0.0.1', () => safeLog(`bridge.started port=${options.port} baas_host=127.0.0.1`));
