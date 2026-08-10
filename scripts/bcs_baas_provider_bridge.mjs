#!/usr/bin/env node
/**
 * Loopback-only BCS Provider -> local BaaS bridge.
 *
 * BCS protocol 2.0 streams use h2c with HTTP/2 prior knowledge.  The bridge
 * therefore deliberately uses Node's native HTTP/2 server rather than an
 * HTTP/1 server.  BCS authenticates every webhook with the Provider-level
 * bcs_to_provider_token; the bridge separately verifies that the requested
 * provider_bot_ref is one of this local Provider's three registered bots.
 */

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
    if (key === '--port' || key === '--baas-url' || key === '--token-file') index += 1;
  }
  if (!Number.isInteger(options.port) || options.port < 1 || options.port > 65535 || !options.tokenFile) {
    throw new Error('usage: bcs_baas_provider_bridge.mjs --port PORT --token-file PATH [--baas-url URL]');
  }
  options.downlinkUrl = `${options.baasUrl.replace(/\/$/, '')}/bcn/downlink`;
  return options;
}

function runtimeCredentials(tokenFile) {
  let raw;
  try {
    raw = JSON.parse(readFileSync(tokenFile, 'utf8'));
  } catch {
    throw new Error('runtime credential file is unavailable');
  }
  if (typeof raw.baas_token !== 'string' || raw.baas_token.length === 0) {
    throw new Error('runtime BaaS credential is missing');
  }
  if (raw.provider_bots !== undefined && (raw.provider_bots === null || typeof raw.provider_bots !== 'object')) {
    throw new Error('provider credentials are invalid');
  }
  if (typeof raw.bcs_to_provider_token !== 'string' || raw.bcs_to_provider_token.length === 0) {
    throw new Error('BCS Provider credential is missing');
  }
  const providerBotRefs = new Set();
  for (const record of Object.values(raw.provider_bots ?? {})) {
    if (!record || typeof record !== 'object') continue;
    const { provider_bot_ref: providerBotRef } = record;
    if (typeof providerBotRef !== 'string' || providerBotRef.length === 0) {
      throw new Error('provider credentials are invalid');
    }
    providerBotRefs.add(providerBotRef);
  }
  return {
    baasToken: raw.baas_token,
    bcsToProviderToken: raw.bcs_to_provider_token,
    providerBotRefs,
  };
}

function bearerToken(headers) {
  const authorization = headers.authorization;
  if (typeof authorization !== 'string') return '';
  const [scheme, token] = authorization.split(' ', 2);
  return scheme?.toLowerCase() === 'bearer' && token ? token : '';
}

function responseJson(response, status, payload) {
  const body = JSON.stringify(payload);
  response.writeHead(status, {
    'content-type': 'application/json',
    'content-length': Buffer.byteLength(body),
  });
  response.end(body);
}

async function requestJson(request) {
  const declared = Number(request.headers['content-length'] ?? 0);
  if (!Number.isInteger(declared) || declared <= 0 || declared > MAX_BODY_BYTES) {
    throw new Error('invalid request size');
  }
  const chunks = [];
  let total = 0;
  for await (const chunk of request) {
    total += chunk.length;
    if (total > MAX_BODY_BYTES) throw new Error('invalid request size');
    chunks.push(chunk);
  }
  const parsed = JSON.parse(Buffer.concat(chunks).toString('utf8'));
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed) || parsed.type !== 'req') {
    throw new Error('invalid provider request');
  }
  return parsed;
}

function requestMeta(body) {
  const providerBotRef = body.to_bot && typeof body.to_bot === 'object'
    ? body.to_bot.provider_bot_ref
    : undefined;
  return {
    method: typeof body.method === 'string' ? body.method : '',
    runId: typeof body.id === 'string' ? body.id : '',
    providerBotRef: typeof providerBotRef === 'string' ? providerBotRef : '',
  };
}

function safeLog(message) {
  // Deliberately restrict diagnostic fields to method, run ID, provider ref,
  // and status. Tokens and message bodies never enter this process's logs.
  process.stdout.write(`${new Date().toISOString()} ${message}\n`);
}

async function writeStream(response, upstream) {
  for await (const chunk of upstream.body) {
    if (!response.write(chunk)) await once(response, 'drain');
  }
  response.end();
}

