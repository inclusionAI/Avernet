import assert from 'node:assert/strict';
import { createServer, type Server } from 'node:http';
import { test } from 'node:test';
import { WebSocketServer, type WebSocket } from 'ws';
import { resolveEndpoint } from '../src/endpoint.js';
import type { BotSession, RequestFrame } from '../src/protocol.js';
import { BcnWsClient } from '../src/ws-client.js';
import { testConfig, waitFor } from './fixtures.js';

interface LocalBcnServer {
  http: Server;
  wss: WebSocketServer;
  endpoint: string;
  frames: Array<Record<string, unknown>>;
  sockets: WebSocket[];
}

async function localBcnServer(
  onFrame?: (socket: WebSocket, frame: Record<string, unknown>, server: LocalBcnServer) => void,
): Promise<LocalBcnServer> {
  const http = createServer();
  const wss = new WebSocketServer({ server: http, path: '/ws/bot' });
  await new Promise<void>(resolve => http.listen(0, '127.0.0.1', resolve));
  const address = http.address();
  if (!address || typeof address === 'string') throw new Error('local server did not bind a TCP port');
  const result: LocalBcnServer = {
    http,
    wss,
    endpoint: `http://127.0.0.1:${address.port}/`,
    frames: [],
    sockets: [],
  };
  wss.on('connection', socket => {
    result.sockets.push(socket);
    socket.on('message', raw => {
      const frame = JSON.parse(raw.toString()) as Record<string, unknown>;
      result.frames.push(frame);
      if (onFrame) onFrame(socket, frame, result);
      else defaultFrameHandler(socket, frame);
    });
  });
  return result;
}

function defaultFrameHandler(socket: WebSocket, frame: Record<string, unknown>): void {
  if (frame.type !== 'req' || typeof frame.id !== 'string') return;
  if (frame.method === 'bot.connect') {
    socket.send(JSON.stringify({
      type: 'res',
      id: frame.id,
      ok: true,
      payload: {
        is_new: false,
        bot_uuid: 'bot-123',
        token: 'bot-secret',
        protocol_version: 2,
        min_supported_version: 2,
      },
    }));
  } else if (frame.method === 'bot.status') {
    socket.send(JSON.stringify({ type: 'res', id: frame.id, ok: true, payload: {} }));
  }
}

async function closeServer(server: LocalBcnServer): Promise<void> {
  for (const socket of server.sockets) socket.terminate();
  await new Promise<void>(resolve => server.wss.close(() => resolve()));
  await new Promise<void>(resolve => server.http.close(() => resolve()));
}

function botSession(endpoint: string): BotSession {
  return { version: 1, endpoint, botUuid: 'bot-123', botToken: 'bot-secret', botName: 'DSH Bot' };
}

test('negotiates BCN Bot WebSocket V2, dispatches requests, and sends canonical events', async t => {
  const server = await localBcnServer();
  t.after(() => closeServer(server));
  const endpoint = await resolveEndpoint(server.endpoint);
  const client = new BcnWsClient({
    endpoint,
    session: botSession(server.endpoint),
    config: testConfig({ endpoint: server.endpoint }),
    onSessionChanged: async () => {},
  });
  t.after(() => client.stop());
  client.onRequest('chat.send', async frame => {
    client.sendResponse(frame.id, true, { run_id: 'run-1' });
  });
  client.start();
  await waitFor(() => client.connected, 'client did not negotiate bot.connect');

  const connect = server.frames.find(frame => frame.method === 'bot.connect') as RequestFrame | undefined;
  assert.equal(connect?.params.protocol_version, 2);
  assert.equal(connect?.params.bot_id, 'bot-123');
  assert.equal(connect?.params.token, 'bot-secret');

  const socket = server.sockets[0];
  assert.ok(socket);
  socket.send(JSON.stringify({
    type: 'req',
    id: 'bcs-request-1',
    method: 'chat.send',
    params: {},
  }));
  await waitFor(
    () => server.frames.some(frame => frame.type === 'res' && frame.id === 'bcs-request-1'),
    'client did not respond to BCS request',
  );

  client.sendEvent('agent', {
    run_id: 'run-1',
    bcs_group_id: 'group-1',
    stream: 'tool',
    ts: Date.now(),
    data: { phase: 'start', toolCallId: 'call-1', name: 'read', args: {} },
  });
  await waitFor(() => server.frames.some(frame => frame.type === 'event' && frame.event === 'agent'), 'event not sent');
});

test('reconnects with exponential backoff and stops reconnecting after disposal', async t => {
  let connects = 0;
  const server = await localBcnServer((socket, frame) => {
    if (frame.method === 'bot.connect') {
      connects += 1;
      if (typeof frame.id !== 'string') return;
      socket.send(JSON.stringify({
        type: 'res',
        id: frame.id,
        ok: true,
        payload: {
          is_new: false,
          bot_uuid: 'bot-123',
          token: 'bot-secret',
          protocol_version: connects === 1 ? 1 : 2,
        },
      }));
    } else {
      defaultFrameHandler(socket, frame);
    }
  });
  t.after(() => closeServer(server));
  const endpoint = await resolveEndpoint(server.endpoint);
  const client = new BcnWsClient({
    endpoint,
    session: botSession(server.endpoint),
    config: testConfig({ endpoint: server.endpoint, reconnectInitialMs: 50, reconnectMaxMs: 100 }),
    onSessionChanged: async () => {},
  });
  client.start();
  await waitFor(() => connects >= 2 && client.connected, 'client did not reconnect', 4_000);
  await client.stop();
  const stoppedAt = connects;
  await new Promise(resolve => setTimeout(resolve, 200));
  assert.equal(connects, stoppedAt);
});

test('persists a rotated bot.connect token before marking the connection ready', async t => {
  const server = await localBcnServer((socket, frame) => {
    if (frame.type !== 'req' || frame.method !== 'bot.connect' || typeof frame.id !== 'string') return;
    socket.send(JSON.stringify({
      type: 'res',
      id: frame.id,
      ok: true,
      payload: {
        is_new: false,
        bot_uuid: 'bot-123',
        token: 'rotated-secret',
        protocol_version: 2,
      },
    }));
  });
  t.after(() => closeServer(server));
  const endpoint = await resolveEndpoint(server.endpoint);
  const persisted: BotSession[] = [];
  const client = new BcnWsClient({
    endpoint,
    session: botSession(server.endpoint),
    config: testConfig({ endpoint: server.endpoint }),
    onSessionChanged: async session => { persisted.push(session); },
  });
  t.after(() => client.stop());
  client.start();
  await waitFor(() => client.connected, 'client did not connect');
  assert.equal(persisted[0]?.botToken, 'rotated-secret');
  assert.equal(client.botSession.botToken, 'rotated-secret');
});
