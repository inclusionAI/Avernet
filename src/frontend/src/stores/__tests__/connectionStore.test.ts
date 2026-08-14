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

const setWindowLocation = (protocol: string, hostname: string) => {
  Object.defineProperty(globalThis, 'window', {
    configurable: true,
    value: { location: { protocol, hostname } },
  });
};

describe('selectBcnWebsocketUrl', () => {
  beforeEach(() => {
    (globalThis as { LOCAL_BCN_PORT?: string }).LOCAL_BCN_PORT = '21000';
  });

  afterAll(() => {
    delete (globalThis as { window?: Window }).window;
    delete (globalThis as { LOCAL_BCN_PORT?: string }).LOCAL_BCN_PORT;
  });

  it('uses the page hostname and configured BCS port for a local HTTP demo', () => {
    setWindowLocation('http:', 'demo.example.com');

    expect(selectBcnWebsocketUrl()).toBe('ws://demo.example.com:21000/ws');
  });

  it('uses secure WebSocket when the local demo page uses HTTPS', () => {
    setWindowLocation('https:', 'demo.example.com');

    expect(selectBcnWebsocketUrl()).toBe('wss://demo.example.com:21000/ws');
  });

  it('uses the configured BCN server outside the local preset', () => {
    delete (globalThis as { LOCAL_BCN_PORT?: string }).LOCAL_BCN_PORT;
    setWindowLocation('https:', 'demo.example.com');

    expect(selectBcnWebsocketUrl()).toBe('wss://bcn.example.test/ws');
  });
});
