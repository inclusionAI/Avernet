import { strict as assert } from 'node:assert';
import { createBotDataDirInjector, injectBotDataDir, resolveBotDataDir } from '../src/bot-data-dir.js';

describe('bot data directory environment injection', () => {
  let originalBotDataDir: string | undefined;
  let originalOpenClawDataDir: string | undefined;

  beforeEach(() => {
    originalBotDataDir = process.env.BOT_DATA_DIR;
    originalOpenClawDataDir = process.env.OPENCLAW_DATA_DIR;
    delete process.env.BOT_DATA_DIR;
    delete process.env.OPENCLAW_DATA_DIR;
  });

  afterEach(() => {
    if (originalBotDataDir === undefined) delete process.env.BOT_DATA_DIR;
    else process.env.BOT_DATA_DIR = originalBotDataDir;
    if (originalOpenClawDataDir === undefined) delete process.env.OPENCLAW_DATA_DIR;
    else process.env.OPENCLAW_DATA_DIR = originalOpenClawDataDir;
  });

  it('keeps an existing BOT_DATA_DIR value', async () => {
    process.env.BOT_DATA_DIR = '/tmp/existing-bot-data';

    assert.equal(await injectBotDataDir(), '/tmp/existing-bot-data');
    assert.equal(process.env.BOT_DATA_DIR, '/tmp/existing-bot-data');
  });

  it('resolves BOT_DATA_DIR from the runtime session store path', async () => {
    const runtime = {
      config: {
        async loadConfig() {
          return { session: { store: { type: 'json' } } };
        },
      },
      channel: {
        session: {
          resolveStorePath(_store: unknown, opts: { agentId: string }) {
            assert.deepEqual(opts, { agentId: 'main' });
            return '/tmp/openclaw-profile/agents/main/sessions/sessions.json';
          },
        },
      },
    };

    assert.equal(await resolveBotDataDir(runtime), '/tmp/openclaw-profile');
  });

  it('falls back to OPENCLAW_DATA_DIR', async () => {
    process.env.OPENCLAW_DATA_DIR = '/tmp/openclaw-env-data';
    assert.equal(await resolveBotDataDir(), '/tmp/openclaw-env-data');
  });

  it('injects BOT_DATA_DIR only once per injector', async () => {
    process.env.OPENCLAW_DATA_DIR = '/tmp/openclaw-once';
    const injectOnce = createBotDataDirInjector();

    assert.equal(await injectOnce(), '/tmp/openclaw-once');
    delete process.env.BOT_DATA_DIR;
    assert.equal(await injectOnce(), undefined);
  });
});
