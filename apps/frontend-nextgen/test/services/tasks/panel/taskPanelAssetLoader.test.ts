/** @jest-environment jsdom */
import { afterEach, beforeEach, expect, it, jest } from '@jest/globals';
jest.mock('@/services/backendApi/httpClient', () => ({ backendRequest: jest.fn(), BackendRequestError: jest.requireActual<typeof import('@/services/backendApi/httpClient')>('@/services/backendApi/httpClient').BackendRequestError }));
let load: () => Promise<any>;
let request: jest.Mock;
const global = window as any;
const url = 'https://assets.example.test/task-panel/0.2.0/index.umd.js';
const manifest = (value = url) => ({ code: 200000, data: {
  schema_version: 1, env: 'pre', bundles: [{ name: 'taskPanel', url: value }],
} });
const asset = () => ({ hostApiVersion: 1, assetVersion: '0.2.0', TaskLoopView: () => null, createTaskPanelController: () => ({}) });
const tick = async () => { for (let i = 0; i < 30; i++) await Promise.resolve(); };
async function ready() {
  const promise = load(); await tick();
  if (!document.querySelector('script')) {
    document.querySelector('link')!.dispatchEvent(new Event('load')); await tick();
  }
  global.taskPanel = asset(); document.querySelector('script')!.dispatchEvent(new Event('load'));
  document.querySelector('link')!.dispatchEvent(new Event('load'));
  return promise;
}
const originalFetch = global.fetch;
afterEach(() => { global.fetch = originalFetch; });
beforeEach(() => {
  global.fetch = jest.fn(async (path: string) => ({ ok: true, json: async () => ({ version: '9.8.7' }), headers: { get: () => path.endsWith('.css') ? 'text/css' : 'application/javascript' } }));
  jest.resetModules(); document.head.innerHTML = ''; delete global.taskPanel;
  request = require('@/services/backendApi/httpClient').backendRequest;
  request.mockResolvedValue(manifest() as never);
  load = require('@/services/tasks/panel/taskPanelAssetLoader').loadTaskPanelAsset;
});
it('discovers once, deduplicates loads and accepts a compatible independently released version', async () => {
  const promise = load(); expect(load()).toBe(promise); await tick();
  expect(request).toHaveBeenCalledWith('/api/v1/collaboration/tasks/manifest', expect.objectContaining({ method: 'GET', injectUserId: false, signal: expect.anything() }));
  expect(request).toHaveBeenCalledTimes(1);
  const script = document.querySelector('script')!; const css = document.querySelector('link')!;
  expect(script.src).toBe(url); expect(css.href).toBe(new URL('task-panel.css', url).href);
  let resolved = false; void promise.then(() => { resolved = true; });
  global.taskPanel = asset(); script.dispatchEvent(new Event('load')); await tick(); expect(resolved).toBe(false);
  css.dispatchEvent(new Event('load')); await expect(promise).resolves.toBe(global.taskPanel);
  expect(load()).toBe(promise); expect(request).toHaveBeenCalledTimes(1);
});
it('reports discovery failure without fallback assets, then refetches on retry', async () => {
  request.mockRejectedValueOnce(new Error('manifest unavailable') as never);
  await expect(load()).rejects.toThrow('manifest unavailable');
  expect(document.querySelectorAll('script, link')).toHaveLength(0);
  await ready(); expect(request).toHaveBeenCalledTimes(2);
});
it.each([{}, { code: 200000 }, { code: 200000, data: { schema_version: 2, env: 'pre', bundles: [] } },
  { code: 200000, data: { ...manifest().data, bundles: [] } },
  { code: 200000, data: { ...manifest().data, bundles: [...manifest().data.bundles, ...manifest().data.bundles] } },
  { code: 200000, data: { ...manifest().data, bundles: [{ name: 'bcsPanel', url }] } },
])('rejects an invalid manifest without injecting resources: %j', async value => {
  request.mockResolvedValue(value as never);
  await expect(load()).rejects.toThrow('manifest 配置无效');
  expect(document.querySelectorAll('script, link')).toHaveLength(0);
});
it.each(['javascript:alert(1)', 'data:text/javascript,bad', 'http://evil.test/index.umd.js',
  'https://user:password@assets.example.test/index.umd.js', `${url}?redirect=bad`, `${url}#bad`,
  'https://assets.example.test/wrong.js', 'https://assets.example.test/white space/index.umd.js',
])('rejects an unsafe manifest URL: %s', async value => {
  request.mockResolvedValue(manifest(value) as never);
  await expect(load()).rejects.toThrow('资源地址无效');
  expect(document.querySelectorAll('script, link')).toHaveLength(0);
});
it('uses the same manifest path for local same-origin unpublished assets', async () => {
  request.mockResolvedValue(manifest('/task-panel/0.2.0/index.umd.js') as never);
  await ready(); expect(document.querySelector('script')!.src).toBe(new URL('/task-panel/0.2.0/index.umd.js', window.location.href).href);
});
it('rejects relative local discovery URLs in a production build', async () => {
  const previous = process.env.NODE_ENV;
  try {
    process.env.NODE_ENV = 'production'; request.mockResolvedValue(manifest('/task-panel/0.2.0/index.umd.js') as never);
    await expect(load()).rejects.toThrow('资源地址无效');
  } finally { process.env.NODE_ENV = previous; }
});
it('refuses an unknown pre-existing global even if its API looks compatible', async () => {
  const previous = asset(); global.taskPanel = previous;
  await expect(load()).rejects.toThrow('来源'); expect(global.taskPanel).toBe(previous);
  expect(document.querySelector('script')).toBeNull();
});
it('rejects an unsupported host API rather than requiring a pinned package version', async () => {
  const promise = load(); const failure = promise.catch(error => error); await tick();
  global.taskPanel = { ...asset(), hostApiVersion: 2 };
  document.querySelector('script')!.dispatchEvent(new Event('load'));
  document.querySelector('link')!.dispatchEvent(new Event('load'));
  expect((await failure).message).toContain('不兼容');
});
it('retries CSS without evaluating an already loaded script again', async () => {
  const promise = load(); const failure = promise.catch(error => error); await tick();
  global.taskPanel = asset(); document.querySelector('script')!.dispatchEvent(new Event('load'));
  document.querySelector('link')!.dispatchEvent(new Event('error'));
  expect((await failure).message).toContain('样式加载失败');
  const retried = load(); await tick();
  expect(document.querySelector('script')).toBeNull();
  document.querySelector('link')!.dispatchEvent(new Event('load'));
  await expect(retried).resolves.toBe(global.taskPanel);
});
it('does not hot-swap an evaluated asset when discovery changes after a failed load', async () => {
  const promise = load(); const failure = promise.catch(error => error); await tick();
  global.taskPanel = asset(); document.querySelector('script')!.dispatchEvent(new Event('load'));
  document.querySelector('link')!.dispatchEvent(new Event('error')); await failure;
  const previous = global.taskPanel;
  request.mockResolvedValue(manifest('https://assets.example.test/task-panel/0.3.0/index.umd.js') as never);
  await expect(load()).rejects.toThrow('刷新页面'); expect(global.taskPanel).toBe(previous);
  expect(document.querySelectorAll('script, link')).toHaveLength(0);
});
it('removes failed script resources and retries the original URL', async () => {
  const promise = load(); const failure = promise.catch(error => error); await tick();
  document.querySelector('script')!.dispatchEvent(new Event('error'));
  expect((await failure).message).toContain('加载失败');
  expect(document.querySelectorAll('script, link')).toHaveLength(0); await ready();
});

