import type { BotEngineConfig } from '@/domain/botEditor';
import { botEditorService } from '@/services/botWorkshop/botEditorService';
import { useCallback, useEffect, useState } from 'react';
import { toast } from 'sonner';

export function useBotEngineConfig(botId: string | null, ownerId: string | undefined, isOwner: boolean) {
  const [config, setConfig] = useState<BotEngineConfig>({});
  /** 最近一次落库成功的配置，作为 diff 基线；编辑草稿只写入 config */
  const [savedConfig, setSavedConfig] = useState<BotEngineConfig>({});
  const [loading, setLoading] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [restoring, setRestoring] = useState(false);

  useEffect(() => {
    setConfig({});
    setSavedConfig({});
    setLoaded(false);
  }, [botId, isOwner, ownerId]);

  const load = useCallback(async () => {
    if (!botId || !isOwner || loaded || loading) return;
    setLoading(true);
    try {
      const current = await botEditorService.loadEngineConfig(botId, ownerId);
      setConfig(current);
      setSavedConfig(current);
      setLoaded(true);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '引擎配置加载失败');
    } finally {
      setLoading(false);
    }
  }, [botId, isOwner, loaded, loading, ownerId]);

  const save = useCallback(async () => {
    try {
      if (!botId || !isOwner) throw new Error('仅 Bot Owner 可修改引擎配置');
      await botEditorService.saveEngineConfig(botId, config, ownerId);
      setSavedConfig(config);
      toast.success('引擎配置已保存');
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '引擎配置保存失败');
      throw error;
    }
  }, [botId, config, isOwner, ownerId]);

  const restoreDefaults = useCallback(async () => {
    if (!botId || !isOwner || restoring) throw new Error('仅 Bot Owner 可恢复默认配置');
    setRestoring(true);
    try {
      const defaults = await botEditorService.loadDefaultEngineConfig(botId, ownerId);
      await botEditorService.saveEngineConfig(botId, defaults, ownerId);
      setConfig(defaults);
      setSavedConfig(defaults);
      setLoaded(true);
      toast.success('已恢复默认配置');
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '恢复默认配置失败');
      throw error;
    } finally {
      setRestoring(false);
    }
  }, [botId, isOwner, ownerId, restoring]);

  return {
    config: isOwner ? config : {},
    savedConfig: isOwner ? savedConfig : {},
    setConfig,
    loading,
    restoring,
    load,
    save,
    restoreDefaults,
  };
}
