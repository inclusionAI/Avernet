import { defaultCapabilities } from '@/capabilities/defaultCapabilities';

afterEach(() => {
  Reflect.deleteProperty(globalThis, 'QWENWORK_PROVIDER_SLUG');
});

it('does not enable QwenWork on an unprovisioned deployment', () => {
  expect(defaultCapabilities.getQwenWorkBotRegistrationConfig('http://127.0.0.1:21000')).toEqual({
    status: 'unsupported',
    value: { providerSlug: '', apiUrl: 'http://127.0.0.1:21000' },
  });
});

it('requires explicit deployment opt-in and a BCS endpoint', () => {
  Object.defineProperty(globalThis, 'QWENWORK_PROVIDER_SLUG', { value: ' local ', configurable: true });
  expect(defaultCapabilities.getQwenWorkBotRegistrationConfig('http://127.0.0.1:21000')).toMatchObject({
    status: 'available',
    value: { providerSlug: 'local' },
  });
  expect(defaultCapabilities.getQwenWorkBotRegistrationConfig('').status).toBe('unsupported');
});
