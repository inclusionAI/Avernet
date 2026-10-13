/** @jest-environment jsdom */
import { defaultCapabilities } from '@/capabilities/defaultCapabilities';

afterEach(() => {
  Reflect.deleteProperty(globalThis, 'QWENWORK_PROVIDER_SLUG');
  Reflect.deleteProperty(window, 'AVERNET_RUNTIME_CONFIG');
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

it('uses runtime provider configuration and allows runtime disable of a baked-in default', () => {
  Object.defineProperty(globalThis, 'QWENWORK_PROVIDER_SLUG', { value: 'build-provider', configurable: true });
  Object.defineProperty(window, 'AVERNET_RUNTIME_CONFIG', {
    value: { QWENWORK_PROVIDER_SLUG: 'runtime-provider' },
    configurable: true,
  });
  expect(defaultCapabilities.getQwenWorkBotRegistrationConfig('https://example.com').value.providerSlug).toBe(
    'runtime-provider',
  );
  Object.defineProperty(window, 'AVERNET_RUNTIME_CONFIG', {
    value: { QWENWORK_PROVIDER_SLUG: '' },
    configurable: true,
  });
  expect(defaultCapabilities.getQwenWorkBotRegistrationConfig('https://example.com').status).toBe('unsupported');
});
