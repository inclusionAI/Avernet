import type { UmdModule } from './types';

/** 模块缓存：Map<cdnUrl, UmdModule>，按 cdn 维度复用 fetch + eval 结果 */
const moduleCache = new Map<string, UmdModule>();

/** 并发去重：同一 cdn 在 fetch 期间复用同一个 promise，避免重复网络请求 */
const pendingFetch = new Map<string, Promise<string>>();

export function getModule(cdn: string): UmdModule | undefined {
  return moduleCache.get(cdn);
}

export function setModule(cdn: string, mod: UmdModule): void {
  moduleCache.set(cdn, mod);
}

export function getPendingFetch(cdn: string): Promise<string> | undefined {
  return pendingFetch.get(cdn);
}

export function setPendingFetch(cdn: string, promise: Promise<string>): void {
  pendingFetch.set(cdn, promise);
}

export function clearPendingFetch(cdn: string): void {
  pendingFetch.delete(cdn);
}

/** 清除模块缓存，cdn 缺省时清空全部 */
export function clearModuleCache(cdn?: string): void {
  if (cdn) {
    moduleCache.delete(cdn);
    pendingFetch.delete(cdn);
  } else {
    moduleCache.clear();
    pendingFetch.clear();
  }
}