it('loads local unpublished assets in development without calling the backend manifest', async () => {
  const previous = process.env.NODE_ENV;
  try {
    process.env.NODE_ENV = 'development';
    await ready();
    expect(request).not.toHaveBeenCalled();
    expect(document.querySelector('script')!.src).toBe(new URL('/task-panel/index.umd.js', window.location.href).href);
    expect(document.querySelector('link')!.href).toBe(new URL('/task-panel/task-panel.css', window.location.href).href);
  } finally { process.env.NODE_ENV = previous; }
});

const developmentFallback = 'https://gw.alipayobjects.com/render/p/hitu_npm/@avernet-assets/task-panel/9.8.7/dist/index.umd.js';
it.each(['offline', 'html', 'css', 'http'])('uses the complete remote release when local assets are unavailable: %s', async failure => {
  const previous = process.env.NODE_ENV;
  try {
    process.env.NODE_ENV = 'development';
    global.fetch = jest.fn(async (path: string) => {
      if (new URL(path, window.location.href).origin === 'https://registry.npmjs.org') return { ok: true, json: async () => ({ version: '9.8.7' }) };
      if (failure === 'offline') throw new TypeError('Failed to fetch');
      return { ok: failure !== 'http', headers: { get: () => failure === 'html' || (failure === 'css' && path.endsWith('.css')) ? 'text/html' : 'application/javascript' } };
    });
    await ready();
    expect(document.querySelector('script')!.src).toBe(developmentFallback);
    expect(document.querySelector('link')!.href).toBe(new URL('task-panel.css', developmentFallback).href);
    expect(request).not.toHaveBeenCalled();
  } finally { process.env.NODE_ENV = previous; }
});
it('keeps the evaluated development release when local resources later disappear', async () => {
  const previous = process.env.NODE_ENV;
  try {
    process.env.NODE_ENV = 'development';
    const loaded = await ready();
    global.fetch = jest.fn(async () => { throw new TypeError('offline'); });
    await expect(load()).resolves.toBe(loaded);
    expect(document.querySelectorAll('script')).toHaveLength(1);
    expect(document.querySelector('script')!.src).toBe(new URL('/task-panel/index.umd.js', window.location.href).href);
    expect(global.fetch).not.toHaveBeenCalled();
  } finally { process.env.NODE_ENV = previous; }
});

