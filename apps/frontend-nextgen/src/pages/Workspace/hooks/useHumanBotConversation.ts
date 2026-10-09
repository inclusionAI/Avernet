import type { WorkspaceView } from '@/domain/collaboration/availableViews';
import { findSelectedChatBot } from '@/hooks/workspaceIdentityMapper';
import type { PanelHandle } from '@tc-chat/core';
import { useMemo, type RefObject } from 'react';
import { useBotChat } from './useBotChat';
import { useBotSessions } from './useBotSessions';
import { useFriendBots } from './useFriendBots';
import { useOwnedBots } from './useOwnedBots';

interface UseHumanBotConversationOptions {
  activeIdentityId: string | null;
  isUserIdentity: boolean;
  view: WorkspaceView;
  expandedBotIds: Record<string, true>;
  panelRef: RefObject<PanelHandle | null>;
}

/** 收口现有 Human→Bot 交互式单聊，避免 Workspace 根 Hook 同时承担两种直接会话模型。 */
export function useHumanBotConversation({
  activeIdentityId,
  isUserIdentity,
  view,
  expandedBotIds,
  panelRef,
}: UseHumanBotConversationOptions) {
  const owned = useOwnedBots(activeIdentityId, isUserIdentity);
  const friends = useFriendBots(activeIdentityId, isUserIdentity, isUserIdentity && view !== 'group');
  const allChatBots = useMemo(
    () => [
      ...owned.chatBots,
      ...friends.friendBots.filter((bot) => !owned.chatBots.some((mine) => mine.botId === bot.botId)),
    ],
    [friends.friendBots, owned.chatBots],
  );
  const expandedBotIdList = useMemo(() => Object.keys(expandedBotIds), [expandedBotIds]);
  const botSessions = useBotSessions(
    allChatBots,
    expandedBotIdList,
    activeIdentityId,
    owned.isLoading || friends.isLoading,
  );
  const selectedChatBot = useMemo(
    () => findSelectedChatBot(allChatBots, botSessions.selectedSession?.botId, expandedBotIds),
    [allChatBots, botSessions.selectedSession?.botId, expandedBotIds],
  );
  const botChat = useBotChat(selectedChatBot, botSessions.selectedSession, panelRef);

  return {
    chatBots: owned.chatBots,
    hasAgentCodingBots: owned.hasAgentCodingBots,
    isMyBotsLoading: owned.isLoading,
    myBotsError: owned.error,
    reloadMyBots: owned.reload,
    friendBots: friends.friendBots,
    isFriendBotsLoading: friends.isLoading,
    friendBotsError: friends.error,
    reloadFriendBots: friends.reload,
    allChatBots,
    botSessions,
    selectedChatBot,
    botChat,
  };
}
