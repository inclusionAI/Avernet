import { strict as assert } from 'node:assert';
import { once } from 'node:events';
import { mkdtemp, rm, stat } from 'node:fs/promises';
import type { AddressInfo } from 'node:net';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { WebSocketServer } from 'ws';
import { BcsWsClient } from '../src/bcs-ws-client.js';
import type { ResolvedBcsAccount, SessionInfo } from '../src/types.js';

async function startBcsStub() {
  let cookieHeader: string | undefined;
  let requestUrl: string | undefined;
  let connectParams: Record<string, unknown> | undefined;
  const server = new WebSocketServer({ host: '127.0.0.1', port: 0 });
  await once(server, 'listening');

  server.on('connection', (socket, request) => {
    cookieHeader = request.headers.cookie;
    requestUrl = request.url;

    socket.on('message', data => {
      const frame = JSON.parse(data.toString());
      if (frame.method === 'bot.connect') {
        connectParams = frame.params;
        socket.send(JSON.stringify({
          type: 'res',
          id: frame.id,
          ok: true,
          payload: {
            is_new: false,
            bot_uuid: 'bot-1',
            token: 'next-token',
            protocol_version: 2,
          },
        }));
      }
    });
  });

  const address = server.address() as AddressInfo | null;
  assert.equal(typeof address, 'object');
  assert.ok(address);

  return {
    port: address.port,
    get cookieHeader() {
      return cookieHeader;
    },
    get requestUrl() {
      return requestUrl;
    },
    get connectParams() {
      return connectParams;
    },
    async close() {
      await new Promise<void>((resolve, reject) => {
        server.close(err => (err ? reject(err) : resolve()));
      });
    },
  };
}

describe('BcsWsClient security behavior', () => {
  it('does not send Cookie headers or log reconnect tokens', async () => {
    const bcs = await startBcsStub();
    const dataDir = await mkdtemp(join(tmpdir(), 'bcn-ws-client-'));
    const logs: string[] = [];
    const token = 'secret-reconnect-token';
    const cookie = 'session=secret-cookie';
    const account: ResolvedBcsAccount = {
      accountId: 'default',
      enabled: true,
      bcsUrl: `ws://127.0.0.1:${bcs.port}/ws/bot`,
      botId: 'bot-1',
      botName: 'Bot 1',
      capabilities: {
        summary: 'test bot',
        domains: [],
        skills: [],
        scopes: [],
      },
      heartbeatIntervalMs: 60_000,
      reconnectIntervalMs: 5_000,
      connectionTimeoutMs: 10_000,
      cookie,
    } as ResolvedBcsAccount & { cookie: string };
    const session: SessionInfo = {
      bot_uuid: 'bot-1',
      token,
      bcs_url: account.bcsUrl,
    };
    const client = new BcsWsClient({
      account,
      dataDir,
      log: {
        info: (...args: unknown[]) => logs.push(args.join(' ')),
        warn: (...args: unknown[]) => logs.push(args.join(' ')),
        error: (...args: unknown[]) => logs.push(args.join(' ')),
      },
    });

    try {
      await client.connect(session);
      assert.equal(bcs.cookieHeader, undefined);
      assert.equal(logs.some(line => line.includes(token)), false);
      assert.equal(logs.some(line => line.includes(cookie)), false);
    } finally {
      await client.disconnect();
      await bcs.close();
      await rm(dataDir, { recursive: true, force: true });
    }
  });

  it('sends reconnect token only in bot.connect and always dials the configured URL', async () => {
    const bcs = await startBcsStub();
    const dataDir = await mkdtemp(join(tmpdir(), 'bcn-ws-client-'));
    const token = 'secret-reconnect-token';
    const account: ResolvedBcsAccount = {
      accountId: 'default',
      enabled: true,
      bcsUrl: `ws://127.0.0.1:${bcs.port}/configured`,
      botId: 'bot-1',
      botName: 'Bot 1',
      capabilities: {
        summary: 'test bot',
        domains: [],
        skills: [],
        scopes: [],
      },
      heartbeatIntervalMs: 60_000,
      reconnectIntervalMs: 5_000,
      connectionTimeoutMs: 10_000,
    };
    const session: SessionInfo = {
      bot_uuid: 'bot-1',
      token,
      bcs_url: `ws://127.0.0.1:${bcs.port}/session-file`,
    };
    const client = new BcsWsClient({
      account,
      dataDir,
      log: {
        info: () => undefined,
        warn: () => undefined,
        error: () => undefined,
      },
    });

    try {
      await client.connect(session);
      assert.equal(bcs.requestUrl, '/configured');
      assert.equal(bcs.connectParams?.token, token);
    } finally {
      await client.disconnect();
      await bcs.close();
      await rm(dataDir, { recursive: true, force: true });
    }
  });

  it('rejects non-WebSocket BCS URLs before connecting', async () => {
    const account: ResolvedBcsAccount = {
      accountId: 'default',
      enabled: true,
      bcsUrl: 'http://127.0.0.1:21000/ws/bot',
      botId: 'bot-1',
      botName: 'Bot 1',
      capabilities: {
        summary: 'test bot',
        domains: [],
        skills: [],
        scopes: [],
      },
      heartbeatIntervalMs: 60_000,
      reconnectIntervalMs: 5_000,
      connectionTimeoutMs: 10_000,
    };
    const client = new BcsWsClient({ account });

    await assert.rejects(
      () => client.connect(null),
      /Invalid BCS WebSocket URL/,
    );
  });

  it('saves session files with owner-only permissions', async () => {
    const originalUmask = process.umask(0o022);
    const bcs = await startBcsStub();
    const dataDir = await mkdtemp(join(tmpdir(), 'bcn-session-mode-'));
    const account: ResolvedBcsAccount = {
      accountId: 'default',
      enabled: true,
      bcsUrl: `ws://127.0.0.1:${bcs.port}/ws/bot`,
      botId: 'bot-1',
      botName: 'Bot 1',
      capabilities: {
        summary: 'test bot',
        domains: [],
        skills: [],
        scopes: [],
      },
      heartbeatIntervalMs: 60_000,
      reconnectIntervalMs: 5_000,
      connectionTimeoutMs: 10_000,
    };
    const client = new BcsWsClient({
      account,
      dataDir,
      log: {
        info: () => undefined,
        warn: () => undefined,
        error: () => undefined,
      },
    });

    try {
      await client.connect(null);
      const sessionPath = join(dataDir, '.bcs', 'session.json');
      const sessionStat = await stat(sessionPath);
      assert.equal(sessionStat.mode.toString(8).slice(-3), '600');
    } finally {
      process.umask(originalUmask);
      await client.disconnect();
      await bcs.close();
      await rm(dataDir, { recursive: true, force: true });
    }
  });
});
