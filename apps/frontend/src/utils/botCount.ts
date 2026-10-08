/**
 * Bot 数量统计工具
 *
 * 背景:`/api/bots/by-owner-or-collaborator` 接口返回的列表是
 * 「当前用户拥有的 Bot」+「当前用户被加为协作者的 Bot」的并集,
 * 接口 `total` 字段也是并集总数。
 *
 * 而 Bot 配额(MAX_BOT_COUNT)只约束「用户自己拥有的 Bot」,
 * 协作的服务 Bot 属于别人,不应占用当前用户配额。
 * 因此统计配额用量时必须基于真实归属过滤,不能直接用接口 total。
 */

import { type Bot } from '@/stores/botStore';

/**
 * 判断一个 Bot 是否归属于当前用户(owner)。
 * 优先以服务端下发的 user_role 为准;缺失时回退到 owner_id 比较。
 * 与 ServiceBotList 的归属判定保持一致。
 */
export function isOwnedBot(
  bot: Pick<Bot, 'user_role' | 'owner_id'>,
  currentUserId?: string | null,
): boolean {
  if (bot.user_role === 'owner') return true;
  if (bot.user_role === 'collaborator') return false;
  // user_role 缺失时的兜底:按 owner_id 判定
  return !!currentUserId && bot.owner_id === currentUserId;
}

/**
 * 统计计入配额的「当前用户拥有的 Bot」数量。
 * 排除:协作 Bot(属于他人)、桌面 Bot(desktop 不占云端配额)。
 */
export function countOwnedBots(
  bots: Array<Pick<Bot, 'user_role' | 'owner_id' | 'bot_type'>>,
  currentUserId?: string | null,
): number {
  return bots.filter(
    (bot) => bot.bot_type !== 'desktop' && isOwnedBot(bot, currentUserId),
  ).length;
}
