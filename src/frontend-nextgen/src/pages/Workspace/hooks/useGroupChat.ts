import type { SessionView } from '@/domain/collaboration';
import { useTaskPreflightAssistant } from '@/hooks/useTaskPreflightAssistant';
import { chatBridge } from '@/services/workspace/chatBridge';
import type { SessionMessageAttachment } from '@/services/workspace/groupChatAttachmentService';
import { createGroupChatProvider, type GroupChatState } from '@/services/workspace/groupChatProvider';
import { resolveGroupWsOrigin } from '@/services/workspace/groupChatProviderHelpers';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { useChat, useChatBridge, type ProviderConnectionStatus } from '@tc-chat/adapters';
import type { BridgeInputRef, ChatMessage, PanelHandle } from '@tc-chat/core';
import type { SenderRef } from '@tc-chat/ui/es/Sender/types';
import { useCallback, useEffect, useMemo, useRef, useState, type RefObject } from 'react';
import { useConnectionStatusSmoothing } from './useConnectionStatusSmoothing';
import { toast } from 'sonner';
import {
  buildEchoAttachments,
  buildGroupChatBridgeRequest,
  buildGroupUserMessageExtra,
} from './groupChatRequestBuilder';
import { prependUniqueMessages } from './groupChatHistoryUtils';
import { useGroupBootstrapProcessing } from './useGroupBootstrapProcessing';
import { useManifestHistoryLoader } from './useManifestHistoryLoader';

/**
 * useGroupChat —— 协作群对话 Hook。
 *
 * 是基于 SDK `useChat` 与 `createGroupChatProvider` 的薄包装：
 * - 透传 `chat` (useChat 结果) 供 GroupChatPane SDK UI 直接消费
 * - 透传 `supportState` (Provider 阶段) 与 `connectionStatus` (WebSocket 连接 5 态)
 * - 暴露 `send/stop/reconnect` 命令，仅在 Hook 内做最小裁剪（trim、isRequesting 短路）
 * - 不拼装会话显示字段（业务字段层由调用方 / 组件负责）
 *
 * Provider 连接、history hydration 与 WS 暂存由 useManifestHistoryLoader 统一协调；
 * SessionView 同时提供 BCS 所需的 group_id 和 session_id。
 */
