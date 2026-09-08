import { notifyError, notifySuccess } from '@/components/ui/notify';
import { useCallback } from 'react';

export function useCopyBotId() {
  return useCallback(async (botId: string) => {
    try {
      await navigator.clipboard.writeText(botId);
      notifySuccess('Bot ID 已复制');
    } catch {
      notifyError(`复制失败，请手动复制：${botId}`);
    }
  }, []);
}
