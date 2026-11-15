import { CANCEL_UI_TEXT, SEND_BAN_UI_TEXT } from '@/adapters/a2a/bootstrap';
import { clearBotCdnConfig, queryAndRegisterBotLibraryCdn } from '@/services/bcs/libraryCdnInjector';
import { listTaskPreflightMessages, mergeTaskPreflightMessages } from '@/services/tasks/taskPreflightMessageStore';
import { createBotChatProvider, isAicodingConnectionEngine, type BotChatState } from '@/services/workspace/botChatProvider';
import type { BotChatSessionView, ChatBotView } from '@/services/workspace/botSessionService';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { useChat, type ProviderConnectionStatus } from '@tc-chat/adapters';
import type { ChatMessage, PanelHandle, PromptFileRef, ResourceReference } from '@tc-chat/core';
import { useEffect, useMemo, useRef, useState, type RefObject } from 'react';
import { toast } from 'sonner';
import { enrichFileRefContent } from './botChatMessagePrefill';
import { prependUniqueMessages } from './groupChatHistoryUtils';
import { useBotConnectionRuntime } from './useBotConnectionRuntime';
import { useSessionDisplayStatus, type SessionEnterOutcome } from './useSessionDisplayStatus';

export interface SendFileRefs {
  resourceReferences?: ResourceReference[];
  promptFileRefs?: PromptFileRef[];
  fileRefDisplay?: Array<{ insert_id: string; name: string }>;
}
export type BotLibraryCdnLoader = (botId: string) => Promise<number>;
/** useBotChat —— bot 单聊对话 Hook(镜像 useGroupChat)。 */
export function useBotChat(
  bot: ChatBotView | null,
  session: BotChatSessionView | null,
  panelRef?: RefObject<PanelHandle | null>,
  loadBotLibraryCdn: BotLibraryCdnLoader = queryAndRegisterBotLibraryCdn,
  authenticatedUserId?: string | null,
) {
  const workspaceIdentityId = useWorkspaceStore((s) => s.activeIdentityId);
  const identityId = authenticatedUserId === undefined ? workspaceIdentityId : authenticatedUserId;
  const historyRefreshNonce = useWorkspaceStore((s) => s.historyRefreshNonce);
  const sessionId = session?.sessionId ?? null;
  const [supportState, setSupportState] = useState<BotChatState>({ phase: 'idle', error: null });
  const [connectionStatus, setConnectionStatus] = useState<ProviderConnectionStatus>('disconnected');
  // 连接状态仍等待双完成，但 HTTP 历史独立就绪，先供阅读，不等待 IAM/握手。
  const [connectDone, setConnectDone] = useState(false);
  const [historyProvider, setHistoryProvider] = useState<ReturnType<typeof createBotChatProvider> | null>(null);
  const [enterFailed, setEnterFailed] = useState(false);
  const [historyEmpty, setHistoryEmpty] = useState(false);
  const [hasMoreHistory, setHasMoreHistory] = useState(false);
  const [isLoadingMoreHistory, setIsLoadingMoreHistory] = useState(false);

  const provider = useMemo(() => {
    if (!bot || !sessionId || !identityId) return null;
    return createBotChatProvider({ bot, userId: identityId, sessionId });
  }, [bot, sessionId, identityId]);

  const connectionRuntime = useBotConnectionRuntime(provider);
  const historyDone = provider !== null && historyProvider === provider;
  const currentProvider = useRef(provider);
  currentProvider.current = provider;

  // 副屏 CDN 映射后台加载；切 Bot/卸载清旧 scope，失败不阻塞历史和连接。
  useEffect(() => {
    const botId = bot?.botId ?? null;
    const runtimeAllowsCdn = !bot?.isFriendBot
      || (connectionRuntime.resolved && !isAicodingConnectionEngine(connectionRuntime.engine));
    if (!botId || !runtimeAllowsCdn) return;
    void loadBotLibraryCdn(botId).catch(() => {
      /* 静默降级：方式②副屏不可用不阻断会话主流程 */
    });
    return () => {
      clearBotCdnConfig(botId);
    };
  }, [bot?.botId, bot?.isFriendBot, connectionRuntime, loadBotLibraryCdn]);

  // panelRef 透传 SDK，发送时自动 flushContext() 注入副屏交互。
  const chat = useChat({
    provider: (provider ?? null) as never,
    conversationKey: sessionId ?? undefined,
    placeholderMessage: '',
    panelRef,
  });
  const enterOutcome: SessionEnterOutcome = enterFailed
    ? 'failed'
    : connectDone && historyDone && (historyEmpty || chat.messages.length > 0)
    ? 'ready'
    : 'pending';
  const displayStatus = useSessionDisplayStatus({
    sessionKey: sessionId,
    rawStatus: connectionStatus,
    enterOutcome,
  });

  const prevRef = useRef<string | null>(null);
  useEffect(() => {
    if (!provider || !sessionId) return;
    if (prevRef.current === sessionId) return;
    let cancelled = false;
    prevRef.current = sessionId;
    setConnectDone(false);
    setEnterFailed(false);
    panelRef?.current?.closePanelForce();
    provider
      .connect()
      .then(() => {
        if (!cancelled) setConnectDone(true);
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setEnterFailed(true);
        toast.error(error instanceof Error ? error.message : 'Bot 连接失败');
      });
    return () => {
      cancelled = true;
      prevRef.current = null;
      void provider.disconnect();
    };
  }, [provider, sessionId]);

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

  // A2A M2 bootstrap seam：B12 取消四态播报（单行 toast；文案单源 CANCEL_UI_TEXT，
  // 与 v3 路径互不干扰——A2A 未启用时 provider 永不触发该订阅源）。
  useEffect(() => {
    if (!provider) return;
    return provider.subscribeToCancelOutcome?.((outcome) => {
      toast(CANCEL_UI_TEXT[outcome.kind]);
    });
  }, [provider]);

  useEffect(() => {
    if (!provider || !sessionId) return;
    let cancelled = false;
    setHasMoreHistory(false);
    setIsLoadingMoreHistory(false);
    setHistoryProvider(null);
    setHistoryEmpty(false);
    setEnterFailed(false);
    provider
      .loadHistory()
      .then((history: ChatMessage[]) => {
        if (!cancelled) {
          const merged = mergeTaskPreflightMessages(history, listTaskPreflightMessages(sessionId));
          chat.setMessages(merged);
          setHasMoreHistory(provider.hasMoreHistory);
          setHistoryEmpty(merged.length === 0);
          setHistoryProvider(provider);
        }
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setEnterFailed(true);
        toast.error(error instanceof Error ? error.message : '加载历史消息失败');
      });
    return () => {
      cancelled = true;
    };
    // historyRefreshNonce: 点击会话 tab 时递增,即使是同一会话也强制重新拉取历史。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [provider, sessionId, historyRefreshNonce]);

  const send = (text: string, options?: SendFileRefs) => {
    if (!sessionId || !text.trim() || chat.isRequesting) return;
    // A2A M2 bootstrap seam：§3.1 context 级发送禁令门——CancelTask 落地"停止未确认"
    // 期间禁发同 context 新消息（文案单源 SEND_BAN_UI_TEXT）；非 A2A 面恒放行。
    if (provider?.isSendBanned?.()) {
      toast.error(SEND_BAN_UI_TEXT);
      return;
    }
    const trimmed = text.trim();
    const displayContent = enrichFileRefContent(trimmed, options?.fileRefDisplay);
    chat.onRequest({
      content: trimmed,
      sessionId,
      userMessage: {
        content: displayContent,
        extra: { displayTime: new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }) },
      },
      ...(options?.resourceReferences ? { resourceReferences: options.resourceReferences } : {}),
      ...(options?.promptFileRefs ? { promptFileRefs: options.promptFileRefs } : {}),
      ...(options?.fileRefDisplay ? { fileRefDisplay: options.fileRefDisplay } : {}),
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
      // 手动重连成功回写进入信号：失败态恢复后不卡「连接中」；历史信号由 reloadHistory 回写。
      setEnterFailed(false);
      setConnectDone(true);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '重连失败');
    }
  };
  const loadMoreHistory = async () => {
    if (!provider || !sessionId || isLoadingMoreHistory || !hasMoreHistory) return;
    setIsLoadingMoreHistory(true);
    try {
      const older = await provider.loadMoreHistory();
      if (currentProvider.current !== provider) return;
      if (older.length > 0) {
        chat.setMessages((current) => prependUniqueMessages(current, older));
      }
      setHasMoreHistory(provider.hasMoreHistory);
    } catch (error) {
      if (currentProvider.current !== provider) return;
      toast.error(error instanceof Error ? error.message : '加载更多历史消息失败');
    } finally {
      if (currentProvider.current === provider) setIsLoadingMoreHistory(false);
    }
  };

  const reloadHistory = async () => {
    if (!provider || !sessionId) return;
    try {
      const h = await provider.loadHistory();
      if (currentProvider.current !== provider) return;
      chat.setMessages(h);
      setHasMoreHistory(provider.hasMoreHistory);
      setEnterFailed(false);
      setHistoryEmpty(h.length === 0);
      setHistoryProvider(provider);
    } catch (e) {
      if (currentProvider.current !== provider) return;
      setEnterFailed(true);
      toast.error(e instanceof Error ? e.message : '加载历史消息失败');
    }
  };

  return {
    chat,
    supportState,
    connectionStatus: displayStatus.status,
    historyReady: historyDone,
    connectionRuntime,
    send,
    stop,
    reconnect,
    reloadHistory,
    hasMoreHistory,
    isLoadingMoreHistory,
    loadMoreHistory,
  };
}
