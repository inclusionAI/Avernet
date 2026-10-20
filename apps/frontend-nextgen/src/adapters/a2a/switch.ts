/**
 * A2A 客户端总开关——spec stage2 §1：默认 off；本文件是**唯一**读取入口，
 * 灰度放量/整体回退都从这里生效。开发 override 走 localStorage（仓内先例
 * AICODING_RESUME），正式放量载体（@umijs/max defineConfig 契约）M2 接线
 * 时在此文件内扩展，不另开读取口。
 */
export const A2A_SWITCH_KEY = 'A2A_CHAT_CLIENT';

export function isA2aClientEnabled(): boolean {
  try {
    return typeof window !== 'undefined'
      && window.localStorage.getItem(A2A_SWITCH_KEY) === '1';
  } catch {
    return false; // storage 不可用（隐私模式等）→ 保守 off
  }
}