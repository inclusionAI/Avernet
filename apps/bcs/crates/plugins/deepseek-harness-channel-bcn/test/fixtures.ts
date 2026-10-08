import type { Config } from '../src/config.js';

export function testConfig(overrides: Partial<Config> = {}): Config {
  return {
    enabled: true,
    endpoint: 'http://127.0.0.1:9000/',
    botName: 'DSH Test Bot',
    summary: 'Test Bot',
    domains: ['testing'],
    skills: ['verification'],
    scopes: [],
    onboardingTokenRef: 'BCN_ONBOARDING_TOKEN',
    botSessionRef: 'BCN_BOT_SESSION',
    connectionTimeoutMs: 2_000,
    heartbeatIntervalMs: 10_000,
    reconnectInitialMs: 100,
    reconnectMaxMs: 1_000,
    ...overrides,
  };
}

export async function waitFor(
  predicate: () => boolean,
  message: string,
  timeoutMs = 3_000,
): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (predicate()) return;
    await new Promise(resolve => setTimeout(resolve, 10));
  }
  throw new Error(message);
}
