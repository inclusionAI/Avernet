/** @jest-environment jsdom */
import { defaultCapabilities } from '@/capabilities/defaultCapabilities';

afterEach(() => {
  Reflect.deleteProperty(window, 'AVERNET_RUNTIME_CONFIG');
});

it.each([undefined, '', '   ', 'javascript:alert(1)', 'invalid', 'https://user:password@example.com/guide'])(
  'omits missing or unsafe deployment guide links: %s',
  (url) => {
    Object.defineProperty(window, 'AVERNET_RUNTIME_CONFIG', {
      value: { DSH_REGISTRATION_GUIDE_URL: url },
      configurable: true,
    });
    expect(defaultCapabilities.getDeepSeekHarnessRegistrationGuideUrl().value).toBeNull();
  },
);

it('uses the deployment guide revision instead of a baked-in mutable branch', () => {
  const url = 'https://docs.example.com/releases/0.2.0/dsh';
  Object.defineProperty(window, 'AVERNET_RUNTIME_CONFIG', {
    value: { DSH_REGISTRATION_GUIDE_URL: ` ${url} ` },
    configurable: true,
  });
  expect(defaultCapabilities.getDeepSeekHarnessRegistrationGuideUrl().value).toBe(url);
});
