/**
 * A2A 客户端总开关——spec stage2 §1：默认 off；本文件是**唯一**读取入口，
 * 灰度放量/整体回退都从这里生效。
 *
 * 判定优先级（高→低）：
 * 1. localStorage '0'/'1' —— 运行期人工开关（现场排查/紧急回 v3 用）；
 * 2. 构建期 define 常量 __A2A_CLIENT_DEFAULT__（@umijs/max define 契约，
 *    由镜像构建参数 A2A_CLIENT_DEFAULT=true 注入——验证/灰度镜像整包默认开，
 *    产品主线构建不注入即保持 off）；
 * 3. 两级皆无 → off（主线现状，零行为变化）。
 * 正式远程配置放量载体（M4）仍在**本文件内**扩展，不另开读取口。
 */
export const A2A_SWITCH_KEY = 'A2A_CHAT_CLIENT';

declare const __A2A_CLIENT_DEFAULT__: boolean | string | undefined;

export function isA2aClientEnabled(): boolean {
  try {
    if (typeof window !== 'undefined') {
      const v = window.localStorage.getItem(A2A_SWITCH_KEY);
      if (v === '0') return false; // 运行期显式关优先于一切（现场回退）
      if (v === '1') return true;
    }
  } catch {
    // storage 不可用（隐私模式等）→ 落到构建期默认，不武断全关
  }
  try {
    // 双形态吸收（review P2）：unit-built 布尔（config/a2aBuildDefault.ts 的
    // 正路）与防御性字符串 'true'（旧构建产物/异构 define 管线）都认；
    // '"'true'"' 这类带引号字面量恒 false——引号进不了这个判定。
    const buildDefault = typeof __A2A_CLIENT_DEFAULT__ === 'undefined'
      ? undefined
      : __A2A_CLIENT_DEFAULT__;
    return buildDefault === true || buildDefault === 'true';
  } catch {
    return false;
  }
}