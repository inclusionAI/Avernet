import { strict as assert } from 'node:assert';
import { once } from 'node:events';
import { readFileSync } from 'node:fs';
import { mkdtemp, rm } from 'node:fs/promises';
import type { AddressInfo } from 'node:net';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { WebSocketServer } from 'ws';
import {
  createBcsSessionCleanupGateway,
  resolveBcsSessionCleanupConfig,
  runBcsSessionCleanupOnce,
} from '../src/session-cleanup.js';

const __dirname = dirname(fileURLToPath(import.meta.url));

type GatewayStubFrame = {
  id?: string;
  method?: string;
  ok?: boolean;
  params?: Record<string, unknown>;
};

function collectLogs() {
  const entries: Array<{ level: string; message: string }> = [];
  return {
    entries,
    log: {
      info(...args: unknown[]) {
        entries.push({ level: 'info', message: args.map(String).join(' ') });
      },
      warn(...args: unknown[]) {
        entries.push({ level: 'warn', message: args.map(String).join(' ') });
      },
      error(...args: unknown[]) {
        entries.push({ level: 'error', message: args.map(String).join(' ') });
      },
    },
  };
}

async function startGatewayStub(now: number) {
  const frames: GatewayStubFrame[] = [];
  const server = new WebSocketServer({ host: '127.0.0.1', port: 0 });
  await once(server, 'listening');

  server.on('connection', socket => {
    socket.send(JSON.stringify({
      type: 'event',
      event: 'connect.challenge',
      payload: { nonce: 'test-nonce' },
    }));

    socket.on('message', data => {
      const frame = JSON.parse(data.toString()) as GatewayStubFrame;
      frames.push(frame);

      if (frame.method === 'connect') {
        socket.send(JSON.stringify({
          type: 'res',
          id: frame.id,
          ok: true,
          payload: { type: 'hello-ok', protocol: 4 },
        }));
        return;
      }

      if (frame.method === 'sessions.list') {
        socket.send(JSON.stringify({
          type: 'res',
          id: frame.id,
          ok: true,
          payload: {
            sessions: [
              {
                key: 'agent:main:bcs:group:old-gateway-group',
                updatedAt: now - 3 * 24 * 60 * 60 * 1000,
                lastChannel: 'bcs',
              },
            ],
          },
        }));
        return;
      }

      if (frame.method === 'sessions.delete') {
        socket.send(JSON.stringify({
          type: 'res',
          id: frame.id,
          ok: true,
          payload: { ok: true, deleted: true },
        }));
      }
    });
  });

  const address = server.address() as AddressInfo | null;
  assert.equal(typeof address, 'object');
  assert.ok(address);

  return {
    port: address.port,
    frames,
    async close() {
      await new Promise<void>((resolve, reject) => {
        server.close(err => (err ? reject(err) : resolve()));
      });
    },
  };
}

