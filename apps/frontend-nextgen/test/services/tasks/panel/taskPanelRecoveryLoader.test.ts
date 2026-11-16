/** @jest-environment jsdom */
import { afterEach, beforeEach, expect, it, jest } from '@jest/globals';
jest.mock('@/services/backendApi/httpClient', () => ({ backendRequest: jest.fn(), BackendRequestError: jest.requireActual<typeof import('@/services/backendApi/httpClient')>('@/services/backendApi/httpClient').BackendRequestError }));
const url = 'https://assets.example.test/task-panel/1.0.2/index.umd.js';
const global = window as any;
const asset = () => ({ hostApiVersion: 1, assetVersion: '1.0.2', createTaskPanelController: jest.fn() });
const tick = async () => { for (let i = 0; i < 30; i++) await Promise.resolve(); };
let load: () => Promise<any>;
let loadUI: () => Promise<any>;
let request: jest.Mock;
const originalFetch = global.fetch;
afterEach(() => { global.fetch = originalFetch; });
beforeEach(() => {
  jest.resetModules(); document.head.innerHTML = '';
  delete global.taskPanel; delete global.taskPanelRecovery; delete global.React; delete global.ReactDOM;
  request = require('@/services/backendApi/httpClient').backendRequest;
  request.mockResolvedValue({ code: 200000, data: { schema_version: 1, env: 'pre', bundles: [{ name: 'taskPanel', url }] } } as never);
  load = require('@/services/tasks/panel/taskPanelRecoveryLoader').loadTaskPanelRecoveryAsset;
  loadUI = require('@/services/tasks/panel/taskPanelAssetLoader').loadTaskPanelAsset;
});
it('普通会话只加载恢复入口，不注入完整 UI、React 或 CSS，且并发探测共用加载', async () => {
  const pending = load(); expect(load()).toBe(pending); await tick();
  expect(document.querySelectorAll('script')).toHaveLength(1);
  expect(document.querySelector('script')!.src).toBe(new URL('recovery.umd.js', url).href);
  expect(document.querySelector('link')).toBeNull();
  expect(global.React).toBeUndefined(); expect(global.ReactDOM).toBeUndefined();
  global.taskPanelRecovery = asset(); document.querySelector('script')!.dispatchEvent(new Event('load'));
  await expect(pending).resolves.toBe(global.taskPanelRecovery);
  expect(load()).toBe(pending);
});
it('发现任务后 UI 才按需加载，且必须与恢复入口来自同一发布目录', async () => {
  const pending = load(); await tick();
  global.taskPanelRecovery = asset(); document.querySelector('script')!.dispatchEvent(new Event('load')); await pending;
  const ui = loadUI(); await tick();
  const script = document.querySelector<HTMLScriptElement>(`script[src="${url}"]`)!;
  expect(script).not.toBeNull(); expect(document.querySelector('link')!.href).toBe(new URL('task-panel.css', url).href);
  global.taskPanel = { ...asset(), TaskLoopView: () => null };
  script.dispatchEvent(new Event('load')); document.querySelector('link')!.dispatchEvent(new Event('load'));
  await expect(ui).resolves.toBe(global.taskPanel);
});
it('恢复入口失败可重试，不隐式回退加载完整副屏', async () => {
  const pending = load(); const failed = pending.catch(error => error); await tick();
  document.querySelector('script')!.dispatchEvent(new Event('error'));
  expect((await failed).message).toContain('恢复加载失败');
  expect(document.querySelectorAll('script, link')).toHaveLength(0);
  const retry = load(); await tick();
  global.taskPanelRecovery = asset(); document.querySelector('script')!.dispatchEvent(new Event('load'));
  await expect(retry).resolves.toBe(global.taskPanelRecovery);
});
it('manifest 在恢复和 UI 加载之间变更时拒绝混用版本', async () => {
  const pending = load(); await tick();
  global.taskPanelRecovery = asset(); document.querySelector('script')!.dispatchEvent(new Event('load')); await pending;
  request.mockResolvedValue({ code: 200000, data: { schema_version: 1, env: 'pre', bundles: [{ name: 'taskPanel', url: url.replace('1.0.2', '1.0.3') }] } } as never);
  await expect(loadUI()).rejects.toThrow('来源已变化');
  expect(document.querySelector('link')).toBeNull(); expect(global.taskPanel).toBeUndefined();
});

