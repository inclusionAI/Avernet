/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 *
 * Bot 工具函数
 * 提供统一的 Bot ID 校验和空值防护工具
 */

import type { Bot } from '@/services/backend-api/BotController';
import { BOT_STATUS } from '@/stores/botStore';

/**
 * 检查 Bot ID 是否有效
 * 有效条件：非空、非空字符串
 * 注意：'default' 也是有效的 Bot ID（用户拥有一个不可删除的 default bot）
 * @param botId 待检查的 Bot ID
 * @returns 是否有效
 */
export function isValidBotId(
  botId: string | null | undefined,
): botId is string {
  return !!botId && botId !== '';
}

/**
 * 获取安全的 Bot ID
 * 如果传入的 botId 无效，返回 fallback（如果有效）或 null
 * @param botId 待检查的 Bot ID
 * @param fallback 备用 Bot ID
 * @returns 有效的 Bot ID 或 null
 */
export function getSafeBotId(
  botId: string | null | undefined,
  fallback?: string | null,
): string | null {
  if (isValidBotId(botId)) return botId;
  return isValidBotId(fallback) ? fallback : null;
}

/**
 * 从 Bot 列表中找到一个有效的备选 Bot
 * **严格要求**：只有 ACTIVE 且有 binding_id 的 Bot 才能作为备选
 * 因为 switchBot 需要 binding_id 来获取连接信息
 * @param bots Bot 列表
 * @param excludeBotId 要排除的 Bot ID（通常是被删除的 Bot）
 * @returns 可以切换的 Bot 或 null
 */
export function findFallbackBot(bots: Bot[], excludeBotId: string): Bot | null {
  // 严格条件：只有 ACTIVE 且有 binding_id 的 Bot 才能切换
  // PENDING/FAILD 等其他状态的 Bot 没有 binding_id，无法切换
  const activeBot = bots.find(
    (b) =>
      b.bot_id !== excludeBotId &&
      b.status === BOT_STATUS.ACTIVE &&
      !!b.binding_id, // 必须有 binding_id
  );

  if (activeBot) {
    console.log('[findFallbackBot] 找到可用备选 Bot:', activeBot.bot_id);
    return activeBot;
  }

  console.warn(
    '[findFallbackBot] 未找到可用备选 Bot，剩余 Bot 状态:',
    bots
      .filter((b) => b.bot_id !== excludeBotId)
      .map((b) => ({ id: b.bot_id, status: b.status, binding: b.binding_id })),
  );
  return null;
}

/**
 * API 请求参数校验装饰器
 * 在调用 API 前校验 bot_id 是否有效
 * @param fn 要被包装的 API 函数
 * @param paramsName 包含 bot_id 的参数名（默认为 'params'）
 * @returns 包装后的函数
 */
export function withBotIdValidation<T extends { bot_id?: string | null }, R>(
  fn: (params: T) => Promise<R>,
  errorMessage = '无效的 Bot ID',
): (params: T) => Promise<R> {
  return async (params: T) => {
    if (!isValidBotId(params.bot_id)) {
      throw new Error(errorMessage);
    }
    return fn(params);
  };
}
