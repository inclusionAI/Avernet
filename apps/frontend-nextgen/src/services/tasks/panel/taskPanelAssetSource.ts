import { backendRequest, BackendRequestError } from '@/services/backendApi/httpClient';
import { useErrorNotifyStore } from '@/stores/errorNotifyStore';

let selectedReleaseUrl: string | null = null;
let selectedVersion: string | null = null;
export const TASK_PANEL_DEV_FALLBACK_URL = 'https://gw.alipayobjects.com/render/p/hitu_npm/@avernet-assets/task-panel/1.0.1/dist/index.umd.js';
let developmentRelease: Promise<string> | null = null;
let developmentStyle: HTMLLinkElement | null = null;
export function getTaskPanelDevelopmentStyle(url: string): HTMLLinkElement | null {
  return developmentStyle?.isConnected && developmentStyle.href === new URL('task-panel.css', url).href ? developmentStyle : null;
}

/** Both entries must originate from the same immutable release in this page. */
export function pinTaskPanelRelease(url: string): void {
  if (selectedReleaseUrl && selectedReleaseUrl !== url) throw new Error('任务副屏资产来源已变化或未知，请刷新页面后重试。');
  selectedReleaseUrl = url;
}

export function pinTaskPanelVersion(version: string): void {
  if (selectedVersion && selectedVersion !== version) throw new Error('任务副屏资产版本不一致，请刷新页面后重试。');
  selectedVersion = version;
}

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

async function discoverDevelopmentRelease(): Promise<string> {
  const localUrl = new URL('/task-panel/index.umd.js', window.location.href).href;
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), 3_000);
  const style = document.createElement('link');
  style.rel = 'stylesheet'; style.href = new URL('task-panel.css', localUrl).href;
  style.media = 'not all'; style.dataset.taskPanelSource = localUrl;
  try {
    // Probe the whole release before either entry executes; dev HTML is not an asset.
    const probes: [string, RegExp][] = [
      [localUrl, /^(?:application|text)\/(?:javascript|ecmascript)(?:;|$)/i],
      [new URL('recovery.umd.js', localUrl).href, /^(?:application|text)\/(?:javascript|ecmascript)(?:;|$)/i],
      [new URL('task-panel.css', localUrl).href, /^text\/css(?:;|$)/i],
    ];
    await Promise.all(probes.map(async ([url, contentType]) => {
      const response = await fetch(url, { method: 'HEAD', cache: 'no-store', signal: controller.signal });
      if (!response.ok || !contentType.test(response.headers.get('content-type') ?? '')) {
        throw new Error('本地任务副屏资源不可用。');
      }
    }));
    // A successful HEAD is not proof that the browser can load the stylesheet.
    await new Promise<void>((resolve, reject) => {
      style.onload = () => resolve();
      style.onerror = () => reject(new Error('本地任务副屏样式加载失败。'));
      controller.signal.addEventListener('abort', () => reject(new Error('本地任务副屏资源加载超时。')), { once: true });
      if (controller.signal.aborted) { reject(new Error('本地任务副屏资源加载超时。')); return; }
      document.head.appendChild(style);
    });
    developmentStyle = style;
    return localUrl;
  } catch {
    style.remove();
    return TASK_PANEL_DEV_FALLBACK_URL;
  } finally { style.onload = null; style.onerror = null; window.clearTimeout(timer); controller.abort(); }
}

export async function discoverTaskPanelUrl(): Promise<string> {
  if (process.env.NODE_ENV === 'development') {
    if (selectedReleaseUrl) return selectedReleaseUrl;
    developmentRelease ??= discoverDevelopmentRelease();
    return developmentRelease;
  }
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), 30_000);
  try {
    const result = await backendRequest<{
      code?: number;
      data?: { schema_version?: number; env?: string; bundles?: { name?: string; url?: string }[] };
    }>('/api/v1/collaboration/tasks/manifest', { method: 'GET', injectUserId: false, signal: controller.signal, operation: 'task-panel-manifest' }).catch(error => {
      if (error instanceof BackendRequestError && error.status !== 401 && error.toastKey) useErrorNotifyStore.getState().cancel(error.toastKey);
      throw error;
    });
    const data = result?.data;
    if (result?.code !== 200000 || data?.schema_version !== 1 || typeof data.env !== 'string' ||
        !Array.isArray(data.bundles) || data.bundles.length !== 1 || data.bundles[0]?.name !== 'taskPanel') {
      throw new Error('任务副屏 manifest 配置无效，请联系管理员。');
    }
    return bundleUrl(data.bundles[0].url);
  } finally { window.clearTimeout(timer); }
}
