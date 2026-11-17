/** @jest-environment jsdom */
import { afterEach, beforeEach, expect, it, jest } from '@jest/globals';
jest.mock('@/services/backendApi/httpClient', () => ({ backendRequest: jest.fn() }));
const originalFetch = global.fetch;
const originalEnv = process.env.NODE_ENV;
const registry = 'https://registry.npmjs.org/@avernet-assets%2Ftask-panel/latest';
let discover: typeof import('@/services/tasks/panel/taskPanelAssetSource').discoverTaskPanelUrl;
beforeEach(() => {
  jest.resetModules();
  process.env.NODE_ENV = 'development';
  discover = require('@/services/tasks/panel/taskPanelAssetSource').discoverTaskPanelUrl;
});
afterEach(() => {
  global.fetch = originalFetch;
  process.env.NODE_ENV = originalEnv;
  jest.useRealTimers();
});
function metadata(response: unknown) {
  global.fetch = jest.fn(async (url: string) => {
    if (url !== registry) throw new TypeError('local assets unavailable');
    return response;
  }) as unknown as typeof fetch;
}
it('resolves latest once and pins all consumers to the same immutable version', async () => {
  metadata({ ok: true, json: async () => ({ version: '9.8.7' }) });
  const results = await Promise.all([discover(), discover()]);
  expect(results).toEqual(Array(2).fill('https://gw.alipayobjects.com/render/p/hitu_npm/@avernet-assets/task-panel/9.8.7/dist/index.umd.js'));
  await expect(discover()).resolves.toBe(results[0]);
  expect((global.fetch as jest.Mock).mock.calls.filter(([url]: unknown[]) => url === registry)).toHaveLength(1);
  expect(global.fetch).toHaveBeenCalledWith(registry, expect.objectContaining({ credentials: 'omit', cache: 'no-store', signal: expect.anything() }));
});
it.each([undefined, '', 'latest', '1.0.3/bad', '1.0.3-beta.1', 103])('rejects invalid release metadata (%s) without silently using an old version', async version => {
  metadata({ ok: true, json: async () => ({ version }) });
  await expect(discover()).rejects.toThrow('最新发布版本无效');
});
it('allows retry after registry failure', async () => {
  metadata({ ok: false });
  await expect(discover()).rejects.toThrow('无法查询');
  metadata({ ok: true, json: async () => ({ version: '9.8.8' }) });
  await expect(discover()).resolves.toContain('/9.8.8/dist/index.umd.js');
});
it('aborts a stalled registry query', async () => {
  jest.useFakeTimers();
  global.fetch = jest.fn(async (url: string, options: RequestInit) => {
    if (url !== registry) throw new TypeError('local assets unavailable');
    return new Promise((_resolve, reject) => { options.signal!.addEventListener('abort', () => reject(new Error('registry timeout')), { once: true }); });
  }) as unknown as typeof fetch;
  const failure = discover().catch(error => error);
  for (let tick = 0; tick < 20; tick++) await Promise.resolve();
  await jest.advanceTimersByTimeAsync(10_000);
  expect((await failure).message).toBe('registry timeout');
});
