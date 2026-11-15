import { DEFAULT_AGENTSWARM_TENANT, resolveAgentswarmTenant } from '@/services/agentswarm/tenant';

const setInjected = (value: unknown) => {
  (globalThis as Record<string, unknown>).TEAMCLAW_AGENTSWARM_TENANT = value;
};

afterEach(() => setInjected(undefined));

test('the tenant defaults to teamclaw when nothing is injected', () => {
  setInjected(undefined);
  expect(resolveAgentswarmTenant()).toBe('teamclaw');
  expect(DEFAULT_AGENTSWARM_TENANT).toBe('teamclaw');
});

test('an injected tenant wins over the default', () => {
  setInjected('other-co');
  expect(resolveAgentswarmTenant()).toBe('other-co');
});

test('a blank or whitespace-only injection falls back rather than sending an empty tenant', () => {
  setInjected('   ');
  expect(resolveAgentswarmTenant()).toBe('teamclaw');
  setInjected('');
  expect(resolveAgentswarmTenant()).toBe('teamclaw');
});

test('a non-string injection is ignored', () => {
  setInjected(42);
  expect(resolveAgentswarmTenant()).toBe('teamclaw');
});

test('the resolved tenant satisfies the gateway minimum length, so no request is refused for it', () => {
  setInjected(undefined);
  expect(resolveAgentswarmTenant().length).toBeLessThanOrEqual(64);
  expect(resolveAgentswarmTenant().length).toBeGreaterThanOrEqual(1);
});