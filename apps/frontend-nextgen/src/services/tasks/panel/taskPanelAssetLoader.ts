import React from 'react';
import ReactDOM from 'react-dom';
import { backendRequest } from '@/services/backendApi/httpClient';
import type { TaskPanelAsset } from '@avernet-assets/task-panel';
export type { TaskPanelAsset } from '@avernet-assets/task-panel';

let loading: Promise<TaskPanelAsset> | null = null;
// Pin an evaluated script to this page, including after CSS failure. Never replace its global.
let selectedUrl: string | null = null;
let evaluatedAsset: TaskPanelAsset | null = null;

function bundleUrl(value: unknown): string {
  if (typeof value !== 'string' || /[\\\s]/.test(value)) throw new Error('任务副屏 manifest 资源地址无效。');
  const url = new URL(value, window.location.href);
  const sameOriginDev = process.env.NODE_ENV !== 'production' && url.origin === window.location.origin;
  if ((!sameOriginDev && (url.protocol !== 'https:' || !value.startsWith('https://'))) ||
      !['https:', 'http:'].includes(url.protocol) || url.username || url.password || url.search || url.hash ||
      !url.pathname.endsWith('/index.umd.js')) {
    throw new Error('任务副屏 manifest 资源地址无效。');
  }
  return url.href;
}

async function discover(): Promise<string> {
  if (process.env.NODE_ENV === 'development') {
    return new URL('/task-panel/index.umd.js', window.location.href).href;
  }
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), 30_000);
  try {
    const result = await backendRequest<{
      code?: number;
      data?: { schema_version?: number; env?: string; bundles?: { name?: string; url?: string }[] };
    }>('/api/v1/collaboration/tasks/manifest', { method: 'GET', injectUserId: false, signal: controller.signal });
    const data = result?.data;
    if (result?.code !== 200000 || data?.schema_version !== 1 || typeof data.env !== 'string' ||
        !Array.isArray(data.bundles) || data.bundles.length !== 1 || data.bundles[0]?.name !== 'taskPanel') {
      throw new Error('任务副屏 manifest 配置无效，请联系管理员。');
    }
    return bundleUrl(data.bundles[0].url);
  } finally { window.clearTimeout(timer); }
}

/** A single discovery/load per page; failure permits retry, never an implicit version fallback. */
export function loadTaskPanelAsset(): Promise<TaskPanelAsset> {
  if (loading) return loading;
  loading = discover().then(url => new Promise<TaskPanelAsset>((resolve, reject) => {
    const global = window as unknown as { React?: typeof React; ReactDOM?: typeof ReactDOM; taskPanel?: TaskPanelAsset };
    if ((selectedUrl && selectedUrl !== url) || (global.taskPanel && global.taskPanel !== evaluatedAsset)) {
      reject(new Error('任务副屏资产来源已变化或未知，请刷新页面后重试。')); return;
    }
    global.React = React; global.ReactDOM = ReactDOM;
    const compatible = () => {
      const asset = global.taskPanel;
      if (!asset || asset.hostApiVersion !== 1 || typeof asset.assetVersion !== 'string' || !asset.assetVersion ||
          typeof asset.TaskLoopView !== 'function' || typeof asset.createTaskPanelController !== 'function') {
        throw new Error('任务副屏宿主接口不兼容，请刷新后重试。');
      }
      return asset;
    };
    const style = document.createElement('link');
    style.rel = 'stylesheet'; style.href = new URL('task-panel.css', url).href;
    style.dataset.taskPanelSource = url;
    let script: HTMLScriptElement | null = null;
    let styleReady = false;
    let scriptReady = Boolean(evaluatedAsset);
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
    style.onload = () => { styleReady = true; finish(); };
    style.onerror = () => fail(new Error('任务副屏样式加载失败，请重试。'));
    document.head.appendChild(style);
    if (!scriptReady) {
      selectedUrl = url;
      script = document.createElement('script'); script.src = url; script.async = true;
      script.dataset.taskPanelSource = url;
      script.onload = () => {
        evaluatedAsset = global.taskPanel ?? null;
        scriptReady = true; finish();
      };
      script.onerror = () => fail(new Error('任务副屏加载失败，请重试。'));
      document.head.appendChild(script);
    }
    finish();
  })).catch(error => { loading = null; throw error; });
  return loading;
}
