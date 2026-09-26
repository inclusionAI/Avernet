/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 *
 * Tracert 埋点 facade
 *
 * 委托给 AppExt.trackerAdapter：开源默认 no-op，内部 extend 注入真实 Tracert。
 * 消费方无需改动——继续 import { Tracert } from '@/utils/tracert' 即可。
 *
 * 基于 fast-spm / Tracert（阿里内部埋点体系）
 *
 * 点位命名规范：页面-模块-操作（b位-c位-d位）
 * 例如：启动页-启动页-Bot初始化成功
 *
 * 使用方式：
 *   import { Tracert } from '@/utils/tracert';
 *   Tracert.click('启动页-Bot初始化成功', { bot_id: bot.bot_id });
 *   Tracert.expo('首页-活动规则');
 *
 * DOM 属性方式（交由 Tracert 自动采集）：
 *   <button spm="启动页-多Bot选择-确认" data-aspm-param={`bot_id=${botId}`}>
 */

import { getExt } from '@/capabilities';
import { AppExt } from '@/shell/extension';

/**
 * Tracert 埋点工具对象
 */
export const Tracert = {
  /**
   * 动态配置 Tracert（如设置 B 位）
   * @param options 配置项，如 { spmBPos: 'b176901' }
   */
  config: (options: Record<string, unknown>): void => {
    getExt(AppExt).trackerAdapter.config(options);
  },

  /**
   * 手动触发点击埋点
   * @param spm 点位标识，格式：页面-模块-操作
   * @param params 扩展参数
   */
  click: (spm: string, params?: Record<string, unknown>): void => {
    getExt(AppExt).trackerAdapter.click(spm, params);
  },

  /**
   * 手动触发曝光埋点
   * @param spm 点位标识，格式：页面-模块-操作
   * @param direction 曝光方向，默认 'down'
   * @param params 扩展参数
   */
  expo: (
    spm: string,
    direction?: string,
    params?: Record<string, unknown>,
  ): void => {
    getExt(AppExt).trackerAdapter.expo(spm, direction, params);
  },
};

// 兼容旧代码，保留 track 函数（已废弃，建议直接使用 Tracert.click）
/** @deprecated 请直接使用 Tracert.click() */
export function track(spm: string, params?: Record<string, unknown>): void {
  Tracert.click(spm, params);
}