describe('BCS session cleanup', () => {
  it('declares only pruneAfter and intervalMinutes in the public config schema', () => {
    const manifest = JSON.parse(
      readFileSync(join(__dirname, '..', 'openclaw.plugin.json'), 'utf8'),
    ) as {
      configSchema?: {
        properties?: Record<string, {
          properties?: Record<string, unknown>;
          required?: string[];
        }>;
      };
    };

    const sessionCleanup = manifest.configSchema?.properties?.sessionCleanup;

    assert.ok(sessionCleanup);
    assert.deepEqual(Object.keys(sessionCleanup.properties ?? {}).sort(), [
      'intervalMinutes',
      'pruneAfter',
    ]);
    assert.deepEqual(sessionCleanup.required?.sort(), [
      'intervalMinutes',
      'pruneAfter',
    ]);
  });

  it('enables cleanup with only pruneAfter and intervalMinutes configured', () => {
    const cleanup = resolveBcsSessionCleanupConfig({
      channels: {
        bcs: {
          sessionCleanup: {
            pruneAfter: '2d',
            intervalMinutes: 60,
          },
        },
      },
    });

    assert.equal(cleanup.enabled, true);
    assert.equal(cleanup.pruneAfterMs, 2 * 24 * 60 * 60 * 1000);
    assert.equal(cleanup.intervalMs, 60 * 60 * 1000);
  });

  it('deletes only stale BCS sessions through gateway and logs each deletion', async () => {
    const now = Date.parse('2026-06-09T00:00:00.000Z');
    const deleted: Array<{ key: string; deleteTranscript?: boolean }> = [];
    const { entries, log } = collectLogs();

    const result = await runBcsSessionCleanupOnce({
      now,
      cleanup: {
        enabled: true,
        pruneAfterMs: 2 * 24 * 60 * 60 * 1000,
        intervalMs: 60 * 60 * 1000,
        maxDeletesPerRun: 100,
      },
      gateway: {
        async listSessions() {
          return [
            {
              key: 'agent:main:bcs:group:old-group',
              updatedAt: new Date(now - 3 * 24 * 60 * 60 * 1000).toISOString(),
              lastChannel: 'bcs',
              chatType: 'group',
            },
            {
              key: 'agent:main:bcs:group:fresh-group',
              updatedAt: new Date(now - 60 * 60 * 1000).toISOString(),
              lastChannel: 'bcs',
              chatType: 'group',
            },
            {
              key: 'agent:main:discord:group:old-group',
              updatedAt: new Date(now - 3 * 24 * 60 * 60 * 1000).toISOString(),
              lastChannel: 'discord',
              chatType: 'group',
            },
            {
              key: 'agent:main:bcs:group:active-group',
              updatedAt: new Date(now - 3 * 24 * 60 * 60 * 1000).toISOString(),
              lastChannel: 'bcs',
              chatType: 'group',
              hasActiveRun: true,
            },
            {
              key: 'main',
              updatedAt: new Date(now - 3 * 24 * 60 * 60 * 1000).toISOString(),
              lastChannel: 'bcs',
            },
          ];
        },
        async deleteSession(key: string, params: { deleteTranscript?: boolean }) {
          deleted.push({ key, deleteTranscript: params.deleteTranscript });
        },
      },
      log,
    });

    assert.deepEqual(deleted, [
      {
        key: 'agent:main:bcs:group:old-group',
        deleteTranscript: true,
      },
    ]);
    assert.equal(result.scanned, 5);
    assert.equal(result.deleted, 1);
    assert.equal(result.failed, 0);
    assert.ok(
      entries.some(entry =>
        entry.level === 'info' &&
        entry.message.includes('deleting stale BCS session') &&
        entry.message.includes('agent:main:bcs:group:old-group'),
      ),
      'expected per-session deletion log',
    );
  });

  it('uses Gateway sessions.list and sessions.delete for cleanup deletion', async () => {
    const now = Date.parse('2026-06-09T00:00:00.000Z');
    const previousGatewayPort = process.env.OPENCLAW_GATEWAY_PORT;
    const gateway = await startGatewayStub(now);
    const dataDir = await mkdtemp(join(tmpdir(), 'bcn-session-cleanup-'));
    process.env.OPENCLAW_GATEWAY_PORT = '70000';

    try {
      await runBcsSessionCleanupOnce({
        now,
        cleanup: {
          enabled: true,
          pruneAfterMs: 2 * 24 * 60 * 60 * 1000,
          intervalMs: 60 * 60 * 1000,
          maxDeletesPerRun: 100,
        },
        gateway: createBcsSessionCleanupGateway({
          cfg: {
            gateway: {
              port: gateway.port,
              auth: { token: 'test-token' },
            },
          },
          dataDir,
        }),
      });

      assert.ok(gateway.frames.some(frame => frame.method === 'sessions.list'));
      const deleteFrame = gateway.frames.find(frame => frame.method === 'sessions.delete');
      assert.equal(deleteFrame?.params?.key, 'agent:main:bcs:group:old-gateway-group');
      assert.equal(deleteFrame?.params?.deleteTranscript, true);
      assert.equal('force' in (deleteFrame?.params ?? {}), false);
      const connectFrames = gateway.frames.filter(frame => frame.method === 'connect');
      assert.deepEqual(connectFrames.map(frame => frame.params?.scopes), [
        [ 'operator.read' ],
        [ 'operator.admin' ],
      ]);
    } finally {
      if (previousGatewayPort === undefined) {
        delete process.env.OPENCLAW_GATEWAY_PORT;
      } else {
        process.env.OPENCLAW_GATEWAY_PORT = previousGatewayPort;
      }
      await gateway.close();
      await rm(dataDir, { recursive: true, force: true });
    }
  });
});
