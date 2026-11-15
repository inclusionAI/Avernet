import { getCapabilities } from '@/capabilities';
import type { BotChatSessionView, BotModelView, ChatBotView } from '@/services/workspace/botSessionService';
import { botSessionService } from '@/services/workspace/botSessionService';
import { botModelContextKey, loadCachedBotModels } from '@/services/workspace/botModelCache';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { toast } from 'sonner';

export interface UseBotModelsResult {
  models: BotModelView[];
  activeModelId: string | null;
  isLoadingModels: boolean;
  loadModels: () => void;
  selectModel: (modelId: string) => Promise<boolean>;
}

/** 合并 capability 注入的模型徽标；getModelBadge 默认 null（无徽标时保持原列表项引用）。 */
function withModelBadges(models: BotModelView[]): BotModelView[] {
  return models.map((model) => {
    const badgeResult = getCapabilities().getModelBadge(model);
    const badge = badgeResult.status === 'available' ? badgeResult.value : null;
    return badge ? { ...model, badge } : model;
  });
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
  autoLoad = true,
): UseBotModelsResult {
  const key = botModelContextKey(bot, activeIdentityId);
  const context = useRef({ bot, activeIdentityId });
  context.current = { bot, activeIdentityId };
  const [request, setRequest] = useState({ key: '', revision: 0 });
  const [state, setState] = useState<{ key: string; models: BotModelView[]; loading: boolean }>({
    key: '', models: [], loading: false,
  });
  const models = state.key === key ? state.models : [];
  const isLoadingModels = state.key === key && state.loading;
  const loadModels = useCallback(() => setRequest((current) => ({ key, revision: current.revision + 1 })), [key]);

  useEffect(() => {
    const { bot, activeIdentityId } = context.current;
    if (!autoLoad && request.key !== key) return;
    if (!bot || !activeIdentityId || bot.isFriendBot) {
      setState({ key, models: [], loading: false });
      return;
    }
    let cancelled = false;
    setState((current) => ({ key, models: current.key === key ? current.models : [], loading: true }));
    loadCachedBotModels(bot, activeIdentityId).then((res) => {
      if (cancelled) return;
      if (!res.ok) toast.error(res.error.friendlyMessage);
      setState({ key, models: res.ok ? withModelBadges(res.data) : [], loading: false });
    }).catch((error: unknown) => {
      if (cancelled) return;
      toast.error(error instanceof Error ? error.message : '模型列表加载失败');
      setState({ key, models: [], loading: false });
    });
    return () => {
      cancelled = true;
    };
  }, [key, autoLoad, request]);

  const activeModelId = useMemo(() => session?.model ?? models[0]?.modelId ?? null, [models, session?.model]);

  const selectModel = useCallback(
    async (modelId: string) => {
      if (!bot || bot.isFriendBot || !session || !activeIdentityId || modelId === session.model) return false;
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

  return { models, activeModelId, isLoadingModels, loadModels, selectModel };
}
