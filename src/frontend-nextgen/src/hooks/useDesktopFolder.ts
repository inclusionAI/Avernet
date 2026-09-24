import { localBotService } from '@/services/botWorkshop/localBotService';
import { useCallback } from 'react';
import { toast } from 'sonner';
export function useDesktopFolder(botId: string | null) {
  return useCallback(
    async (path?: string) => {
      if (!botId) return;
      try {
        await localBotService.openFolder(botId, path);
        toast.success('打开目录请求已提交');
      } catch (e) {
        toast.error(e instanceof Error ? e.message : '打开目录失败');
      }
    },
    [botId],
  );
}