export function useGroupChat(session: SessionView | null) {
  const identityId = useWorkspaceStore((s) => s.activeIdentityId);
  const activeIdentity = useWorkspaceStore(
    (s) => s.identities.find((identity) => identity.id === s.activeIdentityId) ?? null,
  );
  const historyRefreshNonce = useWorkspaceStore((s) => s.historyRefreshNonce);
  const sessionId = session?.sessionId ?? null;
  const groupId = session?.groupId ?? null;

  const panelRef = useRef<PanelHandle>(null);
  // 主屏输入框 ref（SenderRef）：经 useChatBridge.setInputRef 注册到全局桥,卡片填输入框时
  // bridge.getInputRef().insert(text) 生效。SenderRef ⊇ BridgeInputRef,见下方 useChatBridge 调用 as 转换。
  const inputRef = useRef<SenderRef | null>(null);
  // 主→副事件通道：全局单例 ChatBridge（services/workspace/chatBridge.ts，installGlobal:true）。
  // <ChatLayout.Panel bridge={chatBridge}> 注入使 emitPanelEvent 可达副屏 eventEmitter；
  // 全局单例同时是 aixcore 卡片沙箱 window.aixBridge 的真桥来源，避免沙箱退化成空 {}。
  // useChatBridge 集中在此注册一次 submit/abort（不在 useBotChat 内接，见 useWorkspace 集中注册注释）。

  const [supportState, setSupportState] = useState<GroupChatState>({ phase: 'idle', error: null });
  const [connectionStatus, setConnectionStatus] = useState<ProviderConnectionStatus>('disconnected');
  const smoothedConnectionStatus = useConnectionStatusSmoothing(connectionStatus);
  const [hasMoreHistory, setHasMoreHistory] = useState(false);
  const [isLoadingMoreHistory, setIsLoadingMoreHistory] = useState(false);

  // Provider 依赖 sessionId + groupId + identityId；任一缺失则不创建（Hook 进入空闲态）。
  const provider = useMemo(() => {
    if (!sessionId || !groupId || !identityId) return null;
    return createGroupChatProvider({ sessionId, groupId, identityId, wsOrigin: resolveGroupWsOrigin() });
  }, [sessionId, groupId, identityId]);

  const chat = useChat({
    // provider 为 null 时 Hook 仍需调用 useChat 以保持 Hook 数量稳定；这里以 never 兜底，
    // 实际当 provider 为 null 时 sendMessage 等命令不会触发（调用方不应渲染对话面板）。
    provider: (provider ?? null) as never,
    conversationKey: sessionId ?? undefined,
    // 不使用 SDK defaultMessages（其内部会裁剪 id/createdAt 等字段导致历史消息无法正常展示），
    // 改由下方 effect 显式 loadHistory + setMessages，保留完整 ChatMessage 语义。
    placeholderMessage: '',
    panelRef,
  });
  const groupBootstrapProcessing = useGroupBootstrapProcessing({
    groupId,
    sessionId,
    messages: chat.messages,
    supportPhase: supportState.phase,
  });

  // 集中注册 submit/abort/getMessages 到全局单例桥，使 aixcore 卡片沙箱的 bridge.sendMessage 经
  // ChatBridge.submit → 此处注册的 handler → chat.onRequest 注入对话流（断点 B 修复）。
  // inputRef: SenderRef ⊇ BridgeInputRef,as 转换规避 Ref 不变性问题(SDK hook 形参 RefObject<BridgeInputRef>)。
  // buildRequestParams（根因 A/C 修复）：SDK 默认实现仅产 {userMessage:{content}} 无顶层 content → ws 帧 message 丢；
  //   显式产顶层 content + 透传 botUuid/mentionAll/replyTo/panelContext/isInject,使 provider.request 组装完整 ws 帧。
  useChatBridge({
    bridge: chatBridge,
    chat,
    panelRef,
    inputRef: inputRef as RefObject<BridgeInputRef | null>,
    buildRequestParams: (content, extra) =>
      buildGroupChatBridgeRequest(sessionId ?? '', content, extra, {
        senderId: activeIdentity?.id ?? undefined,
        senderName: activeIdentity?.displayName ?? undefined,
        senderAvatarUrl: activeIdentity?.avatarUrl,
      }),
  });

  // 订阅 Provider 阶段状态 + WebSocket 连接状态。
  useEffect(() => {
    if (!provider) return;
    const offState = provider.subscribeToSupportState(setSupportState);
    const offStatus = provider.subscribeToConnectionStatus((event: { status: ProviderConnectionStatus }) =>
      setConnectionStatus(event.status),
    );
    return () => {
      offState();
      offStatus();
    };
  }, [provider]);

  // 切换会话时清理旧会话副屏；连接、history hydration 与 WS 暂存由
  // useManifestHistoryLoader 作为同一个可取消初始化流程统一管理。
  useEffect(() => {
    if (!provider || !sessionId) return;
    panelRef.current?.closePanelForce();
  }, [provider, sessionId]);

  useManifestHistoryLoader({
    provider,
    sessionId,
    historyRefreshNonce,
    setHasMoreHistory,
    setIsLoadingMoreHistory,
    setMessages: chat.setMessages,
  });

  // 重新进入会话时合并本地持久化的前置 assistant 消息（已抽入 useTaskPreflightAssistant）。
  const { appendAssistantMessage, streamAssistantMessage } = useTaskPreflightAssistant({
    chat,
    sessionKey: sessionId ?? undefined,
    mergePersistedHistory: true,
  });

  const send = (text: string, mentions?: string[], attachments?: SessionMessageAttachment[]) => {
    const hasText = text.trim().length > 0;
    const hasAttachments = !!attachments && attachments.length > 0;
    if (!sessionId || (!hasText && !hasAttachments) || chat.isRequesting) return;
    const trimmed = text.trim();
    // 本地回显附件：share_url 用于跨端/免鉴权分发，`<img>` 本地回显改走会话内容地址避免分享域名/CORS 加载失败。
    // 与桥路径 buildGroupChatBridgeRequest 共用 buildEchoAttachments/buildGroupUserMessageExtra（O3 回显一致）。
    const echoAttachments = buildEchoAttachments(sessionId, attachments);
    chat.onRequest({
      content: trimmed,
      sessionId,
      ...(mentions && mentions.length > 0 ? { mentions } : {}),
      ...(attachments && attachments.length > 0 ? { attachments } : {}),
      userMessage: {
        content: trimmed,
        extra: buildGroupUserMessageExtra(echoAttachments, {
          senderId: activeIdentity?.id ?? undefined,
          senderName: activeIdentity?.displayName ?? undefined,
          senderAvatarUrl: activeIdentity?.avatarUrl,
        }),
      },
    });
  };

  const stop = () => {
    if (!chat.isRequesting) return;
    chat.abort();
  };

  const reconnect = async () => {
    if (!provider) return;
    try {
      await provider.reconnect();
    } catch (error: unknown) {
      toast.error(error instanceof Error ? error.message : '重连失败');
    }
  };

  const reloadHistory = async () => {
    if (!provider || !sessionId) return;
    provider.beginHistoryHydration();
    try {
      if (!provider.isConnected) await provider.connect();
      const history: ChatMessage[] = await provider.loadHistory();
      chat.setMessages(history);
      setHasMoreHistory(provider.hasMoreHistory);
      setIsLoadingMoreHistory(false);
      provider.enterLiveMode();
    } catch (error: unknown) {
      provider.enterLiveMode();
      toast.error(error instanceof Error ? error.message : '加载历史消息失败');
    }
  };

  // 向上翻页加载更早的历史消息：provider 以当前最旧时间戳为 before 游标拉取上一页，
  // 这里按 id 去重后前置拼接到 SDK chat.messages（旧→新升序），并由 BubbleList
  // 在前置插入后保持滚动位置。hasMore / isLoadingMore 同步自 provider 供 UI 显隐。
  const loadMoreHistory = async () => {
    if (!provider || !sessionId) return;
    if (isLoadingMoreHistory || !hasMoreHistory) return;
    setIsLoadingMoreHistory(true);
    try {
      const older: ChatMessage[] = await provider.loadMoreHistory();
      if (older.length > 0) chat.setMessages((prev) => prependUniqueMessages(prev, older));
      setHasMoreHistory(provider.hasMoreHistory);
    } catch (error: unknown) {
      setHasMoreHistory(provider.hasMoreHistory);
      toast.error(error instanceof Error ? error.message : '加载更多历史消息失败');
    } finally {
      setIsLoadingMoreHistory(false);
    }
  };
  const submitPanelMessage = useCallback(
    (content: string) => {
      if (sessionId)
        chat.onRequest(
          buildGroupChatBridgeRequest(
            sessionId,
            content,
            { isInject: true },
            {
              senderId: activeIdentity?.id ?? undefined,
              senderName: activeIdentity?.displayName ?? undefined,
              senderAvatarUrl: activeIdentity?.avatarUrl,
            },
          ),
        );
    },
    [activeIdentity, chat, sessionId],
  );

  return {
    chat,
    panelRef,
    inputRef,
    chatBridge,
    supportState,
    connectionStatus: smoothedConnectionStatus,
    send,
    stop,
    submitPanelMessage,
    appendAssistantMessage,
    streamAssistantMessage,
    reconnect,
    reloadHistory,
    hasMoreHistory,
    isLoadingMoreHistory,
    loadMoreHistory,
    groupBootstrapProcessing,
  };
}
