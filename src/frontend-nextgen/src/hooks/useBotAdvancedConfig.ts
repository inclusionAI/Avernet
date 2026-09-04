import type { BotChannel, BotChannelInput, BotIdentityFile } from '@/domain/botAdvancedConfig';
import { botAdvancedConfigService } from '@/services/botWorkshop/botAdvancedConfigService';
import { useCallback, useEffect, useState } from 'react';
import { toast } from 'sonner';

export function useBotAdvancedConfig(botId: string | null, enabled = true) {
  const [files, setFiles] = useState<BotIdentityFile[]>([]);
  const [channels, setChannels] = useState<BotChannel[]>([]);
  const [loading, setLoading] = useState(false);
  const load = useCallback(async () => {
    if (!botId || !enabled) return;
    setLoading(true);
    const [fileResult, channelResult] = await Promise.allSettled([
      botAdvancedConfigService.listIdentityFiles(botId),
      botAdvancedConfigService.listChannels(botId),
    ]);
    setFiles(fileResult.status === 'fulfilled' ? fileResult.value : []);
    setChannels(channelResult.status === 'fulfilled' ? channelResult.value : []);
    setLoading(false);
  }, [botId, enabled]);
  useEffect(() => {
    void load();
  }, [load]);
  const act = useCallback(
    async (work: () => Promise<unknown>, message: string) => {
      try {
        await work();
        toast.success(message);
        await load();
      } catch (error) {
        toast.error(error instanceof Error ? error.message : '操作失败');
        throw error;
      }
    },
    [load],
  );
  const getFile = useCallback((type: string) => botAdvancedConfigService.getIdentityFile(botId!, type), [botId]);
  const saveFile = useCallback(
    (type: string, content: string) =>
      act(() => botAdvancedConfigService.saveIdentityFile(botId!, type, content), '文档已保存'),
    [act, botId],
  );
  return {
    files,
    channels,
    loading,
    getFile,
    saveFile,
    createChannel: (input: BotChannelInput) =>
      act(() => botAdvancedConfigService.createChannel(botId!, input), '渠道已绑定'),
    updateChannel: (id: number, input: BotChannelInput) =>
      act(() => botAdvancedConfigService.updateChannel(botId!, id, input), '渠道配置已更新'),
    toggleChannel: (channel: BotChannel) =>
      act(() => botAdvancedConfigService.setChannelStatus(botId!, channel), '渠道状态已更新'),
    deleteChannel: (id: number) => act(() => botAdvancedConfigService.deleteChannel(botId!, id), '渠道已删除'),
  };
}