function bridgeHandler(options, activeRuns) {
  return async (request, response) => {
    const path = request.headers[':path'] ?? request.url;
    const method = request.headers[':method'] ?? request.method;
    if (method === 'GET' && path === '/health') {
      responseJson(response, 200, { ok: true });
      return;
    }
    if (method !== 'POST' || path !== '/webhook') {
      responseJson(response, 404, { error: 'not found' });
      return;
    }

    let body;
    let credentials;
    try {
      body = await requestJson(request);
      credentials = runtimeCredentials(options.tokenFile);
    } catch {
      responseJson(response, 400, { error: 'invalid request' });
      return;
    }

    const meta = requestMeta(body);
    if (!meta.runId || !meta.providerBotRef) {
      responseJson(response, 400, { error: 'missing id or provider_bot_ref' });
      return;
    }
    if (bearerToken(request.headers) !== credentials.bcsToProviderToken) {
      safeLog(`bridge.reject reason=unauthorized method=${meta.method} run_id=${meta.runId} provider_bot_ref=${meta.providerBotRef}`);
      responseJson(response, 401, { error: 'unauthorized' });
      return;
    }
    if (!credentials.providerBotRefs.has(meta.providerBotRef)) {
      safeLog(`bridge.reject reason=provider_bot_mismatch method=${meta.method} run_id=${meta.runId} provider_bot_ref=${meta.providerBotRef}`);
      responseJson(response, 403, { error: 'provider bot mismatch' });
      return;
    }
    if (meta.method === 'chat.abort') {
      safeLog(`bridge.reject reason=abort_unsupported provider_bot_ref=${meta.providerBotRef}`);
      responseJson(response, 400, { error: 'unsupported method' });
      return;
    }
    if (!['chat.send', 'chat.inject', 'chat.history'].includes(meta.method)) {
      responseJson(response, 400, { error: 'unsupported method' });
      return;
    }

    const isStream = meta.method === 'chat.send' && String(request.headers['x-bcn-transport'] ?? '').toLowerCase() === 'sse';
    const controller = new AbortController();
    if (isStream) {
      activeRuns.set(meta.runId, controller);
      response.once('close', () => {
        if (!response.writableEnded) controller.abort();
      });
    }
    try {
      const upstream = await fetch(options.downlinkUrl, {
        method: 'POST',
        headers: {
          'content-type': 'application/json',
          authorization: `Bearer ${credentials.baasToken}`,
          'x-bcn-transport': isStream ? 'sse' : 'json',
        },
        body: JSON.stringify(body),
        signal: controller.signal,
      });
      if (!isStream) {
        const payload = Buffer.from(await upstream.arrayBuffer());
        response.writeHead(upstream.status, {
          'content-type': upstream.headers.get('content-type') ?? 'application/json',
          'content-length': payload.length,
        });
        response.end(payload);
        safeLog(`bridge.forward method=${meta.method} run_id=${meta.runId} status=${upstream.status}`);
        return;
      }
      if (!upstream.ok || !upstream.body) {
        responseJson(response, 502, { error: 'local BaaS downlink unavailable' });
        safeLog(`bridge.downlink_failed method=${meta.method} run_id=${meta.runId} status=${upstream.status}`);
        return;
      }
      response.writeHead(upstream.status, {
        'content-type': upstream.headers.get('content-type') ?? 'text/event-stream',
        'cache-control': 'no-cache',
        'x-bcn-protocol-version': '2.0',
        'x-bcn-run-id': meta.runId,
      });
      await writeStream(response, upstream);
      safeLog(`bridge.stream_complete run_id=${meta.runId} cancelled=${controller.signal.aborted}`);
    } catch (error) {
      if (!response.headersSent) responseJson(response, 502, { error: 'local BaaS downlink unavailable' });
      else if (!response.writableEnded) response.destroy();
      safeLog(`bridge.downlink_failed method=${meta.method} run_id=${meta.runId} cancelled=${controller.signal.aborted}`);
    } finally {
      if (activeRuns.get(meta.runId) === controller) activeRuns.delete(meta.runId);
    }
  };
}

const options = parseArgs(process.argv);
const handler = bridgeHandler(options, new Map());
const http1Server = createHttp1Server(handler);
const http2Server = createHttp2Server(handler);
const h2Preface = Buffer.from('PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n');
function listenLoopback(server) {
  return new Promise((resolve) => server.listen(0, '127.0.0.1', () => resolve(server.address().port)));
}

const http1Port = await listenLoopback(http1Server);
const http2Port = await listenLoopback(http2Server);
const server = createTcpServer((socket) => {
  const bufferedChunks = [];
  let bufferedBytes = 0;
  const dispatch = (targetPort) => {
    socket.pause();
    socket.removeListener('data', inspectProtocol);
    const buffered = Buffer.concat(bufferedChunks, bufferedBytes);
    const upstream = connectTcp(targetPort, '127.0.0.1', () => {
      upstream.write(buffered);
      socket.pipe(upstream);
      upstream.pipe(socket);
      socket.resume();
    });
    upstream.on('error', () => socket.destroy());
    socket.on('error', () => upstream.destroy());
  };
  const inspectProtocol = (chunk) => {
    bufferedChunks.push(chunk);
    bufferedBytes += chunk.length;
    const prefixLength = Math.min(bufferedBytes, h2Preface.length);
    const prefix = Buffer.concat(bufferedChunks, bufferedBytes).subarray(0, prefixLength);
    // BCS SSE uses cleartext HTTP/2 prior knowledge; BCS callback requests
    // (inject/history) use HTTP/1. A local proxy keeps one Provider URL
    // while handing each accepted socket to a protocol-native server. TCP can
    // split the 24-byte h2c preface, so wait for a full match before choosing
    // HTTP/2 and retain every received byte for the selected listener.
    if (!prefix.equals(h2Preface.subarray(0, prefixLength))) {
      dispatch(http1Port);
    } else if (bufferedBytes >= h2Preface.length) {
      dispatch(http2Port);
    }
  };
  socket.on('data', inspectProtocol);
});
http1Server.on('clientError', (error) => safeLog(`bridge.http1_client_error code=${error.code ?? 'unknown'}`));
http2Server.on('sessionError', (error) => safeLog(`bridge.http2_session_error code=${error.code ?? 'unknown'}`));
server.listen(options.port, '127.0.0.1', () => {
  safeLog(`bridge.started port=${options.port} baas_host=127.0.0.1`);
});
