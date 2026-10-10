// ConversationPage —— 对话页(/workspace/chat,Task 8 组合页)。
// 组合:useHumanIdentity(Task 4 定式:固定登录 Human 身份,经页边界注入)+
// Task 4 会话 Hook(目录/会话/他人只读/URL 同步)+ Task 6 对话侧栏 +
// Task 7 交互舞台 + 只读面板。不调 useWorkspace 数据源,也不按 activeIdentity.kind 分支
// (Bot 身份只读好友分支由 /workspace/chat 单一 Human 语义整体取代,旧分支 Task 10 退休)。
// 登录用户变化(非首次出现)时重置会话 Store:缓存按 botId 记忆、无用户来源,
// 防止上一用户的缓存/选中串号(控制器裁定,不在每次挂载时重置)。
import { Drawer, DrawerContent, DrawerTitle } from '@/components/ui';
import type { IdentityView } from '@/domain/collaboration/types';
import type { ConversationBotSection } from '@/domain/conversation/types';
import { isManagedConversationSection } from '@/domain/conversation/types';
import { buildSingleChatBridgeRequest } from '@/hooks/singleChatBridgeRequest';
import { useHumanIdentity } from '@/hooks/useHumanIdentity';
import { useMinWidth } from '@/hooks/useMediaQuery';
import { buildBotChatTarget } from '@/hooks/workspaceIdentityMapper';
import { buildAgentCodingChatPath } from '@/services/workspace';
import type { ChatBotView } from '@/services/workspace/botSessionService';
import { chatBridge } from '@/services/workspace/chatBridge';
import { useConversationStore } from '@/stores/conversationStore';
import { useChatBridge } from '@tc-chat/adapters';
import type { BridgeInputRef, PanelHandle } from '@tc-chat/core';
import type { SenderRef } from '@tc-chat/ui/es/Sender';
import { history } from '@umijs/max';
import { useEffect, useMemo, useRef, useState, type RefObject } from 'react';
import { ReadOnlyConversationPanel } from '../components/ReadOnlyConversationPanel';
import { useBotChat } from '../hooks/useBotChat';
import { ChatHeaderActions } from './components/ChatHeaderActions';
import { ConversationSessionPlaceholder } from './components/ConversationSessionPlaceholder';
import { ChatHeaderRightPanel, SessionDetailPanel } from './components/ChatHeaderRightPanel';
import {
  ConversationSidebar,
  ConversationSidebarContent,
  type ConversationSidebarProps,
} from './components/ConversationSidebar';
import { SessionHistorySearchPanel } from './components/SessionHistorySearchPanel';
import { ConversationInteractiveStage } from './ConversationInteractiveStage';
import { useChatHeaderPanels } from './hooks/useChatHeaderPanels';
import { useConversationDirectory } from './hooks/useConversationDirectory';
import { useConversationHistory } from './hooks/useConversationHistory';
import { useConversationInteractiveChat } from './hooks/useConversationInteractiveChat';
import { useConversationSelection } from './hooks/useConversationSelection';
import { useConversationSessionEdits } from './hooks/useConversationSessionEdits';
import { useConversationSessionPlaceholder } from './hooks/useConversationSessionPlaceholder';
import { useConversationSessions } from './hooks/useConversationSessions';
import { useConversationUrlSync } from './hooks/useConversationUrlSync';
import { useManagedBotOthers } from './hooks/useManagedBotOthers';

