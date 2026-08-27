import type { SessionView } from '@/domain/collaboration';
import { queryAndRegisterManifestLibraryCdn } from '@/services/bcs/libraryCdnInjector';
import { chatBridge } from '@/services/workspace/chatBridge';
import type { SessionMessageAttachment } from '@/services/workspace/groupChatAttachmentService';
import { createGroupChatProvider, type GroupChatState } from '@/services/workspace/groupChatProvider';
import { resolveGroupWsOrigin } from '@/services/workspace/groupChatProviderHelpers';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { useChat, useChatBridge, type ProviderConnectionStatus } from '@tc-chat/adapters';
import type { BridgeInputRef, ChatMessage, PanelHandle } from '@tc-chat/core';
import type { SenderRef } from '@tc-chat/ui/es/Sender/types';
import { useCallback, useEffect, useMemo, useRef, useState, type RefObject } from 'react';
import { toast } from 'sonner';
import {
  buildEchoAttachments,
  buildGroupChatBridgeRequest,
  buildGroupUserMessageExtra,
} from './groupChatRequestBuilder';

/**
 * useGroupChat —— 协作群对话 Hook。
 *
 * 是基于 SDK `useChat` 与 `createGroupChatProvider` 的薄包装：
 * - 透传 `chat` (useChat 结果) 供 GroupChatPane SDK UI 直接消费
 * - 透传 `supportState` (Provider 阶段) 与 `connectionStatus` (WebSocket 连接 5 态)
 * - 暴露 `send/stop/reconnect` 命令，仅在 Hook 内做最小裁剪（trim、isRequesting 短路）
 * - 不拼装会话显示字段（业务字段层由调用方 / 组件负责）
 *
 * 消息加载：每次切换会话（sessionId 变化）时显式调用 provider.loadHistory()
 * （即 GET /openapi/v1/collaboration/sessions/{sid}/messages），将完整 ChatMessage
 * 直接灌入 SDK chat.messages，绕过 SDK defaultMessages 的字段裁剪（保留 createdAt 等）。
 *
 * Provider 生命周期：mount 或 sessionId 变更 → connect；unmount 或 sessionId 变更 → disconnect。
 * 入参为 SessionView（而非裸 sessionId）：BCS connect 帧需要 group_id，与 sessionId 一并注入 Provider。
 */
export function useGroupChat(session: SessionView | null) {
  const identityId = useWorkspaceStore((s) => s.activeIdentityId);
  const historyRefreshNonce = useWorkspaceStore((s) => s.historyRefreshNonce);
  const sessionId = session?.sessionId ?? null;
  const groupId = session?.groupId ?? null;

  // 副屏命令式 handle（命令式 openTab/closePanelForce；对齐单聊 useWorkspace 的 panelRef）。
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
  const [hasMoreHistory, setHasMoreHistory] = useState(false);
  const [isLoadingMoreHistory, setIsLoadingMoreHistory] = useState(false);

  // Provider 依赖 sessionId + groupId + identityId；任一缺失则不创建（Hook 进入空闲态）。
  // wsOrigin 部署态直连网关（绕过 tern cors proxy 的 WS 盲区）；dev 返回 undefined 走同源 dev proxy。
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
    buildRequestParams: (content, extra) => buildGroupChatBridgeRequest(sessionId ?? '', content, extra),
  });

  // Provider 生命周期：仅在 sessionId 真正变化时 connect，stop 旧会话 disconnect。
  const prevSessionIdRef = useRef<string | null>(null);
  useEffect(() => {
    if (!provider || !sessionId) return;
    if (prevSessionIdRef.current === sessionId) return;
    prevSessionIdRef.current = sessionId;
    // 切换会话：清空副屏 tab，避免旧会话副屏残留。
    panelRef.current?.closePanelForce();
    provider.connect().catch((error: unknown) => {
      toast.error(error instanceof Error ? error.message : '协作连接失败');
    });
    return () => {
      prevSessionIdRef.current = null;
      void provider.disconnect();
    };
  }, [provider, sessionId]);

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

  // 副屏方式②数据桥：进入协作群会话即拉 BCS manifest，写 window.aixLibraryCdnMap，
  // 供引擎 resolveBusinessEntry 把 <AixUI component="lib.X"> 的 lib 名解析成 CDN URL（对应 ocb GroupChatPage）。
  // 不阻塞会话主流程（拉取失败引擎侧方式②不可用，不影响消息收发与方式①③）。
  useEffect(() => {
    if (!sessionId) return;
    void queryAndRegisterManifestLibraryCdn();
  }, [sessionId]);

  // 每次切换会话（sessionId 或 provider 变化）显式拉取历史消息并灌入 SDK chat。
  // loadHistory 内部调用 GET /openapi/v1/collaboration/sessions/{sid}/messages，
  // 并在加载期间切换 supportState.phase（loading-history → ready/idle）供 UI 展示骨架。
  useEffect(() => {
    if (!provider || !sessionId) return;
    let cancelled = false;
    // 切换会话：重置向上翻页状态，避免旧会话的 hasMore / loading 残留到新会话。
    setHasMoreHistory(false);
    setIsLoadingMoreHistory(false);
    provider
      .loadHistory()
      .then((history: ChatMessage[]) => {
        if (cancelled) return;
        chat.setMessages(history);
        if (!cancelled) setHasMoreHistory(provider.hasMoreHistory);
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        toast.error(error instanceof Error ? error.message : '加载历史消息失败');
      });
    return () => {
      cancelled = true;
    };
    // chat.setMessages 来自 useChat，回调引用稳定（useCallback）；不纳入依赖避免重复触发。
    // historyRefreshNonce：点击会话 tab 时递增，即使是同一会话也强制重新拉取历史。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [provider, sessionId, historyRefreshNonce]);

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
        extra: buildGroupUserMessageExtra(echoAttachments),
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

  // 重新加载会话历史：直连 provider.loadHistory() 并把结果灌进 SDK chat.messages。
  // 组件不直接调 Provider；error 状态下 GroupChatPane 的「重新加载历史」走此出口。
  const reloadHistory = async () => {
    if (!provider || !sessionId) return;
    try {
      const history: ChatMessage[] = await provider.loadHistory();
      chat.setMessages(history);
      setHasMoreHistory(provider.hasMoreHistory);
      setIsLoadingMoreHistory(false);
    } catch (error: unknown) {
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
      if (older.length > 0) {
        chat.setMessages((prev) => {
          const ids = new Set(prev.map((message) => message.id));
          const fresh = older.filter((message) => !ids.has(message.id));
          return fresh.length > 0 ? [...fresh, ...prev] : prev;
        });
      }
      setHasMoreHistory(provider.hasMoreHistory);
    } catch (error: unknown) {
      setHasMoreHistory(provider.hasMoreHistory);
      toast.error(error instanceof Error ? error.message : '加载更多历史消息失败');
    } finally {
      setIsLoadingMoreHistory(false);
    }
  };
  // 副屏 <AixUI> 直发 chat.onRequest 绕开全局桥 last-wins（修复群 execute 串到单聊 bot），等价桥路径 + isInject 静默。
  const submitPanelMessage = useCallback(
    (content: string) => {
      if (sessionId) chat.onRequest(buildGroupChatBridgeRequest(sessionId, content, { isInject: true }));
    },
    [chat, sessionId],
  );

  return {
    chat,
    panelRef,
    inputRef,
    chatBridge,
    supportState,
    connectionStatus,
    send,
    stop,
    submitPanelMessage,
    reconnect,
    reloadHistory,
    hasMoreHistory,
    isLoadingMoreHistory,
    loadMoreHistory,
  };
}
