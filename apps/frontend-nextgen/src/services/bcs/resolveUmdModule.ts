// @sdd: UMD 模块加载与导出智能解析（方式②组件加载层，无 SDK 依赖）。
//
// 背景：引擎 loadCDNComponent 按 `exportName.split('.')` 从 window 顶层逐层取，要求
// exportName 精确匹配 UMD 全局变量名。但实际 UMD 全局名（由资产构建 vite lib.name 决定）
// 不一定与 BCS manifest / _libraryName 吐的库名一致：
//   - 当前源码 vite.config lib.name = 'bcsPanel'
//   - 线上 @alipay/bcn-panel-asset@1.1.3 UMD 头: e.BcnPanelAsset = t(...)  → 全局 BcnPanelAsset
// 故不能纯按点路径取，需"加载脚本后探测实际全局对象"（对齐 ocb @alipay/umd-loader 思路）。
//
// 策略（按 url 缓存脚本注入，全局只执行一次）：
//   1. 加载前快照 window 自有 key，加载后 diff 新增对象 → 命中即库对象
//   2. 命中的库对象上按 exportName 点路径 / 末段取组件
//   3. 若脚本已被别处加载过（diff 为空）→ 回退 window[libraryName] 点路径取
//   4. 仍无 → 遍历 window 顶层对象找含 exportName 末段的（最终兜底）
//
// 只用浏览器 API + react 类型，无内部包依赖，Open Core 安全。
import type { ComponentType } from 'react';

/** 脚本注入缓存：同 url 全局只注入一次（避免重复执行副作用）。 */
const scriptCache = new Map<string, Promise<void>>();

/** 库对象缓存：同 url 加载结果复用（null 表示加载过但未探测到库对象）。 */
const libCache = new Map<string, Promise<Record<string, unknown> | null>>();

function injectScript(url: string): Promise<void> {
  const cached = scriptCache.get(url);
  if (cached) return cached;
  const p = new Promise<void>((resolve, reject) => {
    const script = document.createElement('script');
    script.src = url;
    script.async = true;
    script.onload = () => resolve();
    script.onerror = () => reject(new Error(`UMD script load failed: ${url}`));
    document.head.appendChild(script);
  });
  scriptCache.set(url, p);
  return p;
}

/**
 * 加载 UMD 脚本并探测挂到 window 的库对象。
 *
 * 探测顺序：
 * 1. 加载前快照 window key，加载后 diff 新增的非空 object → 视为库对象
 * 2. diff 为空（脚本曾加载过）→ 返回 null（交由调用方走 window[libraryName] / 全量扫描兜底）
 */
export async function loadUmdLibrary(url: string): Promise<Record<string, unknown> | null> {
  const cached = libCache.get(url);
  if (cached) return cached;
  const p = (async () => {
    const before = new Set(Object.keys(window));
    await injectScript(url);
    const after = Object.keys(window);
    for (const key of after) {
      if (before.has(key)) continue;
      const value = (window as unknown as Record<string, unknown>)[key];
      if (value && typeof value === 'object') return value as Record<string, unknown>;
    }
    return null;
  })();
  libCache.set(url, p);
  return p;
}

/**
 * 从给定对象按 exportName 取组件。
 *
 * - 先按点路径逐层取（如 'bcsPanel.StateMachineRunView' → obj.bcsPanel.StateMachineRunView）
 * - 再按末段取（obj.StateMachineRunView，named export 常见形态）
 */
export function pickExport(library: Record<string, unknown> | null | undefined, exportName: string): ComponentType | null {
  if (!library) return null;
  const parts = exportName.split('.');
  let cursor: unknown = library;
  for (const part of parts) {
    cursor = (cursor as Record<string, unknown> | null | undefined)?.[part];
    if (cursor === undefined) break;
  }
  if (typeof cursor === 'function' || (cursor && typeof cursor === 'object')) {
    return cursor as unknown as ComponentType;
  }
  const last = parts[parts.length - 1];
  const direct = library[last];
  if (typeof direct === 'function' || (direct && typeof direct === 'object')) {
    return direct as unknown as ComponentType;
  }
  return null;
}

/**
 * 最终兜底：遍历 window 顶层对象，找含 `exportName` 末段 key 的对象并取该导出。
 *
 * 仅在快照 diff 与 window[libraryName] 都未命中时使用；window 顶层 key 数量有限，
 * 副屏打开触发一次，性能可接受。
 */
function scanWindowForExport(exportName: string): ComponentType | null {
  if (typeof window === 'undefined') return null;
  const last = exportName.split('.').pop();
  if (!last) return null;
  const w = window as unknown as Record<string, unknown>;
  for (const key of Object.keys(w)) {
    const value = w[key];
    if (!value || typeof value !== 'object') continue;
    const found = (value as Record<string, unknown>)[last];
    if (typeof found === 'function' || (found && typeof found === 'object')) {
      return found as unknown as ComponentType;
    }
  }
  return null;
}

/**
 * 解析 UMD 远程组件：加载脚本 + 智能探测全局对象 + 取导出。
 *
 * @param url        UMD bundle CDN URL
 * @param exportName 导出点路径（如 'bcsPanel.StateMachineRunView' 或 'StateMachineRunView'）
 * @param libraryName 库名（manifest/_libraryName，如 'bcsPanel'；与 UMD 实际全局名可能不一致）
 * @returns 组件；找不到抛 Error
 */
export async function resolveUmdComponent(
  url: string,
  exportName: string,
  libraryName?: string,
): Promise<ComponentType> {
  // 1. 加载并探测新增库对象（主路径，UMD global 名无关 manifest 库名）
  const library = await loadUmdLibrary(url);
  const picked = pickExport(library, exportName);
  if (picked) return picked;

  // 2. 脚本曾被加载过（diff 为空）→ 直接 window[libraryName] 点路径取
  if (libraryName && typeof window !== 'undefined') {
    const wlib = (window as unknown as Record<string, unknown>)[libraryName];
    const fromNamed = pickExport(wlib as Record<string, unknown> | undefined, exportName);
    if (fromNamed) return fromNamed;
  }

  // 3. 全量扫描 window 顶层对象，按末段 key 兜底（覆盖 global 名为 BcnPanelAsset 等历史名）
  const scanned = scanWindowForExport(exportName);
  if (scanned) return scanned;

  throw new Error(`UMD export not found: ${exportName} (${url})`);
}
