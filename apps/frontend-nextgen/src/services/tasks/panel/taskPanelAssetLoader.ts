import React from 'react';
import ReactDOM from 'react-dom';
import { discoverTaskPanelUrl, pinTaskPanelRelease, pinTaskPanelVersion } from './taskPanelAssetSource';
import type { TaskPanelAsset } from '@avernet-assets/task-panel';
export type { TaskPanelAsset } from '@avernet-assets/task-panel';

let loading: Promise<TaskPanelAsset> | null = null;
// Pin an evaluated script to this page, including after CSS failure. Never replace its global.
let selectedUrl: string | null = null;
let evaluatedAsset: TaskPanelAsset | null = null;

/** A single discovery/load per page; failure permits retry, never an implicit version fallback. */
export function loadTaskPanelAsset(): Promise<TaskPanelAsset> {
  if (loading) return loading;
  loading = discoverTaskPanelUrl().then(url => new Promise<TaskPanelAsset>((resolve, reject) => {
    const global = window as unknown as { React?: typeof React; ReactDOM?: typeof ReactDOM; taskPanel?: TaskPanelAsset };
    if ((selectedUrl && selectedUrl !== url) || (global.taskPanel && global.taskPanel !== evaluatedAsset)) {
      reject(new Error('任务副屏资产来源已变化或未知，请刷新页面后重试。')); return;
    }
    try { pinTaskPanelRelease(url); } catch (error) { reject(error); return; }
    global.React = React; global.ReactDOM = ReactDOM;
    const compatible = () => {
      const asset = global.taskPanel;
      if (!asset || asset.hostApiVersion !== 1 || typeof asset.assetVersion !== 'string' || !asset.assetVersion ||
          typeof asset.TaskLoopView !== 'function' || typeof asset.createTaskPanelController !== 'function') {
        throw new Error('任务副屏宿主接口不兼容，请刷新后重试。');
      }
      pinTaskPanelVersion(asset.assetVersion);
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
