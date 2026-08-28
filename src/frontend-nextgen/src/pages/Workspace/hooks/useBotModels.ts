import type { BotChatSessionView, BotModelView, ChatBotView } from '@/services/workspace/botSessionService';
import { botSessionService } from '@/services/workspace/botSessionService';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { toast } from 'sonner';

export interface UseBotModelsResult {
  models: BotModelView[];
  activeModelId: string | null;
  isLoadingModels: boolean;
  selectModel: (modelId: string) => Promise<boolean>;
}

/**
 * bot 单聊模型列表与当前会话模型切换。
 * 列表按当前 bot 拉取；切换成功后通过上层会话 map 更新当前会话 model。
 */
export function useBotModels(
  bot: ChatBotView | null,
  session: BotChatSessionView | null,
  activeIdentityId: string | null,
  onSessionModelChange: (botId: string, sessionId: string, model: string) => void,
): UseBotModelsResult {
  const [models, setModels] = useState<BotModelView[]>([]);
  const [isLoadingModels, setIsLoadingModels] = useState(false);

  useEffect(() => {
    if (!bot || !activeIdentityId) {
      setModels([]);
      return;
    }
    let cancelled = false;
    setIsLoadingModels(true);
    botSessionService.listModels(bot, activeIdentityId).then((res) => {
      if (cancelled) return;
      if (!res.ok) toast.error(res.error.friendlyMessage);
      else setModels(res.data);
      setIsLoadingModels(false);
    });
    return () => {
      cancelled = true;
    };
  }, [bot, activeIdentityId]);

  const activeModelId = useMemo(() => session?.model ?? models[0]?.modelId ?? null, [models, session?.model]);

  const selectModel = useCallback(
    async (modelId: string) => {
      if (!bot || !session || !activeIdentityId || modelId === session.model) return false;
      const res = await botSessionService.updateSessionModel(bot, activeIdentityId, session.sessionId, modelId);
      if (!res.ok) {
        toast.error(res.error.friendlyMessage);
        return false;
      }
      onSessionModelChange(bot.botId, session.sessionId, modelId);
      toast.success('模型已切换');
      return true;
    },
    [activeIdentityId, bot, onSessionModelChange, session],
  );

  return { models, activeModelId, isLoadingModels, selectModel };
}
