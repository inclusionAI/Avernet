import { strict as assert } from 'node:assert';
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createBcsPlugin } from '../src/channel.js';
import { setBcsRuntime } from '../src/runtime.js';

describe('BCS channel service bot behavior', () => {
  let originalHome: string | undefined;
  let originalBotDataDir: string | undefined;
  let originalOpenClawDataDir: string | undefined;
  let originalBotType: string | undefined;
  let originalIgnoreCredentials: string | undefined;

  beforeEach(() => {
    originalHome = process.env.HOME;
    originalBotDataDir = process.env.BOT_DATA_DIR;
    originalOpenClawDataDir = process.env.OPENCLAW_DATA_DIR;
    originalBotType = process.env.BOT_TYPE;
    originalIgnoreCredentials = process.env.BCS_IGNORE_CREDENTIALS;
    delete process.env.BOT_DATA_DIR;
    delete process.env.OPENCLAW_DATA_DIR;
    delete process.env.BOT_TYPE;
    delete process.env.BCS_IGNORE_CREDENTIALS;
  });

  afterEach(() => {
    if (originalHome === undefined) delete process.env.HOME;
    else process.env.HOME = originalHome;
    if (originalBotDataDir === undefined) delete process.env.BOT_DATA_DIR;
    else process.env.BOT_DATA_DIR = originalBotDataDir;
    if (originalOpenClawDataDir === undefined) delete process.env.OPENCLAW_DATA_DIR;
    else process.env.OPENCLAW_DATA_DIR = originalOpenClawDataDir;
    if (originalBotType === undefined) delete process.env.BOT_TYPE;
    else process.env.BOT_TYPE = originalBotType;
    if (originalIgnoreCredentials === undefined) delete process.env.BCS_IGNORE_CREDENTIALS;
    else process.env.BCS_IGNORE_CREDENTIALS = originalIgnoreCredentials;
  });

  it('bootstraps a service bot session and skips the WebSocket connection', async () => {
    const dataDir = mkdtempSync(join(tmpdir(), 'bcn-service-channel-'));
    const storePath = join(dataDir, 'agents', 'main', 'sessions', 'sessions.json');
    const sessionPath = join(dataDir, '.bcs', 'session.json');
    const cfg = {
      channels: {
        bcs: {
          bcsUrl: 'ws://127.0.0.1:1/ws/bot',
        },
      },
      session: { store: storePath },
    };
    const logs: string[] = [];
    const abortController = new AbortController();
    process.env.HOME = dataDir;
    writeFileSync(
      join(dataDir, '.credentials'),
      'BOT_TYPE=service\nBOT_ID=service-bot\nOWNER_ID=owner',
      'utf-8',
    );
    setBcsRuntime({
      config: { async loadConfig() { return cfg; } },
      channel: { session: { resolveStorePath() { return storePath; } } },
    } as any);
    const channel = createBcsPlugin();

    try {
      const started = channel.gateway.startAccount({
        cfg,
        accountId: 'default',
        abortSignal: abortController.signal,
        log: {
          info: (...args: unknown[]) => logs.push(args.join(' ')),
          warn: (...args: unknown[]) => logs.push(args.join(' ')),
        },
      });

      for (let i = 0; i < 20 && !existsSync(sessionPath); i++) {
        await new Promise(resolve => setTimeout(resolve, 5));
      }
      assert.deepEqual(JSON.parse(readFileSync(sessionPath, 'utf-8')), {
        bot_uuid: 'service-bot:owner',
        token: 'dummy',
        bcs_url: 'ws://127.0.0.1:1/ws/bot',
      });
      assert.equal(logs.some(line => line.includes('skipping WebSocket connection')), true);

      abortController.abort();
      await started;
    } finally {
      abortController.abort();
      rmSync(dataDir, { recursive: true, force: true });
    }
  });

  it('combines the default service check with a custom skip hook', async () => {
    const dataDir = mkdtempSync(join(tmpdir(), 'bcn-custom-skip-'));
    const cfg = { channels: { bcs: { bcsUrl: 'ws://127.0.0.1:1/ws/bot' } } };
    const abortController = new AbortController();
    let customSkipCalled = false;
    process.env.HOME = dataDir;
    process.env.BCS_IGNORE_CREDENTIALS = '1';
    setBcsRuntime({ config: { async loadConfig() { return cfg; } } } as any);
    const channel = createBcsPlugin({
      shouldSkipConnection() {
        customSkipCalled = true;
        return true;
      },
    });

    try {
      const started = channel.gateway.startAccount({
        cfg,
        accountId: 'default',
        abortSignal: abortController.signal,
        log: { info() {}, warn() {} },
      });
      await new Promise(resolve => setTimeout(resolve, 0));
      assert.equal(customSkipCalled, true);
      abortController.abort();
      await started;
    } finally {
      abortController.abort();
      rmSync(dataDir, { recursive: true, force: true });
    }
  });
});