it('同目录返回的 UI 与恢复入口版本不一致时也拒绝混用', async () => {
  const pending = load(); await tick();
  global.taskPanelRecovery = asset(); document.querySelector('script')!.dispatchEvent(new Event('load')); await pending;
  const ui = loadUI(); const failed = ui.catch(error => error); await tick();
  const script = document.querySelector<HTMLScriptElement>(`script[src="${url}"]`)!;
  global.taskPanel = { ...asset(), assetVersion: '1.0.3', TaskLoopView: () => null };
  script.dispatchEvent(new Event('load')); document.querySelector('link')!.dispatchEvent(new Event('load'));
  expect((await failed).message).toContain('版本不一致');
});

it.each(['offline', 'missing-recovery', 'css-load-error'])('开发模式本地不可用时以远端完整 UMD 恢复，UI 复用同一资产：%s', async failure => {
  const previous = process.env.NODE_ENV;
  try {
    process.env.NODE_ENV = 'development';
    global.fetch = jest.fn(async (path: string) => {
      if (failure === 'offline') throw new TypeError('offline');
      return { ok: failure !== 'missing-recovery' || !path.endsWith('recovery.umd.js'), headers: { get: () => path.endsWith('.css') ? 'text/css' : 'application/javascript' } };
    });
    const pending = load(); await tick();
    expect(document.querySelectorAll('script[src$="recovery.umd.js"]')).toHaveLength(0);
    if (failure === 'css-load-error') {
      document.querySelector('link')!.dispatchEvent(new Event('error')); await tick();
    }
    const remote = 'https://gw.alipayobjects.com/render/p/hitu_npm/@avernet-assets/task-panel/1.0.1/dist/index.umd.js';
    expect(document.querySelectorAll('script')).toHaveLength(1);
    expect(document.querySelector('script')!.src).toBe(remote);
    expect(document.querySelector('link')!.href).toBe(new URL('task-panel.css', remote).href);
    global.taskPanel = { ...asset(), assetVersion: '1.0.1', TaskLoopView: () => null };
    document.querySelector('script')!.dispatchEvent(new Event('load'));
    document.querySelector('link')!.dispatchEvent(new Event('load'));
    await expect(pending).resolves.toBe(global.taskPanel);
    await expect(loadUI()).resolves.toBe(global.taskPanel);
    expect(document.querySelectorAll('script')).toHaveLength(1);
    expect(global.taskPanelRecovery).toBeUndefined();
    expect(request).not.toHaveBeenCalled();
  } finally { process.env.NODE_ENV = previous; }
});

it('开发模式本地资源完整时仍只执行轻量恢复入口', async () => {
  const previous = process.env.NODE_ENV;
  try {
    process.env.NODE_ENV = 'development';
    global.fetch = jest.fn(async (path: string) => ({ ok: true, headers: { get: () => path.endsWith('.css') ? 'text/css' : 'application/javascript' } }));
    const pending = load(); await tick();
    const style = document.querySelector('link')!;
    expect(style.media).toBe('not all');
    expect(document.querySelector('script')).toBeNull();
    style.dispatchEvent(new Event('load')); await tick();
    expect(document.querySelector('script')!.src).toBe(new URL('/task-panel/recovery.umd.js', window.location.href).href);
    expect(document.querySelector('link')).toBe(style);
    expect(style.media).toBe('not all');
    expect(global.React).toBeUndefined(); expect(global.ReactDOM).toBeUndefined();
    global.taskPanelRecovery = asset(); document.querySelector('script')!.dispatchEvent(new Event('load'));
    await expect(pending).resolves.toBe(global.taskPanelRecovery);
    expect(global.fetch).toHaveBeenCalledTimes(3);
    const ui = loadUI(); await tick();
    expect(document.querySelectorAll('link')).toHaveLength(1);
    expect(document.querySelector('link')).toBe(style);
    expect(style.media).toBe('all');
    global.taskPanel = { ...asset(), TaskLoopView: () => null };
    document.querySelector<HTMLScriptElement>('script[src$="index.umd.js"]')!.dispatchEvent(new Event('load'));
    await expect(ui).resolves.toBe(global.taskPanel);
    expect(request).not.toHaveBeenCalled();
  } finally { process.env.NODE_ENV = previous; }
});
