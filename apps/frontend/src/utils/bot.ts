/**
 * Bot 相关工具函数
 */

import { type Bot } from '@/stores/botStore';

/**
 * 获取 Bot 头像 URL
 * 优先使用 ext.avatar_url（by-conditions 和 by-owner 接口返回），
 * 兼容顶层 avatar_url
 */
export function getBotAvatarUrl(
  bot:
    | (Bot | { ext?: { avatar_url?: string }; avatar_url?: string | null })
    | undefined
    | null,
): string | undefined {
  if (!bot) return undefined;
  if (bot.ext?.avatar_url) return bot.ext.avatar_url;
  return bot.avatar_url || undefined;
}
