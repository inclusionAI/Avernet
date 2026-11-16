import type { TaskPanelAsset } from '@avernet-assets/task-panel';
import { loadTaskPanelAsset } from './taskPanelAssetLoader';
import { discoverTaskPanelUrl, pinTaskPanelRelease, pinTaskPanelVersion, TASK_PANEL_DEV_FALLBACK_URL } from './taskPanelAssetSource';

type RecoveryAsset = Pick<TaskPanelAsset, 'hostApiVersion' | 'assetVersion' | 'createTaskPanelController'>;
let loading: Promise<RecoveryAsset> | null = null;
let evaluated: RecoveryAsset | null = null;

/** Headless recovery by default; only the development fallback uses the legacy full UMD. */
export function loadTaskPanelRecoveryAsset(): Promise<RecoveryAsset> {
  if (loading) return loading;
  loading = discoverTaskPanelUrl().then(url => {
    // The development fallback release predates the headless entry. Reuse its full UMD only in dev.
    if (process.env.NODE_ENV === 'development' && url === TASK_PANEL_DEV_FALLBACK_URL) return loadTaskPanelAsset();
    return new Promise<RecoveryAsset>((resolve, reject) => {
    const global = window as unknown as { taskPanelRecovery?: RecoveryAsset };
    if (global.taskPanelRecovery && global.taskPanelRecovery !== evaluated) {
      reject(new Error('任务恢复资产来源未知，请刷新页面后重试。')); return;
    }
    try { pinTaskPanelRelease(url); } catch (error) { reject(error); return; }
    const validate = () => {
      const asset = global.taskPanelRecovery;
      if (!asset || asset.hostApiVersion !== 1 || typeof asset.assetVersion !== 'string' || !asset.assetVersion ||
          typeof asset.createTaskPanelController !== 'function') throw new Error('任务恢复接口不兼容，请刷新后重试。');
      pinTaskPanelVersion(asset.assetVersion);
      return asset;
    };
    if (evaluated) { try { resolve(validate()); } catch (error) { reject(error); } return; }
    const script = document.createElement('script');
    script.src = new URL('recovery.umd.js', url).href; script.async = true;
    script.dataset.taskPanelSource = url;
    let timer: number;
    const cleanup = () => { window.clearTimeout(timer); script.onload = null; script.onerror = null; };
    const fail = (error: Error) => { cleanup(); script.remove(); reject(error); };
    timer = window.setTimeout(() => fail(new Error('任务恢复加载超时，请重试。')), 30_000);
    script.onerror = () => fail(new Error('任务恢复加载失败，请重试。'));
    script.onload = () => {
      evaluated = global.taskPanelRecovery ?? null;
      cleanup();
      try { resolve(validate()); } catch (error) { script.remove(); reject(error); }
    };
    document.head.appendChild(script);
    });
  }).catch(error => { loading = null; throw error; });
  return loading;
}
