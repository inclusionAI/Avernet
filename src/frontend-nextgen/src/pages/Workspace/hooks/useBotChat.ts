import { clearBotCdnConfig, queryAndRegisterBotLibraryCdn } from '@/services/bcs/libraryCdnInjector';
import { listTaskPreflightMessages, mergeTaskPreflightMessages } from '@/services/tasks/taskPreflightMessageStore';
import { createBotChatProvider, type BotChatState } from '@/services/workspace/botChatProvider';
import type { BotChatSessionView, ChatBotView } from '@/services/workspace/botSessionService';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { useChat, type ProviderConnectionStatus } from '@tc-chat/adapters';
import type { ChatMessage, PanelHandle, PromptFileRef, ResourceReference } from '@tc-chat/core';
import { useEffect, useMemo, useRef, useState, type RefObject } from 'react';
import { toast } from 'sonner';
import { useConnectionStatusSmoothing } from './useConnectionStatusSmoothing';

export interface SendFileRefs {
  resourceReferences?: ResourceReference[];
  promptFileRefs?: PromptFileRef[];
  fileRefDisplay?: Array<{ insert_id: string; name: string }>;
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

/** 仅富化本地乐观消息:把 `<file-ref insert_id="X">` 补上 name,供气泡渲染文件名胶囊。 */
function enrichFileRefContent(content: string, fileRefDisplay?: Array<{ insert_id: string; name: string }>): string {
  if (!fileRefDisplay || fileRefDisplay.length === 0) return content;
  let out = content;
  for (const ref of fileRefDisplay) {
    if (!ref.insert_id || !ref.name) continue;
    const escName = ref.name.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    const openTag = new RegExp(
      `(<file-ref\\b[^>]*?\\binsert_id=["']${escapeRegExp(ref.insert_id)}["'][^>]*?)(\\s*/?>)`,
      'i',
    );
    out = out.replace(openTag, (match, pre, tail) =>
      /\bname\s*=/i.test(pre) ? match : `${pre} name="${escName}"${tail}`,
    );
  }
  return out;
}
export type BotLibraryCdnLoader = (botId: string) => Promise<number>;

/** useBotChat —— bot 单聊对话 Hook(镜像 useGroupChat)。 */
export function useBotChat(
  bot: ChatBotView | null,
  session: BotChatSessionView | null,
  panelRef?: RefObject<PanelHandle | null>,
  loadBotLibraryCdn: BotLibraryCdnLoader = queryAndRegisterBotLibraryCdn,
) {
  const identityId = useWorkspaceStore((s) => s.activeIdentityId);
  const historyRefreshNonce = useWorkspaceStore((s) => s.historyRefreshNonce);
  const sessionId = session?.sessionId ?? null;

  const [supportState, setSupportState] = useState<BotChatState>({ phase: 'idle', error: null });
  const [connectionStatus, setConnectionStatus] = useState<ProviderConnectionStatus>('disconnected');
  const smoothedConnectionStatus = useConnectionStatusSmoothing(connectionStatus);

  const provider = useMemo(() => {
    if (!bot || !sessionId || !identityId) return null;
    return createBotChatProvider({ bot, userId: identityId, sessionId });
  }, [bot, sessionId, identityId]);

  // 副屏方式②数据桥（对应单聊 Bot render screens）：进入会话按 botId 拉 Bot 的 CDN 库映射写
  // window.aixLibraryCdnMap，供引擎 resolveBusinessEntry 把 <AixUI component="lib.X"> 的 lib 解析成
  // CDN URL。切换 Bot / 卸载时清旧 botId 的 CDN scope，避免残留串用（对齐 ocb replaceBotCdnScopes；
  // cleanup 先于下一 effect 运行 → 先清旧再查新）。拉取失败静默降级，不阻塞会话主流程、不 toast
  // （保持与 useGroupChat manifest 桥一致的静默降级策略）。
  useEffect(() => {
    const botId = bot?.botId ?? null;
    if (!botId) return;
    void loadBotLibraryCdn(botId).catch(() => {
      /* 静默降级：方式②副屏不可用不阻断会话主流程 */
    });
    return () => {
      clearBotCdnConfig(botId);
    };
  }, [bot?.botId, loadBotLibraryCdn]);

  // panelRef 透传 SDK useChat：useChat 拿到 panelRef 后 chat.onRequest 会自动 flushContext()
  // 并以 panelContext 注入请求，使 bot 引擎感知用户在副屏的交互（对应 useGroupChat:56 的 panelRef 注入）。
  const chat = useChat({
    provider: (provider ?? null) as never,
    conversationKey: sessionId ?? undefined,
    placeholderMessage: '',
    panelRef,
  });

  const prevRef = useRef<string | null>(null);
  useEffect(() => {
    if (!provider || !sessionId) return;
    if (prevRef.current === sessionId) return;
    prevRef.current = sessionId;
    // 切换会话:清空副屏 tab,避免旧会话副屏残留叠加到新会话(对齐 useGroupChat connect effect)。
    panelRef?.current?.closePanelForce();
    provider.connect().catch((error: unknown) => toast.error(error instanceof Error ? error.message : 'Bot 连接失败'));
    return () => {
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

  useEffect(() => {
    if (!provider || !sessionId) return;
    let cancelled = false;
    provider
      .loadHistory()
      .then((history: ChatMessage[]) => {
        if (!cancelled) {
          chat.setMessages(mergeTaskPreflightMessages(history, listTaskPreflightMessages(sessionId)));
        }
      })
      .catch((error: unknown) => {
        if (!cancelled) toast.error(error instanceof Error ? error.message : '加载历史消息失败');
      });
    return () => {
      cancelled = true;
    };
    // historyRefreshNonce: 点击会话 tab 时递增,即使是同一会话也强制重新拉取历史。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [provider, sessionId, historyRefreshNonce]);

  const send = (text: string, options?: SendFileRefs) => {
    if (!sessionId || !text.trim() || chat.isRequesting) return;
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
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '重连失败');
    }
  };
  const reloadHistory = async () => {
    if (!provider || !sessionId) return;
    try {
      const h = await provider.loadHistory();
      chat.setMessages(h);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '加载历史消息失败');
    }
  };

  return { chat, supportState, connectionStatus: smoothedConnectionStatus, send, stop, reconnect, reloadHistory };
}
