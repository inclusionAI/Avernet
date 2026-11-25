import { activateDormantBot } from '@/services/backendApi/bots/botDormantController';
import type { BotDomain } from './types';

export function isDormantPersonalBot(bot: BotDomain): boolean {
  return (
    bot.serviceMode === 'non-service' &&
    bot.deployment === 'cloud' &&
    bot.runtime.engine !== 'teclaw' &&
    bot.lifecycle === 'offline' &&
    (bot.rawStatus?.toUpperCase() === 'RECYCLED' || bot.rawDisplayState?.toLowerCase() === 'dormant')
  );
}

export const botDormantService = {
  async activate(bot: BotDomain, userId: string) {
    if (
      !userId ||
      bot.ownerId !== userId ||
      !isDormantPersonalBot(bot) ||
      !bot.actions.includes('activate') ||
      bot.disabledActions.activate
    ) {
      throw new Error('当前用户或 Bot 状态不支持激活，请刷新列表后重试');
    }
    const response = await activateDormantBot(bot.id, bot.ownerId, userId);
    if (
      !response.data ||
      response.data.bot_id !== bot.id ||
      !['ACTIVE', 'REACTIVATING'].includes(response.data.status.toUpperCase())
    ) {
      throw new Error(response.data?.message || '激活接口未返回有效状态，请刷新列表确认');
    }
    return response.data;
  },
};
