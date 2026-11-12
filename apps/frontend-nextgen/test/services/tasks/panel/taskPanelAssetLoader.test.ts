/** @jest-environment jsdom */
import { beforeEach, expect, it, jest } from '@jest/globals';
let load: () => Promise<any>;
const asset = () => ({ hostApiVersion: 1, assetVersion: '0.1.1', TaskLoopView: () => null, createTaskPanelController: () => ({}) });
const global = window as any;
beforeEach(() => {
  jest.resetModules(); document.head.innerHTML = ''; delete global.taskPanel;
  global.AVERNET_RUNTIME_CONFIG = { TASK_PANEL_ASSET_BASE_URL: 'https://assets.example.test/task-panel/0.1.1' };
  load = require('@/services/tasks/panel/taskPanelAssetLoader').loadTaskPanelAsset;
});
it('deduplicates requests and waits for both fixed-version JS and CSS', async () => {
  const promise = load(); expect(load()).toBe(promise);
  const script = document.querySelector('script')!; const css = document.querySelector('link')!;
  expect(script.src).toContain('https://assets.example.test/task-panel/0.1.1/index.umd.js');
  expect(css.href).toContain('https://assets.example.test/task-panel/0.1.1/task-panel.css');
  let ready = false; void promise.then(() => { ready = true; });
  global.taskPanel = asset(); script.dispatchEvent(new Event('load')); await Promise.resolve(); expect(ready).toBe(false);
  css.dispatchEvent(new Event('load')); await expect(promise).resolves.toBe(global.taskPanel);
});
it('removes failed resources and can retry after a stylesheet failure', async () => {
  const promise = load(); const rejected = promise.catch(error => error);
  document.querySelector('link')!.dispatchEvent(new Event('error')); expect((await rejected).message).toContain('样式加载失败');
  expect(document.querySelectorAll('script, link')).toHaveLength(0);
  const retried = load(); global.taskPanel = asset(); document.querySelector('script')!.dispatchEvent(new Event('load'));
  document.querySelector('link')!.dispatchEvent(new Event('load')); await expect(retried).resolves.toBe(global.taskPanel);
});
it('refuses to overwrite an incompatible runtime global', async () => {
  const previous = { ...asset(), assetVersion: 'other' }; global.taskPanel = previous;
  await expect(load()).rejects.toThrow('不兼容'); expect(global.taskPanel).toBe(previous);
  expect(document.querySelector('script')).toBeNull();
});

it('fails explicitly without deployment configuration, never creating a local mirror fallback', async () => {
  global.AVERNET_RUNTIME_CONFIG = {};
  await expect(load()).rejects.toThrow('未配置任务副屏资产地址');
  expect(document.querySelectorAll('script, link')).toHaveLength(0);
});
it.each(['javascript:alert(1)', 'data:text/javascript,bad', 'https://user:password@assets.example.test/v1', 'https://assets.example.test/v1?redirect=bad'])('rejects an unsafe configured asset address: %s', async value => {
  global.AVERNET_RUNTIME_CONFIG.TASK_PANEL_ASSET_BASE_URL = value;
  await expect(load()).rejects.toThrow('资产地址无效');
  expect(document.querySelectorAll('script, link')).toHaveLength(0);
});
