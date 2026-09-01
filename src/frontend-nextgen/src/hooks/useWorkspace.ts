import { getAvailableViews, type WorkspaceView } from '@/domain/collaboration/availableViews';
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
  // 主屏输入框 ref(SenderRef⊇BridgeInputRef):经 useChatBridge.setInputRef 注册到全局桥,卡片填输入框生效;单聊/群聊均原生 Sender(forwardRef)绑定。design D2/O1。
  const inputRef = useRef<SenderRef | null>(null);
  // 主→副通道用全局单例 ChatBridge（services/workspace/chatBridge.ts，installGlobal:true）：
  // 它既是 <ChatLayout.Panel bridge> 的 emitPanelEvent 通道，也是 aixcore 卡片沙箱 window.aixBridge 的真桥来源。
  // useChatBridge 集中在下方注册一次，不在 useBotChat 内接——避免 support/bot 两个 useChat 在同一桥上 last-wins 覆盖（design D2）。
  const provider = useMemo(() => workspaceService.createProvider(), []);
  useWorkspaceIdentityBootstrap();

  // IdentityView[] → Identity[]（IdentityBar 仍以 prop 驱动，不感知 Service/Store 类型）
  const identities = useMemo(() => identityViews.map(mapIdentityViewToIdentity), [identityViews]);
  const activeIdentityView = useMemo(
    () => identityViews.find((i) => i.id === activeIdentityId) ?? null,
    [identityViews, activeIdentityId],
  );
  const availableViews = useMemo<WorkspaceView[]>(
    () => getAvailableViews(activeIdentityView ? { id: activeIdentityView.id, kind: activeIdentityView.kind } : null),
    [activeIdentityView],
  );

  // view 钳制安全网:setIdentities 初始装载或身份列表变化导致 view 越界时回退到首个可用 tab。
  useEffect(() => {
    // 身份尚未加载完成时 availableViews 仅为「协作群」安全默认值，
    // 若此时钳制会把 URL 直连的单聊 tab 误改到协作群，刷新后丢失原 session。
    if (!activeIdentityView) return;
    if (availableViews.length === 0) return;
    if (!availableViews.includes(view)) setView(availableViews[0]);
  }, [activeIdentityView, availableViews, view, setView]);

  const isTestUser = isTestUserIdentity(activeIdentityId);
  const isUserIdentity = activeIdentityView?.kind === 'user';
  const { chatBots, isLoading: isMyBotsLoading } = useOwnedBots(activeIdentityId, isUserIdentity);
  const { friendBots, isLoading: isFriendBotsLoading } = useFriendBots(activeIdentityId, isUserIdentity, view !== 'group');
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

  // 集中注册 submit/abort/inputRef 到全局单例桥(断点 B):按活跃方选 chat;buildRequestParams 产顶层 content(根因 A,见 singleChatBridgeRequest)。
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

  useEffect(() => {
    if (!isSupportTarget) return;
    provider.connect().catch((error) => {
      toast.error(error instanceof Error ? error.message : 'TeamClaw 客服连接失败');
    });
    return () => provider.disconnect();
  }, [isSupportTarget, provider]);

  // 切换身份:先持久化,再走 store.setActiveIdentity 触发 view 钳制。
  const handleSelectIdentity = (id: string) => {
    workspaceService.persistIdentity(id);
    setActiveIdentity(id);
  };

  // 测试用户发送:经客服 provider.request;真实用户的 bot 单聊由 useBotChat 承接,不走此入口。
  const sendMessage = (content: string) => {
    const normalizedContent = content.trim();
    if (!normalizedContent || chat.isRequesting) return;
    if (!isTestUser || !activeTargetId) return;
    void chat.onRequest({
      content: normalizedContent,
      targetId: activeTargetId,
      userMessage: {
        content: normalizedContent,
        extra: {
          displayTime: new Date().toLocaleTimeString('zh-CN', {
            hour: '2-digit',
            minute: '2-digit',
          }),
        },
      },
    });
  };

  const stopReply = () => {
    if (!chat.isRequesting) return;
    chat.abort();
    toast.info('已停止 TeamClaw 客服回复');
  };

  // 副屏 onAction 回流：fill_input → setDraft；send_message → 注入或真实发送。
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

  // 真实用户 bot 单聊目标(供 ChatPanel 渲染头部);纯映射下沉 workspaceIdentityMapper。
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

  // 副屏 <AixUI> 消息按当前活跃聊天直发 chat.onRequest，绕开全局单例桥 last-wins
  //（避免协作群 execute 串到单聊 bot）。与 useChatBridge 桥路径等价（同 chat + buildSingleChatBridgeRequest）。
  const submitPanelMessage = useCallback(
    (content: string) => {
      const activeChat = isSupportTarget ? chat : botChat.chat;
      activeChat.onRequest(buildSingleChatBridgeRequest(activeTargetId, content));
    },
    [isSupportTarget, chat, botChat.chat, activeTargetId],
  );

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
    toggleBotExpanded: botSessions.toggleBotExpanded,
    botSessions,
    botChat,
    botChatTarget,
    isTestUser,
    // 测试用户客服链路
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
    handlePanelAction,
    reconnect: () => isSupportTarget && chat.reconnect(),
    openHelp: () => toast.info('帮助中心将由宿主资源能力注入'),
  };
}
