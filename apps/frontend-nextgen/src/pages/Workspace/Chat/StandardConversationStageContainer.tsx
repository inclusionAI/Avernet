import type { ConversationBotSection } from '@/domain/conversation/types';
import { buildSingleChatBridgeRequest } from '@/hooks/singleChatBridgeRequest';
import { buildBotChatTarget } from '@/hooks/workspaceIdentityMapper';
import type { HumanIdentity } from '@/capabilities';
import { useConversationViewer } from './hooks/useConversationViewer';
import { useBotChat } from '../hooks/useBotChat';
import { useChatBridge } from '@tc-chat/adapters';
import type { BridgeInputRef, PanelHandle } from '@tc-chat/core';
import type { SenderRef } from '@tc-chat/ui/es/Sender';
import { useEffect, useMemo, useRef, type RefObject } from 'react';
import { chatBridge } from '@/services/workspace/chatBridge';
import type { BotChatSessionView, ChatBotView } from '@/services/workspace/botSessionService';
import { ChatHeaderActions } from './components/ChatHeaderActions';
import { ChatHeaderRightPanel, SessionDetailPanel } from './components/ChatHeaderRightPanel';
import { SessionHistorySearchPanel } from './components/SessionHistorySearchPanel';
import { ConversationInteractiveStage } from './ConversationInteractiveStage';
import { useChatEngineVersionNotice } from './hooks/useChatEngineVersionNotice';
import { useChatHeaderPanels } from './hooks/useChatHeaderPanels';
import type { ChatHeaderPanelKind } from './hooks/useChatHeaderPanels';
import { useConversationInteractiveChat } from './hooks/useConversationInteractiveChat';
import { useConversationSessionEdits } from './hooks/useConversationSessionEdits';
import type { ConversationSessionsModel } from './hooks/useConversationSessions.types';
import { useSessionSwitchReset } from './hooks/useSessionSwitchReset';
import { resolveConversationPresentation } from './conversationPresentation';

interface StandardConversationStageContainerProps {
  bot: ChatBotView | null;
  session: BotChatSessionView | null;
  human: HumanIdentity | null;
  section: ConversationBotSection;
  /** 全量可单聊 Bot(管理 + 好友,模型选择器用)。 */
  chatBots: ChatBotView[];
  /** 对话页会话缓存/收藏/操作模型。 */
  sessions: ConversationSessionsModel;
  onOpenSessionList(): void;
  /** 把普通 Runtime 的请求中状态回传给页面，用于侧边栏当前会话 loading。 */
  onChatRequestingChange?(isRequesting: boolean): void;
}

/**
 * 标准 Chat Runtime 容器；好友 Coding Bot 临时通过展示能力配置复用该容器。
 *
 * 自有 Coding Bot 仍必须和 CodingBotStageBoundary 互斥挂载，避免普通 Runtime 与
 * AIXCoding 子应用同时初始化。
 */