it('falls back after the local probe times out', async () => {
  const previous = process.env.NODE_ENV;
  jest.useFakeTimers();
  try {
    process.env.NODE_ENV = 'development';
    global.fetch = jest.fn((path: string, options: RequestInit) => new URL(path, window.location.href).origin === 'https://registry.npmjs.org' ? Promise.resolve({ ok: true, json: async () => ({ version: '9.8.7' }) }) : new Promise((_resolve, reject) => {
      options.signal!.addEventListener('abort', () => reject(new Error('aborted')), { once: true });
    }));
    const pending = load(); await tick();
    await jest.advanceTimersByTimeAsync(3_000); await tick();
    expect(document.querySelector('script')!.src).toBe(developmentFallback);
    global.taskPanel = asset(); document.querySelector('script')!.dispatchEvent(new Event('load'));
    document.querySelector('link')!.dispatchEvent(new Event('load'));
    await expect(pending).resolves.toBe(global.taskPanel);
  } finally { process.env.NODE_ENV = previous; jest.useRealTimers(); }
});

it.each(['error', 'timeout'])('falls back as a complete release when CSS HEAD succeeds but its stylesheet load fails: %s', async failure => {
  const previous = process.env.NODE_ENV;
  jest.useFakeTimers();
  try {
    process.env.NODE_ENV = 'development';
    const pending = load(); await tick();
    const localStyle = document.querySelector('link')!;
    expect(localStyle.href).toBe(new URL('/task-panel/task-panel.css', window.location.href).href);
    expect(document.querySelector('script')).toBeNull();
    if (failure === 'error') localStyle.dispatchEvent(new Event('error'));
    else await jest.advanceTimersByTimeAsync(3_000);
    await tick();
    expect(localStyle.isConnected).toBe(false);
    expect(document.querySelector('script')!.src).toBe(developmentFallback);
    expect(document.querySelectorAll('link')).toHaveLength(1);
    expect(document.querySelector('link')!.href).toBe(new URL('task-panel.css', developmentFallback).href);
    global.taskPanel = asset(); document.querySelector('script')!.dispatchEvent(new Event('load'));
    document.querySelector('link')!.dispatchEvent(new Event('load'));
    await expect(pending).resolves.toBe(global.taskPanel);
  } finally { process.env.NODE_ENV = previous; jest.useRealTimers(); }
});
it('reuses the successfully loaded local stylesheet without issuing a second stylesheet load', async () => {
  const previous = process.env.NODE_ENV;
  try {
    process.env.NODE_ENV = 'development';
    const pending = load(); await tick();
    const localStyle = document.querySelector('link')!;
    expect(document.querySelector('script')).toBeNull();
    localStyle.dispatchEvent(new Event('load')); await tick();
    expect(document.querySelector('link')).toBe(localStyle);
    expect(localStyle.media).toBe('all');
    expect(document.querySelectorAll('link')).toHaveLength(1);
    expect(document.querySelector('script')!.src).toBe(new URL('/task-panel/index.umd.js', window.location.href).href);
    global.taskPanel = asset(); document.querySelector('script')!.dispatchEvent(new Event('load'));
    await expect(pending).resolves.toBe(global.taskPanel);
  } finally { process.env.NODE_ENV = previous; }
});
