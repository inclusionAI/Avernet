/** Verify glob semantics, not just the presence of a proxy key. */
jest.mock('@umijs/max', () => ({ defineConfig: (config: unknown) => config }), { virtual: true });

// eslint-disable-next-line @typescript-eslint/no-var-requires
const micromatch = require('micromatch');

describe('Evolution development proxy', () => {
  const originalEnv = { ...process.env };

  beforeEach(() => {
    process.env.PRESET = 'local';
    process.env.FRONTEND_API_PROFILE = 'full';
    process.env.TEAMCLAW_GW_BASE = 'https://gateway.example.test';
    delete process.env.EVOLUTION_UPSTREAM;
    jest.resetModules();
  });

  afterEach(() => {
    process.env = { ...originalEnv };
    jest.resetModules();
  });

  const loadProxy = (): Record<string, { target: string }> => {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    return require('../../config/config.local').default.proxy;
  };
  const targetFor = (proxy: Record<string, { target: string }>, path: string) => {
    // Plain proxy keys are prefixes; glob keys use micromatch semantics.
    const key = Object.keys(proxy).find((candidate) =>
      candidate.includes('*') ? micromatch.isMatch(path, candidate) : path.startsWith(candidate),
    );
    return key ? proxy[key].target : undefined;
  };

  test('cannot bypass Gateway even when stale EVOLUTION_UPSTREAM is configured', () => {
    process.env.EVOLUTION_UPSTREAM = 'http://127.0.0.1:8091';
    const proxy = loadProxy();
    for (const path of [
      '/openapi/v1/bots/bot-1/evolution/bindings',
      '/openapi/v1/bots/bot-1/evolution/runs',
      '/openapi/v1/bots/bot-1/evolution/runs/run-1/evidence',
      '/openapi/v1/bots/bot-1/evolution/runs/run-1:cancel',
      '/openapi/v1/bots/bot-1/evolution/strategies',
      '/openapi/v1/evolution/jobs/jobs:claim',
    ]) {
      expect(targetFor(proxy, path)).toBe('https://gateway.example.test');
    }
  });

  test('does not capture Genome, Promotion or other Bot routes', () => {
    process.env.EVOLUTION_UPSTREAM = 'http://127.0.0.1:8091';
    const proxy = loadProxy();
    for (const path of [
      '/openapi/v1/bots/bot-1/genome/revisions',
      '/openapi/v1/bots/bot-1/promotions',
      '/openapi/v1/bots/bot-1/evolution-other/runs',
      '/openapi/v1/bots/bot-1/sessions',
    ]) {
      expect(targetFor(proxy, path)).toBe('https://gateway.example.test');
    }
  });

  test('leaves existing routing unchanged without an Evolution upstream', () => {
    const proxy = loadProxy();
    expect(Object.keys(proxy).some((key) => key.includes('/evolution'))).toBe(false);
    expect(targetFor(proxy, '/openapi/v1/bots/bot-1/evolution/runs')).toBe('https://gateway.example.test');
  });
});
