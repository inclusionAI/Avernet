/**
 * Capabilities 核心运行时。约 50 行的扩展点机制，设计见 capabilities-system.md。
 *
 * 生命周期：
 *   open   —— defineExt / extend 累积；尚未合并
 *   sealed —— 合并所有 patch 并深冻结；extend 抛错，getExt 返回冻结值
 * 触发 seal：首次 getExt/useExt（= 应用首次渲染读取契约），或显式 sealExtensions()。
 */
import type { Ext, ExtPatch } from './types';

interface Entry<T extends object> {
  ext: Ext<T>;
  defaults: T;
  patches: ExtPatch<T>[];
}

let state: 'open' | 'sealed' = 'open';
const registry = new Map<string, Entry<any>>();
const resolved = new Map<Ext<any>, Readonly<any>>();

/** 深冻结：递归冻结对象与数组，防止运行期意外改动合并结果。 */
function deepFreeze<T>(value: T): Readonly<T> {
  if (value && typeof value === 'object' && !Object.isFrozen(value)) {
    Object.values(value as Record<string, unknown>).forEach(deepFreeze);
    Object.freeze(value);
  }
  return value;
}

function resolveEntry(entry: Entry<any>): Readonly<any> {
  // 浅拷贝默认值后逐 patch 应用：保证原始 defaults 对象不被替换型 patch 改动。
  // patch 须为不可变风格（函数式返回新值），列表追加用 (prev) => [...prev, x]。
  const merged: Record<string, unknown> = { ...entry.defaults };
  for (const patch of entry.patches) {
    for (const key of Object.keys(patch)) {
      const p = (patch as Record<string, unknown>)[key];
      merged[key] = typeof p === 'function' ? p(merged[key]) : p;
    }
  }
  const frozen = deepFreeze(merged);
  resolved.set(entry.ext, frozen);
  return frozen;
}

/**
 * 声明一个契约 + 开源默认值。仅允许在 **\/extension.ts 中调用（ESLint 强制）。
 * 默认值即开源行为，须完整可运行；类型从默认值推导。
 */
export function defineExt<T extends object>(name: string, defaults: T): Ext<T> {
  if (registry.has(name)) {
    throw new Error(`[capabilities] duplicate extension name: "${name}"`);
  }
  const ext: Ext<T> = { name, __defaults: defaults };
  registry.set(name, { ext, defaults, patches: [] });
  return ext;
}

/**
 * 扩展一个契约。仅允许在 src/internal/** 中调用（ESLint 强制）。
 * 必须在「seal（应用启动）」且「该契约首次被读取」之前完成，否则抛错。
 */
export function extend<T extends object>(ext: Ext<T>, patch: ExtPatch<T>): void {
  if (state === 'sealed') {
    throw new Error(
      `[capabilities] extend("${ext.name}") called after seal — ` +
        `扩展必须在应用首次渲染前完成（@ext wiring 在启动入口同步执行）`,
    );
  }
  const entry = registry.get(ext.name);
  if (!entry) {
    throw new Error(`[capabilities] unknown extension: "${ext.name}"`);
  }
  if (resolved.has(ext)) {
    throw new Error(
      `[capabilities] extend("${ext.name}") called after it was already read（sealed）— ` +
        `该契约已被 getExt/useExt 读取并冻结，扩展须在读取前完成`,
    );
  }
  entry.patches.push(patch);
}

/**
 * 锁定扩展（应用启动入口在 render 前调用）：此后不允许再 extend。
 * 注意不在此预先解析所有契约——契约在各自 extension.ts 首次被 import 时才 defineExt，
 * 可能晚于本函数（如开源形态下 @ext=empty，AppExt 随页面异步加载才注册）。
 * 各契约的合并改为首次 getExt 惰性进行；@ext 的所有 extend 在 render 前已完成，故惰性安全。
 */
export function sealExtensions(): void {
  if (state === 'sealed') return;
  // warm：此刻已注册的契约（@ext 同步注入的内部契约）立即合并冻结，固定其全部 patch。
  // 晚于本函数注册的契约（页面异步加载的 extension.ts）由 getExt 惰性解析（无内部 patch，安全）。
  registry.forEach((entry) => {
    if (!resolved.has(entry.ext)) resolveEntry(entry);
  });
  state = 'sealed';
}

/** 消费（非 React：requestConfig / Service / 工具函数）。首次读取时惰性合并并冻结。 */
export function getExt<T extends object>(ext: Ext<T>): Readonly<T> {
  const cached = resolved.get(ext);
  if (cached) return cached as Readonly<T>;
  const entry = registry.get(ext.name);
  if (!entry || entry.ext !== ext) {
    throw new Error(`[capabilities] extension not registered: "${ext.name}"`);
  }
  return resolveEntry(entry) as Readonly<T>;
}

/**
 * 消费（React）。返回合并后的只读契约值。
 * 值在首次读取时固定且冻结，引用稳定，无需响应式订阅。
 */
export function useExt<T extends object>(ext: Ext<T>): Readonly<T> {
  return getExt(ext);
}

/** 仅供测试：清空注册表并回到 open 态。 */
export function __resetExtensionsForTest(): void {
  state = 'open';
  registry.clear();
  resolved.clear();
}
