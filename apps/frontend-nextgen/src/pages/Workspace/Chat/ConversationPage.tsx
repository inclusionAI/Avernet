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
import {
  ConversationSidebar,
  ConversationSidebarContent,
  type ConversationSidebarProps,
} from './components/ConversationSidebar';
import { ConversationInteractiveStage } from './ConversationInteractiveStage';
import { useConversationDirectory } from './hooks/useConversationDirectory';
import { useConversationHistory } from './hooks/useConversationHistory';
import { useConversationInteractiveChat } from './hooks/useConversationInteractiveChat';
import { useConversationSelection } from './hooks/useConversationSelection';
import { useConversationSessionEdits } from './hooks/useConversationSessionEdits';
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
  useEffect(() => {
    const previousUserId = previousUserIdRef.current;
    previousUserIdRef.current = userId;
    if (previousUserId !== null && userId !== null && previousUserId !== userId) {
      useConversationStore.getState().reset();
    }
  }, [userId]);

  const directory = useConversationDirectory(userId);
  const sessions = useConversationSessions({
    userId,
    managedBots: directory.managedBots,
    friendBots: directory.friendBots,
  });
  const others = useManagedBotOthers({
    userId,
    managedBots: directory.managedBots,
    enabled: true,
  });
  const hydrated = !directory.managedLoading && !directory.friendLoading;
  const { store, selection, origin, interactive, readonly, onRouteSelection, selectReadonlySession } =
    useConversationSelection({
      managedBots: directory.managedBots,
      friendBots: directory.friendBots,
      hydrated,
    });
  useConversationUrlSync({ hydrated, selection, onRouteSelection });

  // 交互式(mine)装配:target/viewer/认证信息/请求身份全部来自登录 Human(Task 7 约定)。
  const interactiveBot = interactive.bot;
  const selectedSession = interactive.session;
  const target = useMemo(
    () => (interactiveBot ? buildBotChatTarget(interactiveBot, selectedSession) : null),
    [interactiveBot, selectedSession],
  );
  const botChat = useBotChat(interactiveBot, selectedSession, panelRef);
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
    () => [...directory.managedBots.map((view) => view.bot), ...directory.friendBots.map((view) => view.bot)],
    [directory.managedBots, directory.friendBots],
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
    // AgentCoding Bot 不进对话目录(目录 Service 已剔除),入口统一收敛到 Bot 工坊。
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
    if (section === 'managed' && friendUserId) {
      selectReadonlySession(botId, sessionId, friendUserId);
      return;
    }
    if (section === 'friend') sessions.selectFriendBotSession(botId, sessionId);
    else sessions.selectMineSession(botId, sessionId);
  };
  const sidebarProps: ConversationSidebarProps = {
    managedBots: directory.managedBots,
    friendBots: directory.friendBots,
    store,
    directory,
    sessions,
    others,
    onOpenSession: handleOpenSession,
    onOpenPublicBots: () => history.push('/collaboration-square/bots'),
    onOpenBotWorkshop: () => history.push('/bot-workshop'),
  };
  const openAgentCodingBot = (bot: ChatBotView) =>
    history.push(buildAgentCodingChatPath({ botId: bot.botId, spaceId: bot.spaceId, spaceName: bot.spaceName }));

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
        ) : (
          <ConversationInteractiveStage
            model={interactiveModel}
            onOpenSessionList={() => setMobileListOpen(true)}
            onOpenAgentCodingBot={openAgentCodingBot}
          />
        )}
      </div>
    </div>
  );
}
