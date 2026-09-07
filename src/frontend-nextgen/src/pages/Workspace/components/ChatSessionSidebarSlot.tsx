import { Drawer, DrawerContent, DrawerTitle } from '@/components/ui';
import type { WorkspaceView } from '@/domain/collaboration/availableViews';
import { useWorkspace } from '@/hooks/useWorkspace';
import {
  BotSessionList,
  BotSessionSidebar,
  type BotSessionSidebarProps,
} from '@/pages/Workspace/components/BotSessionSidebar';

interface ChatSessionSidebarSlotProps {
  /** Reactive workspace model（chatBots / friendBots / botSessions 等会话侧栏所需数据）。 */
  workspace: ReturnType<typeof useWorkspace>;
  view: WorkspaceView;
  onViewChange: (v: WorkspaceView) => void;
  availableViews: WorkspaceView[];
  /** <lg 会话列表抽屉开关。≥lg 内流侧栏始终渲染，抽屉仅 <lg 由移动端顶部的「打开会话列表」触发。 */
  mobileListOpen: boolean;
  onMobileListClose: () => void;
  onOpenPublicBots: () => void;
  onOpenBotWorkshop?: () => void;
}

/**
 * 聊天视图一级/二级会话侧栏容器：内流 BotSessionSidebar（≥lg）与 <lg 抽屉复用同一份
 * botSidebarProps，避免两处分叉；抽屉内选中会话后收起。从 WorkspacePage 抽出以控制页面体量。
 */
export function ChatSessionSidebarSlot({
  workspace,
  view,
  onViewChange,
  availableViews,
  mobileListOpen,
  onMobileListClose,
  onOpenPublicBots,
  onOpenBotWorkshop,
}: ChatSessionSidebarSlotProps) {
  const botSessions = workspace.botSessions;
  const botSidebarProps: BotSessionSidebarProps = {
    view,
    onViewChange,
    availableViews,
    identities: workspace.identities,
    activeIdentityId: workspace.activeIdentityId,
    chatBots: workspace.chatBots,
    hasAgentCodingBots: workspace.hasAgentCodingBots,
    friendBots: workspace.friendBots,
    isMyBotsLoading: workspace.isMyBotsLoading,
    myBotsError: workspace.myBotsError,
    onRetryMyBots: workspace.reloadMyBots,
    isFriendBotsLoading: workspace.isFriendBotsLoading,
    friendBotsError: workspace.friendBotsError,
    onRetryFriendBots: workspace.reloadFriendBots,
    expandedBotIds: workspace.expandedBotIds,
    expandedBotSectionKey: workspace.expandedBotSectionKey,
    sessionsByBotId: botSessions.sessionsByBotId,
    favoriteSessionsByBotId: botSessions.favoriteSessionsByBotId,
    sessionPageMetaByBotId: botSessions.sessionPageMetaByBotId,
    favoriteSessionPageMetaByBotId: botSessions.favoriteSessionPageMetaByBotId,
    isSessionsLoading: botSessions.isSessionsLoading,
    selectedBotSessionId: botSessions.selectedBotSessionId,
    onToggleBotExpanded: workspace.onToggleBotExpanded,
    onSelectBot: workspace.onSelectAgentCodingBot,
    onSelectSession: botSessions.openSession,
    onCreateSession: (botId) => {
      const bot = [...workspace.chatBots, ...workspace.friendBots].find((b) => b.botId === botId);
      if (bot) void botSessions.createSession(bot);
    },
    onDeleteSession: async (botId, sessionId) => {
      const bot = [...workspace.chatBots, ...workspace.friendBots].find((b) => b.botId === botId);
      if (!bot) return false;
      return botSessions.deleteSession(bot, sessionId);
    },
    onRenameSession: async (botId, sessionId, title) => {
      const bot = [...workspace.chatBots, ...workspace.friendBots].find((b) => b.botId === botId);
      if (!bot) return false;
      return botSessions.renameSession(bot, sessionId, title);
    },
    onClearSessionContext: async (botId, sessionId) => {
      const bot = [...workspace.chatBots, ...workspace.friendBots].find((b) => b.botId === botId);
      if (!bot) return false;
      return botSessions.clearContext(bot, sessionId);
    },
    onToggleFavorite: (botId, sessionId) => botSessions.toggleFavorite(botId, sessionId),
    onLoadFavorites: (botId) => botSessions.loadFavoriteSessions(botId),
    onLoadMoreSessions: botSessions.loadMoreSessions,
    onOpenBotWorkshop,
    onOpenPublicBots,
  };

  return (
    <>
      <BotSessionSidebar {...botSidebarProps} />
      <Drawer
        open={mobileListOpen}
        onOpenChange={(open) => {
          if (!open) onMobileListClose();
        }}
      >
        <DrawerContent side="left" size="sm" showClose={false} bodyClassName="p-0 flex flex-col">
          <DrawerTitle className="sr-only">会话列表</DrawerTitle>
          <BotSessionList
            {...botSidebarProps}
            onSelectSession={(botId, sessionId) => {
              botSessions.openSession(botId, sessionId);
              onMobileListClose();
            }}
          />
        </DrawerContent>
      </Drawer>
    </>
  );
}
