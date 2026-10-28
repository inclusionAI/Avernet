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
import { useConversationInteractiveChat } from './hooks/useConversationInteractiveChat';
import { useConversationSessionEdits } from './hooks/useConversationSessionEdits';
import type { ConversationSessionsModel } from './hooks/useConversationSessions.types';
import { useSessionSwitchReset } from './hooks/useSessionSwitchReset';

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
 * 普通 OpenClaw Bot 的 Chat Runtime 容器。
 *
 * 必须和 CodingBotStageBoundary 互斥挂载，保证选中 Coding Bot 时
 * useBotChat / useConversationInteractiveChat / useChatBridge 不会初始化。
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

  const engineVersionNotice = useChatEngineVersionNotice({
    bot,
    section,
    origin: 'mine',
    sessionId: session?.sessionId ?? null,
    userId,
  });
  const target = useMemo(() => (bot ? buildBotChatTarget(bot, session) : null), [bot, session]);
  const botChat = useBotChat(bot, session, panelRef, undefined, userId);

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
    session: interactiveModel.selectedSession,
    messages: botChat.chat.messages,
    panelRef,
    section,
    botId: bot?.botId ?? null,
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
      sidePaneDisabled={headerPanels.sidePaneDisabled}
      onToggleSidePane={headerPanels.toggleSidePane}
      notice={engineVersionNotice.notice}
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
    <>
      <ConversationInteractiveStage
        model={interactiveModel}
        onOpenSessionList={onOpenSessionList}
        headerActions={headerActions}
        headerRightPanels={headerRightPanels}
        highlightMessageId={headerPanels.highlightMessageId}
        sidePaneOpen={headerPanels.sidePaneOpen}
        onToggleSidePane={headerPanels.toggleSidePane}
        onSidePaneIsOpenChange={headerPanels.onSidePaneIsOpenChange}
      />
      {engineVersionNotice.dialogs}
    </>
  );
}
