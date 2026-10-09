import type { BotDomain } from '@/domain/botWorkshop';
import { botEditorController } from '@/services/backendApi/bots/botEditorController';
import { isEnvelopeFailure } from '@/services/backendApi/types';

export const botEditLockService = {
  async claim(bot: BotDomain) {
    const response = await (bot.lock?.status === 'other'
      ? botEditorController.stealEditLock(bot.id, bot.ownerId)
      : botEditorController.acquireEditLock(bot.id, bot.ownerId));
    if (isEnvelopeFailure(response)) throw new Error(response.message || '获取编辑锁失败');
    const lock = response.data;
    if (lock?.need_lock === false && lock.locked === false) return;
    if (lock?.acquired !== true) {
      const holder = lock?.holder_name || lock?.holder_user_id;
      throw new Error(holder ? `未获取到编辑锁，当前由 ${holder} 持有，请刷新后重试` : '未获取到编辑锁，请刷新后重试');
    }
  },
  async release(bot: BotDomain) {
    const response = await botEditorController.releaseEditLock(bot.id, bot.ownerId);
    if (isEnvelopeFailure(response) || response.data?.released !== true) {
      throw new Error(
        response.message && isEnvelopeFailure(response) ? response.message : '释放编辑锁失败，请刷新后重试',
      );
    }
  },
};
