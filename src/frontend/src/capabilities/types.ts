/**
 * Capabilities 模块契约模型 —— 类型契约。
 * 设计见 docs/specs/capabilities-system.md。
 */

/**
 * 契约句柄。由 defineExt 创建，作为消费（useExt/getExt）与扩展（extend）的唯一身份。
 * 业务代码持有它的引用，IDE「find usages」可同时定位消费方与内部扩展方。
 */
export interface Ext<T extends object> {
  readonly name: string;
  /** 仅供运行时内部读取，勿直接消费（消费走 useExt/getExt） */
  readonly __defaults: T;
}

/**
 * 扩展补丁。对契约的每个字段，可：
 * - 直接给值 → 替换默认值
 * - 给 (prev) => next 函数 → 基于当前值计算（列表追加用这个）
 * 未出现的字段保持默认值。
 */
export type ExtPatch<T extends object> = {
  [K in keyof T]?: T[K] | ((prev: T[K]) => T[K]);
};