export function StandardConversationStageContainer({
  bot,
  session,
  human,
  section,
  chatBots,
  sessions,
  onOpenSessionList,
  onChatRequestingChange,
}: StandardConversationStageContainerProps): JSX.Element {
  const userId = human?.userId ?? null;
  const panelRef = useRef<PanelHandle | null>(null);
  const inputRef = useRef<SenderRef | null>(null);
  const botChat = useBotChat(bot, session, panelRef, undefined, userId);

  const engineVersionNotice = useChatEngineVersionNotice({
    bot,
    section,
    origin: 'mine',
    sessionId: botChat.historyReady === false ? null : session?.sessionId ?? null,
    userId,
  });
  const target = useMemo(() => (bot ? buildBotChatTarget(bot, session) : null), [bot, session]);
  const presentation = useMemo(
    () => resolveConversationPresentation(bot, botChat.connectionRuntime),
    [bot, botChat.connectionRuntime],
  );

  useEffect(() => {
    onChatRequestingChange?.(botChat.chat.isRequesting);
    return () => onChatRequestingChange?.(false);
  }, [botChat.chat.isRequesting, onChatRequestingChange]);
  const sessionEdits = useConversationSessionEdits(userId);
  const viewer = useConversationViewer(human);

  const interactiveModel = useConversationInteractiveChat({
    target,
    viewer,
    authenticatedUserId: userId,
    authenticatedUserName: human?.displayName,
    authenticatedUserAvatarUrl: human?.avatarUrl,
    requesterIdentityId: userId,
    chatBots,
    selectedSession: session,
    botChat,
    panelRef,
    inputRef,
    sessions: sessionEdits,
    presentation,
  });

  // 副屏桥注册:对齐旧 useWorkspace(bridge.submit → bot 单聊 chat.onRequest)。
  useChatBridge({
    bridge: chatBridge,
    chat: botChat.chat,
    panelRef,
    inputRef: inputRef as RefObject<BridgeInputRef | null>,
    buildRequestParams: (content) => buildSingleChatBridgeRequest(target?.id ?? null, content),
  });

  // /file 等面板唤起后切会话:输入清空/失效 chip/Escape 收起子面板。
  useSessionSwitchReset(session?.sessionId ?? null, interactiveModel.fileFeature.senderRef);

  // 头部五图标域编排（chat-header-panels）：收藏会话/会话管理/历史消息/资源管理/副屏。
  const headerPanels = useChatHeaderPanels({
    historyReady: botChat.historyReady,
    session: interactiveModel.selectedSession,
    userId,
    messages: botChat.chat.messages,
    panelRef,
    section,
    botId: bot?.botId ?? null,
    toggleFavorite: sessions.favorites.toggleFavorite,
    favoritePending: sessions.favorites.isPending,
    openFileDrawer: interactiveModel.fileFeature.openFileDrawer,
    closeFileDrawer: interactiveModel.fileFeature.closeFileDrawer,
    fileDrawerOpen: interactiveModel.fileFeature.fileDrawerOpen,
    sidePaneEnabled: presentation.sidePane,
  });
  const toggleFileDrawer = () => {
    if (!presentation.resourceManager) return;
    if (interactiveModel.fileFeature.fileDrawerOpen) interactiveModel.fileFeature.closeFileDrawer();
    else {
      headerPanels.closePanel();
      // 副屏让位：副屏为 AppShell 悬浮层，展开时整条覆盖右缘面板让位区（chat-header-panels 互斥）。
      headerPanels.dismissSidePaneForPanels();
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
      showResourceManager={presentation.resourceManager}
      sidePaneOpen={headerPanels.sidePaneOpen}
      showSidePane={presentation.sidePane}
      sidePaneDisabled={headerPanels.sidePaneDisabled}
      onToggleSidePane={headerPanels.toggleSidePane}
      notice={engineVersionNotice.notice}
    />
  );
  // 头部右缘内联副屏（chat-header-panels）：单容器常挂、经 open 开合对话区让宽（与 SDK 副屏
  // 同构，无抽屉蒙层）；renderPanelKind 记住最近一次打开的面板，收起过渡期间内容不闪切。
  const openPanel = headerPanels.openPanel;
  const renderPanelKindRef = useRef<ChatHeaderPanelKind>('detail');
  if (openPanel) renderPanelKindRef.current = openPanel;
  const renderPanelKind = openPanel ?? renderPanelKindRef.current;
  const headerRightPanel = (
    <ChatHeaderRightPanel
      open={openPanel !== null}
      title={renderPanelKind === 'history' ? '历史消息' : '会话详情'}
      onClose={headerPanels.closePanel}
    >
      {renderPanelKind === 'history' ? (
        <SessionHistorySearchPanel search={headerPanels.historySearch} onLocate={headerPanels.locateMessage} />
      ) : (
        <SessionDetailPanel session={interactiveModel.selectedSession} />
      )}
    </ChatHeaderRightPanel>
  );

  return (
    <>
      <ConversationInteractiveStage
        model={interactiveModel}
        onOpenSessionList={onOpenSessionList}
        headerActions={headerActions}
        headerRightPanels={headerRightPanel}
        highlightMessageId={headerPanels.highlightMessageId}
        sidePaneOpen={headerPanels.sidePaneOpen}
        onToggleSidePane={headerPanels.toggleSidePane}
        onSidePaneIsOpenChange={headerPanels.onSidePaneIsOpenChange}
      />
      {engineVersionNotice.dialogs}
    </>
  );
}
