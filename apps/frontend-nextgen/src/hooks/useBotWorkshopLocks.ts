import type { BotDomain } from '@/domain/botWorkshop';
import { botEditLockService } from '@/services/botWorkshop/botEditLockService';
import { resolveBotRuntimeStage } from '@/services/botWorkshop/botRuntimeStage';
import { history } from '@umijs/max';
import { useCallback } from 'react';
import { toast } from 'sonner';

export function useBotWorkshopLocks(load: (options?: { silent?: boolean }) => Promise<void>) {
  const claimLock = useCallback(
    async (bot: BotDomain) => {
      const toastId = toast.loading('正在获取编辑锁...');
      try {
        await botEditLockService.claim(bot);
        toast.success('可以进入编辑', { id: toastId });
        await load({ silent: true });
        const params = new URLSearchParams({
          type: 'edit',
          id: bot.id,
          runtime_stage: resolveBotRuntimeStage(bot.lifecycle),
        });
        if (bot.ownerId) params.set('owner_id', bot.ownerId);
        history.push(`/bot-workshop/detail?${params.toString()}`);
      } catch (error) {
        toast.error(error instanceof Error ? error.message : '获取编辑锁失败', { id: toastId });
        await load({ silent: true });
        throw error;
      }
    },
    [load],
  );
  const releaseLock = useCallback(
    async (bot: BotDomain) => {
      const toastId = toast.loading('正在释放编辑锁...');
      try {
        await botEditLockService.release(bot);
        toast.success('编辑锁已释放', { id: toastId });
      } catch (error) {
        toast.error(error instanceof Error ? error.message : '释放编辑锁失败', { id: toastId });
        throw error;
      } finally {
        await load({ silent: true });
      }
    },
    [load],
  );
  return { claimLock, releaseLock };
}
