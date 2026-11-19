import { rsiScopeKey, useRsiPanelStore } from '@/stores/rsiPanelStore';
const one = { botId: 'bot', ownerId: 'entity-one' };
const two = { botId: 'bot', ownerId: 'entity-two' };
beforeEach(() => {
  useRsiPanelStore.setState({ scope: undefined, attempts: {} });
});
test('Bot scope is independent of chat and separates owners without key collisions', () => {
  expect(rsiScopeKey(one)).not.toEqual(rsiScopeKey(two));
  expect(rsiScopeKey({ botId: 'b:c', ownerId: 'a' })).not.toEqual(rsiScopeKey({ botId: 'c', ownerId: 'a:b' }));
  useRsiPanelStore.getState().open(one);
  expect(useRsiPanelStore.getState().scope).toEqual(one);
  useRsiPanelStore.getState().close();
  expect(useRsiPanelStore.getState().scope).toBeUndefined();
});
test('uncertain submission retains its key and binding across close/reopen and scopes', () => {
  const first = useRsiPanelStore.getState().beginAttempt(one, 'binding-one');
  useRsiPanelStore.getState().close();
  useRsiPanelStore.getState().open(one);
  expect(useRsiPanelStore.getState().beginAttempt(one, 'binding-changed')).toEqual(first);
  expect(useRsiPanelStore.getState().beginAttempt(two, 'binding-two').key).not.toEqual(first.key);
  useRsiPanelStore.getState().confirmAttempt(one, 'wrong-key');
  expect(useRsiPanelStore.getState().attempts[rsiScopeKey(one)]).toEqual(first);
  useRsiPanelStore.getState().confirmAttempt(one, first.key);
  expect(useRsiPanelStore.getState().attempts[rsiScopeKey(one)]).toBeUndefined();
  expect(useRsiPanelStore.getState().attempts[rsiScopeKey(two)]).toBeDefined();
});


test('HTTP browsers without randomUUID can submit and retain a cryptographically random key', () => {
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, 'crypto');
  const source = globalThis.crypto;
  const random = jest.fn((bytes: Uint8Array) => source.getRandomValues(bytes));
  Object.defineProperty(globalThis, 'crypto', { configurable: true, value: { getRandomValues: random } });
  try {
    const first = useRsiPanelStore.getState().beginAttempt(one, 'identity');
    expect(first.key).toMatch(/^rsi-[0-9a-f]{32}$/);
    expect(random).toHaveBeenCalledTimes(1);
    expect(useRsiPanelStore.getState().beginAttempt(one, 'changed')).toEqual(first);
    expect(random).toHaveBeenCalledTimes(1);
    expect(useRsiPanelStore.getState().beginAttempt(two, 'identity').key).not.toEqual(first.key);
  } finally {
    if (descriptor) Object.defineProperty(globalThis, 'crypto', descriptor);
  }
});

test('random source failure creates no pending attempt and never falls back to Math.random', () => {
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, 'crypto');
  const weakRandom = jest.spyOn(Math, 'random');
  Object.defineProperty(globalThis, 'crypto', {
    configurable: true,
    value: { getRandomValues: () => { throw new Error('random source unavailable'); } },
  });
  try {
    let failure: unknown;
    try {
      useRsiPanelStore.getState().beginAttempt(one, 'identity');
    } catch (cause) {
      failure = cause;
    }
    const weakRandomCalls = weakRandom.mock.calls.length;
    expect(failure).toBeInstanceOf(Error);
    expect(useRsiPanelStore.getState().attempts).toEqual({});
    expect(weakRandomCalls).toBe(0);
  } finally {
    weakRandom.mockRestore();
    if (descriptor) Object.defineProperty(globalThis, 'crypto', descriptor);
  }
});
