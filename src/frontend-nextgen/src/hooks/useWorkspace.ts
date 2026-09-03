import { getCapabilities } from '@/capabilities';
import { getAvailableViews, type WorkspaceView } from '@/domain/collaboration/availableViews';
import { useTaskPreflightAssistant } from '@/hooks/useTaskPreflightAssistant';
import { useAgentCodingBotSelection } from '@/pages/Workspace/hooks/useAgentCodingBotSelection';
import { useBotChat } from '@/pages/Workspace/hooks/useBotChat';
import { useBotSessions } from '@/pages/Workspace/hooks/useBotSessions';
import { useFriendBots } from '@/pages/Workspace/hooks/useFriendBots';
import { useOwnedBots } from '@/pages/Workspace/hooks/useOwnedBots';
import { isTestUserIdentity, TEST_USER_SUPPORT_TARGET_ID, workspaceService } from '@/services/workspace';
import { chatBridge } from '@/services/workspace/chatBridge';
import type { ConversationTarget, SupportChatState } from '@/services/workspace/workspaceModel';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { useChat, useChatBridge } from '@tc-chat/adapters';
import type { BridgeInputRef, PanelAction, PanelHandle } from '@tc-chat/core';
import type { SenderRef } from '@tc-chat/ui/es/Sender/types';
import { useCallback, useEffect, useMemo, useRef, useState, type RefObject } from 'react';
import { toast } from 'sonner';
import { buildSingleChatBridgeRequest } from './singleChatBridgeRequest';
import { useHumanIdentity } from './useHumanIdentity';
import { useWorkspaceIdentityBootstrap } from './useWorkspaceIdentityBootstrap';
import { buildBotChatTarget, mapIdentityViewToIdentity } from './workspaceIdentityMapper';
export interface UseWorkspaceOptions {
  /**
   * 副屏 onAction(send_message) 的自定义回流。
   *
   * 产线默认走 sendMessage → chat.onRequest（真实对话 provider）。
   * 自测面板可注入只 toast/log 的回调，不触碰真实对话链路，便于零对话依赖自测 send_message 回流。
   * fill_input 不在此注入（始终走本地 setDraft）。
   */
  onPanelSend?: (content: string) => void;
}
export function useWorkspace(options: UseWorkspaceOptions = {}) {
  const {
    activeIdentityId,
    identities: identityViews,
    activeTargetId,
    search,
    view,
    collapsedGroups,
    expandedBotIds,
    expandedBotSectionKey,
    setActiveIdentity,
    setSearch,
    setView,
    toggleGroup,
  } = useWorkspaceStore();
  const [supportState, setSupportState] = useState<SupportChatState>({ phase: 'idle', error: null });
  const [draft, setDraft] = useState('');
  const { identity: humanIdentity } = useHumanIdentity();
  const panelRef = useRef<PanelHandle>(null);
  const inputRef = useRef<SenderRef | null>(null);
  const provider = useMemo(() => workspaceService.createProvider(), []);
  useWorkspaceIdentityBootstrap();
  const identities = useMemo(() => identityViews.map(mapIdentityViewToIdentity), [identityViews]);
  const activeIdentityView = useMemo(
    () => identityViews.find((i) => i.id === activeIdentityId) ?? null,
    [identityViews, activeIdentityId],
  );
  const availableViews = useMemo<WorkspaceView[]>(
    () => getAvailableViews(activeIdentityView ? { id: activeIdentityView.id, kind: activeIdentityView.kind } : null),
    [activeIdentityView],
  );
  useEffect(() => {
    if (!activeIdentityView) return;
    if (availableViews.length === 0) return;
    if (!availableViews.includes(view)) setView(availableViews[0]);
  }, [activeIdentityView, availableViews, view, setView]);
  const isTestUser = isTestUserIdentity(activeIdentityId);
  const isUserIdentity = activeIdentityView?.kind === 'user';
  const { chatBots, isLoading: isMyBotsLoading } = useOwnedBots(activeIdentityId, isUserIdentity);
  const { friendBots, isLoading: isFriendBotsLoading } = useFriendBots(
    activeIdentityId,
    isUserIdentity,
    view !== 'group',
  );
  const allChatBots = useMemo(
    () => [...chatBots, ...friendBots.filter((b) => !chatBots.some((m) => m.botId === b.botId))],
    [chatBots, friendBots],
  );
  const expandedBotIdList = useMemo(() => Object.keys(expandedBotIds), [expandedBotIds]);
  const botSessions = useBotSessions(allChatBots, expandedBotIdList, activeIdentityId);
  const selectedChatBot = useMemo(
    () => allChatBots.find((b) => b.botId === botSessions.selectedSession?.botId) ?? null,
    [allChatBots, botSessions.selectedSession],
  );
  const botChat = useBotChat(selectedChatBot, botSessions.selectedSession, panelRef);
  const isSupportTarget = isTestUser && activeTargetId === TEST_USER_SUPPORT_TARGET_ID;
  const chat = useChat({
    provider,
    conversationKey: isTestUser ? activeTargetId ?? undefined : undefined,
    defaultMessages: async (info) =>
      info?.conversationKey === TEST_USER_SUPPORT_TARGET_ID ? provider.loadHistory() : [],
    placeholderMessage: '',
    panelRef,
  });

  useChatBridge({
    bridge: chatBridge,
    chat: isSupportTarget ? chat : botChat.chat,
    panelRef,
    inputRef: inputRef as RefObject<BridgeInputRef | null>,
    buildRequestParams: (content) => buildSingleChatBridgeRequest(activeTargetId, content),
  });

  useEffect(() => {
    const unsubscribe = provider.subscribeToSupportState(setSupportState);
    return () => {
      unsubscribe();
    };
  }, [provider]);

  useEffect(() => {
    setDraft('');
    panelRef.current?.closePanelForce();
  }, [activeTargetId]);

  const brand = getCapabilities().getProductBrand().value; // 产品名经 capability 解析（Open=Avernet；internal=TeamClaw），不硬编码
  useEffect(() => {
    if (!isSupportTarget) return;
    provider.connect().catch((error) => {
      toast.error(error instanceof Error ? error.message : `${brand.name} 客服连接失败`);
    });
    return () => provider.disconnect();
  }, [isSupportTarget, provider, brand.name]);

  const { selectedAgentCodingBot, onSelectAgentCodingBot, onToggleBotExpanded } = useAgentCodingBotSelection({
    activeIdentityId,
    toggleBotExpanded: botSessions.toggleBotExpanded,
  });

  const handleSelectIdentity = (id: string) => {
    workspaceService.persistIdentity(id);
    setActiveIdentity(id);
  };

  const sendMessage = (content: string) => {
    const normalizedContent = content.trim();
    if (!normalizedContent || chat.isRequesting) return;
    if (!isTestUser || !activeTargetId) return;
    void chat.onRequest({
      content: normalizedContent,
      targetId: activeTargetId,
      userMessage: {
        content: normalizedContent,
        extra: { displayTime: new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }) },
      },
    });
  };

  const stopReply = () => {
    if (!chat.isRequesting) return;
    chat.abort();
    toast.info(`已停止 ${brand.name} 客服回复`);
  };

  const handlePanelAction = (action: PanelAction) => {
    if (action.type === 'fill_input') {
      setDraft(action.content);
      return;
    }
    if (options.onPanelSend) {
      options.onPanelSend(action.content);
      return;
    }
    sendMessage(action.content);
  };

  const botChatTarget = useMemo<ConversationTarget | null>(() => {
    if (isTestUser)
      return {
        id: TEST_USER_SUPPORT_TARGET_ID,
        name: '客服测试',
        avatar: 'TS',
        engine: 'OpenClaw',
        group: 'mine',
        status: 'available',
        summary: '测试客服',
        kind: 'single',
      } as ConversationTarget;
    if (!selectedChatBot) return null;
    return buildBotChatTarget(selectedChatBot);
  }, [isTestUser, selectedChatBot]);

  const submitPanelMessage = useCallback(
    (content: string) => {
      const activeChat = isSupportTarget ? chat : botChat.chat;
      activeChat.onRequest(buildSingleChatBridgeRequest(activeTargetId, content));
    },
    [isSupportTarget, chat, botChat.chat, activeTargetId],
  );

  const { appendAssistantMessage, streamAssistantMessage } = useTaskPreflightAssistant({
    chat: isSupportTarget ? chat : botChat.chat,
    sessionKey: isSupportTarget ? activeTargetId : botSessions.selectedSession?.sessionId,
  });

  return {
    identities,
    currentUserAvatarUrl: humanIdentity?.avatarUrl,
    activeIdentityId,
    activeIdentity: activeIdentityView,
    setActiveIdentityId: handleSelectIdentity,
    search,
    setSearch,
    view,
    setView,
    collapsedGroups,
    toggleGroup,
    availableViews,
    chatBots,
    friendBots,
    isMyBotsLoading,
    isFriendBotsLoading,
    expandedBotIds,
    expandedBotSectionKey,
    toggleBotExpanded: onToggleBotExpanded,
    botSessions,
    botChat,
    botChatTarget,
    selectedAgentCodingBot,
    onSelectAgentCodingBot,
    onToggleBotExpanded,
    isTestUser,
    supportMessages: chat.messages,
    supportIsRequesting: chat.isRequesting,
    supportIsLoadingMessages: chat.isDefaultMessagesRequesting,
    supportConnectionStatus: chat.connectionStatus,
    supportRetryCount: chat.retryCount,
    supportState,
    draft,
    setDraft,
    panelRef,
    inputRef,
    chatBridge,
    sendMessage,
    stopReply,
    submitPanelMessage,
    appendAssistantMessage,
    streamAssistantMessage,
    handlePanelAction,
    reconnect: () => isSupportTarget && chat.reconnect(),
    openHelp: () => toast.info('帮助中心将由宿主资源能力注入'),
  };
}
