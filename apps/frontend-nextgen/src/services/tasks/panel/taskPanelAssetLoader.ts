import React from 'react';
import ReactDOM from 'react-dom';
import type { TaskPanelAsset } from '@avernet-assets/task-panel';
export type { TaskPanelAsset } from '@avernet-assets/task-panel';
export const TASK_PANEL_ASSET_VERSION = '0.1.1';
declare const TASK_PANEL_ASSET_BASE_URL: string | undefined;
/** Deployment-owned directory URL, analogous to a workflow manifest's pinned CDN entry. */
export function resolveTaskPanelAssetBaseUrl(): string {
  const runtime = (window as unknown as { AVERNET_RUNTIME_CONFIG?: { TASK_PANEL_ASSET_BASE_URL?: string } }).AVERNET_RUNTIME_CONFIG;
  const value = (runtime?.TASK_PANEL_ASSET_BASE_URL || (typeof TASK_PANEL_ASSET_BASE_URL === 'string' ? TASK_PANEL_ASSET_BASE_URL : '')).trim();
  if (!value) throw new Error('未配置任务副屏资产地址，请配置 TASK_PANEL_ASSET_BASE_URL。');
  const url = new URL(value, window.location.href);
  const local = ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname);
  if (url.username || url.password || url.search || url.hash ||
    (url.protocol !== 'https:' && !(url.protocol === 'http:' && (local || url.origin === window.location.origin)))) {
    throw new Error('任务副屏资产地址无效：仅接受可信 HTTPS、同源或本地开发目录地址。');
  }
  return url.href.replace(/\/+$/, '');
}
let loading: Promise<TaskPanelAsset> | null = null;
/** Platform-owned asset only: never take script URLs from messages or Bot CDN config. */
export function loadTaskPanelAsset(): Promise<TaskPanelAsset> {
  if (loading) return loading;
  loading = new Promise<TaskPanelAsset>((resolve, reject) => {
    const root = resolveTaskPanelAssetBaseUrl();
    const global = window as unknown as { React?: typeof React; ReactDOM?: typeof ReactDOM; taskPanel?: TaskPanelAsset };
    global.React = React; global.ReactDOM = ReactDOM;
    const compatible = () => {
      const asset = global.taskPanel;
      if (!asset || asset.hostApiVersion !== 1 || asset.assetVersion !== TASK_PANEL_ASSET_VERSION ||
        typeof asset.TaskLoopView !== 'function' || typeof asset.createTaskPanelController !== 'function') {
        throw new Error('任务副屏资产版本或宿主接口不兼容，请刷新后重试。');
      }
      return asset;
    };
    // Never overwrite a different runtime's global, even during retries.
    if (global.taskPanel) { try { compatible(); } catch (error) { reject(error); return; } }
    const selector = `link[data-task-panel-version="${TASK_PANEL_ASSET_VERSION}"]`;
    const existingStyle = document.querySelector<HTMLLinkElement>(selector);
    const style = existingStyle ?? document.createElement('link');
    let script: HTMLScriptElement | null = null;
    let styleReady = style.dataset.loaded === 'true';
    let scriptReady = Boolean(global.taskPanel);
    let settled = false;
    let timer: number;
    const cleanup = () => { window.clearTimeout(timer); style.onload = null; style.onerror = null; if (script) { script.onload = null; script.onerror = null; } };
    const fail = (error: Error) => {
      if (settled) return; settled = true; cleanup(); script?.remove(); style.remove(); reject(error);
    };
    const finish = () => {
      if (settled || !styleReady || !scriptReady) return;
      try { const asset = compatible(); settled = true; cleanup(); resolve(asset); } catch (error) { fail(error as Error); }
    };
    timer = window.setTimeout(() => fail(new Error('任务副屏加载超时，请重试。')), 30_000);
    style.onload = () => { styleReady = true; style.dataset.loaded = 'true'; finish(); };
    style.onerror = () => fail(new Error('任务副屏样式加载失败，请重试。'));
    if (!existingStyle) {
      style.rel = 'stylesheet'; style.href = `${root}/task-panel.css`; style.dataset.taskPanelVersion = TASK_PANEL_ASSET_VERSION;
      document.head.appendChild(style);
    }
    if (!scriptReady) {
      script = document.createElement('script'); script.src = `${root}/index.umd.js`; script.async = true;
      script.onload = () => { scriptReady = true; finish(); };
      script.onerror = () => fail(new Error('任务副屏加载失败，请重试。'));
      document.head.appendChild(script);
    }
    finish();
  }).catch(error => { loading = null; throw error; });
  return loading;
}
