import type { BotDomain } from '@/services/botWorkshop';
import { resolveBotRuntimeStage } from '@/services/botWorkshop/botRuntimeStage';
import { buildAgentCodingChatPath, workspaceService } from '@/services/workspace';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { history } from '@umijs/max';
import { useCallback } from 'react';
import { toast } from 'sonner';

export function useBotWorkshopNavigation() {
  const openDetail = useCallback((botOrId: BotDomain | string, type: 'view' | 'edit' = 'view') => {
    const id = typeof botOrId === 'string' ? botOrId : botOrId.id;
    const bot = typeof botOrId === 'string' ? undefined : botOrId;
    const params = new URLSearchParams({ type, id });
    if (bot) params.set('runtime_stage', resolveBotRuntimeStage(bot.lifecycle));
    history.push(`/bot-workshop/detail?${params.toString()}`);
  }, []);
  const openConversation = useCallback((bot: BotDomain) => {
    if (bot.runtime.isAgentCodingBot) {
      history.push(buildAgentCodingChatPath({ botId: bot.id, spaceId: bot.spaceId, spaceName: bot.spaceName }));
      return;
    }
    const current = useWorkspaceStore.getState();
    const user = current.identities.find((identity) => identity.kind === 'user');
    if (!user) {
      toast.error('用户身份未加载完成，请稍后重试');
      return;
    }
    const botId = bot.id.includes(':') || !bot.ownerId ? bot.id : `${bot.id}:${bot.ownerId}`;
    workspaceService.persistIdentity(user.id);
    current.setActiveIdentityId(user.id);
    const workspace = useWorkspaceStore.getState();
    workspace.setView('chat');
    workspace.selectBotSession(null);
    if (!workspace.expandedBotIds[botId]) workspace.toggleBotExpanded(botId);
    workspace.setBotExpandedSection(botId, 'mine');
    history.push(`/workspace?${new URLSearchParams({ tab: 'chat', bot: botId }).toString()}`);
  }, []);
  return { openDetail, openConversation };
}
