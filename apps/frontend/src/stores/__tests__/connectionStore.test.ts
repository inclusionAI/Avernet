jest.mock('@/services/backend-api/BotController', () => ({
  ENGINE_TYPE: { OPENCLAW: 'openclaw' },
}));
jest.mock('@/utils/env', () => ({
  getProxypassAbsoluteUrl: (path: string) => `https://example.test${path}`,
  getServers: () => ({ BCN: 'https://bcn.example.test' }),
}));
jest.mock('@/utils/platform', () => ({
  getElectronEnv: () => null,
  isElectron: () => false,
}));

import { selectBcnWebsocketUrl } from '../connectionStore';

const setWindowLocation = (protocol: string, host: string) => {
  Object.defineProperty(globalThis, 'window', {
    configurable: true,
    value: { location: { protocol, host } },
  });
};

describe('selectBcnWebsocketUrl', () => {
  beforeEach(() => {
    (globalThis as { LOCAL_BCN_PROXY?: boolean }).LOCAL_BCN_PROXY = true;
  });

  afterAll(() => {
    delete (globalThis as { window?: Window }).window;
    delete (globalThis as { LOCAL_BCN_PROXY?: boolean }).LOCAL_BCN_PROXY;
  });

  it('uses the frontend tunnel for a local HTTP demo', () => {
    setWindowLocation('http:', '127.0.0.1:8000');

    expect(selectBcnWebsocketUrl()).toBe('ws://127.0.0.1:8000/bcnproxy/ws');
  });

  it('uses secure WebSocket when the local demo page uses HTTPS', () => {
    setWindowLocation('https:', 'demo.example.com:8443');

    expect(selectBcnWebsocketUrl()).toBe(
      'wss://demo.example.com:8443/bcnproxy/ws',
    );
  });

  it('preserves an IPv6 frontend address and a different forwarded port', () => {
    setWindowLocation('http:', '[::1]:18000');

    expect(selectBcnWebsocketUrl()).toBe('ws://[::1]:18000/bcnproxy/ws');
  });

  it('uses the configured BCN server outside the local preset', () => {
    delete (globalThis as { LOCAL_BCN_PROXY?: boolean }).LOCAL_BCN_PROXY;
    setWindowLocation('https:', 'demo.example.com');

    expect(selectBcnWebsocketUrl()).toBe('wss://bcn.example.test/ws');
  });

  it('uses the configured BCN server when the local proxy is disabled', () => {
    (globalThis as { LOCAL_BCN_PROXY?: boolean }).LOCAL_BCN_PROXY = false;
    setWindowLocation('http:', '127.0.0.1:8000');

    expect(selectBcnWebsocketUrl()).toBe('wss://bcn.example.test/ws');
  });
});