export default function ConversationPage(): JSX.Element {
  const { identity: human } = useHumanIdentity();
  const userId = human?.userId ?? null;
  const panelRef = useRef<PanelHandle | null>(null);
  const inputRef = useRef<SenderRef | null>(null);

  // 登录用户变化才整仓重置(不随挂载/重渲染触发);首次出现不算切换。
  const previousUserIdRef = useRef<string | null>(null);
  const identityChanged = previousUserIdRef.current !== null && previousUserIdRef.current !== userId;
  useEffect(() => {
    const previousUserId = previousUserIdRef.current;
    previousUserIdRef.current = userId;
    if (previousUserId !== null && previousUserId !== userId) {
      useConversationStore.getState().reset();
    }
  }, [userId]);

  const directory = useConversationDirectory(userId);
  // 团队 Bot 独立目录(section=team)与会话模型合并消费:
  // 会话加载/选中/URL 同步等按「管理域全集」(managed + team)装配
  // (sprint spec 2026-10-10-conversation-team-bots,取代 B2/B3 Batch 1 临时的空间拆桶分层)。
  const managedAndTeamBots = useMemo(
    () => [...directory.managedBots, ...directory.teamBots],
    [directory.managedBots, directory.teamBots],
  );
  const sessions = useConversationSessions({
    userId,
    managedBots: managedAndTeamBots,
    friendBots: directory.friendBots,
  });
  const placeholder = useConversationSessionPlaceholder({
    userId,
    managedBots: managedAndTeamBots,
    friendBots: directory.friendBots,
    sessions,
  });
  const others = useManagedBotOthers({
    userId,
    managedBots: managedAndTeamBots,
    enabled: true,
  });
  const hydrated = !directory.managedLoading && !directory.teamLoading && !directory.friendLoading;
  const { store, selection, origin, interactive, readonly, onRouteSelection, selectReadonlySession } =
    useConversationSelection({
      managedBots: managedAndTeamBots,
      friendBots: directory.friendBots,
      hydrated,
    });
  useConversationUrlSync({ hydrated, selection, onRouteSelection });

  // 交互式(mine)装配:target/viewer/认证信息/请求身份全部来自登录 Human(Task 7 约定)。
  // 登录用户切换瞬间不再装载旧用户会话(identityChanged 防串话,expand-selection 竞态保护)。
  const interactiveBot = identityChanged || !userId ? null : interactive.bot;
  const selectedSession = interactive.session;
  const target = useMemo(
    () => (interactiveBot ? buildBotChatTarget(interactiveBot, selectedSession) : null),
    [interactiveBot, selectedSession],
  );
  const botChat = useBotChat(interactiveBot, selectedSession, panelRef, undefined, userId);
  const sessionEdits = useConversationSessionEdits(userId);
  // viewer:登录 Human 的身份视图(kind 恒 user,是「查看者」而非可选身份)。
  const viewer = useMemo<IdentityView | null>(
    () =>
      human
        ? {
            id: human.userId,
            kind: 'user',
            displayName: human.displayName,
            avatarUrl: human.avatarUrl,
            online: human.online,
          }
        : null,
    [human],
  );
  const chatBots = useMemo(
    () => [...managedAndTeamBots.map((view) => view.bot), ...directory.friendBots.map((view) => view.bot)],
    [managedAndTeamBots, directory.friendBots],
  );

  const interactiveModel = useConversationInteractiveChat({
    target,
    viewer,
    authenticatedUserId: userId,
    authenticatedUserName: human?.displayName,
    authenticatedUserAvatarUrl: human?.avatarUrl,
    requesterIdentityId: userId,
    chatBots,
    selectedSession,
    botChat,
    panelRef,
    inputRef,
    // AgentCoding Bot 行点击直达 /coding/coding-chat(专属形态),不进 ChatPanel 装配。
    selectedAgentCodingBot: null,
    sessions: sessionEdits,
  });
  // 副屏桥注册:对齐旧 useWorkspace(bridge.submit → bot 单聊 chat.onRequest)。
  useChatBridge({
    bridge: chatBridge,
    chat: botChat.chat,
    panelRef,
    inputRef: inputRef as RefObject<BridgeInputRef | null>,
    buildRequestParams: (content) => buildSingleChatBridgeRequest(target?.id ?? null, content),
  });

  // 只读(others)装配:三元组不完整时 Hook 内部只清态不发请求。
  const selectionIsReadonly = origin === 'others' && store.selectedBotId !== null;
  const readonlyHistory = useConversationHistory({
    bot: selectionIsReadonly ? readonly.bot : null,
    friendUserId: selectionIsReadonly ? store.selectedFriendUserId : null,
    sessionId: selectionIsReadonly ? store.selectedSessionId : null,
  });

  // <lg 二级会话列表抽屉开关(桌面收起,对齐旧页行为)。
  const [mobileListOpen, setMobileListOpen] = useState(false);
  const isDesktop = useMinWidth(1024);
  useEffect(() => {
    if (isDesktop) setMobileListOpen(false);
  }, [isDesktop]);

  const handleOpenSession = (
    botId: string,
    section: ConversationBotSection,
    sessionId: string,
    friendUserId?: string,
  ) => {
    if (isManagedConversationSection(section) && friendUserId) {
      selectReadonlySession(botId, sessionId, friendUserId);
      return;
    }
    if (section === 'friend') sessions.selectFriendBotSession(botId, sessionId);
    else sessions.selectMineSession(botId, sessionId);
  };
  // AgentCoding Bot 行点击直达专用 coding 对话页(与 Bot 工坊「去使用」一致;spec 2026-10-09 G2)。
  const openAgentCodingBot = (bot: ChatBotView) =>
    history.push(buildAgentCodingChatPath({ botId: bot.botId, spaceId: bot.spaceId, spaceName: bot.spaceName }));
  const sidebarProps: ConversationSidebarProps = {
    managedBots: directory.managedBots,
    teamBots: directory.teamBots,
    friendBots: directory.friendBots,
    store,
    directory,
    sessions,
    others,
    onOpenSession: handleOpenSession,
    onOpenPublicBots: () => history.push('/collaboration-square/bots'),
    onOpenAgentCodingBot: openAgentCodingBot,
  };

  // 头部五图标域编排（chat-header-panels）：收藏会话/会话管理/历史消息/资源管理/副屏。
  // 资源管理 = 既有会话文件副屏承接；与右缘面板互斥由 togglePanel 一并处理。
  const headerPanels = useChatHeaderPanels({
    session: interactiveModel.selectedSession,
    messages: botChat.chat.messages,
    panelRef,
    section: store.selectedSection ?? 'managed',
    botId: interactiveBot?.botId ?? null,
    toggleFavorite: sessions.favorites.toggleFavorite,
    favoritePending: sessions.favorites.isPending,
    openFileDrawer: interactiveModel.fileFeature.openFileDrawer,
    closeFileDrawer: interactiveModel.fileFeature.closeFileDrawer,
    fileDrawerOpen: interactiveModel.fileFeature.fileDrawerOpen,
  });
  const toggleFileDrawer = () => {
    if (interactiveModel.fileFeature.fileDrawerOpen) interactiveModel.fileFeature.closeFileDrawer();
    else {
      headerPanels.closePanel();
      interactiveModel.fileFeature.openFileDrawer();
    }
  };
  const headerActions = (
    <ChatHeaderActions
      favorites={headerPanels.favorites}
      openPanel={headerPanels.openPanel}
      onTogglePanel={headerPanels.togglePanel}
      fileDrawerOpen={interactiveModel.fileFeature.fileDrawerOpen}
      onToggleFileDrawer={toggleFileDrawer}
      sidePaneOpen={headerPanels.sidePaneOpen}
      onToggleSidePane={headerPanels.toggleSidePane}
    />
  );
  const headerRightPanels =
    headerPanels.openPanel === 'detail' ? (
      <ChatHeaderRightPanel title="会话详情" onClose={headerPanels.closePanel}>
        <SessionDetailPanel session={interactiveModel.selectedSession} />
      </ChatHeaderRightPanel>
    ) : headerPanels.openPanel === 'history' ? (
      <ChatHeaderRightPanel title="历史消息" onClose={headerPanels.closePanel}>
        <SessionHistorySearchPanel search={headerPanels.historySearch} onLocate={headerPanels.locateMessage} />
      </ChatHeaderRightPanel>
    ) : null;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex min-h-0 flex-1">
        <ConversationSidebar {...sidebarProps} />
        {/* <lg 二级会话列表抽屉:与桌面端共用同一 Content,选中即收起。 */}
        <Drawer
          open={mobileListOpen}
          onOpenChange={(open) => {
            if (!open) setMobileListOpen(false);
          }}
        >
          <DrawerContent side="left" size="sm" showClose={false} bodyClassName="p-0 flex flex-col">
            <DrawerTitle className="sr-only">会话列表</DrawerTitle>
            <ConversationSidebarContent
              {...sidebarProps}
              onOpenSession={(botId, section, sessionId, friendUserId) => {
                handleOpenSession(botId, section, sessionId, friendUserId);
                setMobileListOpen(false);
              }}
            />
          </DrawerContent>
        </Drawer>
        {selectionIsReadonly ? (
          <ReadOnlyConversationPanel
            bot={readonly.bot}
            friend={readonly.friend}
            session={readonly.session}
            messages={readonlyHistory.messages}
            loading={readonlyHistory.loading}
            error={readonlyHistory.error}
            hasMore={readonlyHistory.hasMore}
            isLoadingMore={readonlyHistory.isLoadingMore}
            loadMoreError={readonlyHistory.loadMoreError}
            onRetry={readonlyHistory.retry}
            onLoadMore={() => void readonlyHistory.loadMore()}
            onOpenSessionList={() => setMobileListOpen(true)}
          />
        ) : placeholder ? (
          // 展开 Bot 后目标尚无选中会话:占位(加载/失败/空态/无效指定,expand-selection spec),
          // 不挂载旧对话。
          <ConversationSessionPlaceholder model={placeholder} onOpenSessionList={() => setMobileListOpen(true)} />
        ) : (
          <ConversationInteractiveStage
            model={interactiveModel}
            onOpenSessionList={() => setMobileListOpen(true)}
            onOpenAgentCodingBot={openAgentCodingBot}
            headerActions={headerActions}
            headerRightPanels={headerRightPanels}
            highlightMessageId={headerPanels.highlightMessageId}
          />
        )}
      </div>
    </div>
  );
}
