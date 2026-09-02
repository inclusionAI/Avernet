import { queryAndRegisterManifestLibraryCdn } from '@/services/bcs/libraryCdnInjector';
import type { GroupChatProvider } from '@/services/workspace/groupChatProvider';
import type { ChatMessage } from '@tc-chat/core';
import { useEffect } from 'react';
import { toast } from 'sonner';

/**
 * 协作群会话切换时统一执行连接、manifest 与历史 hydration 的加载器。
 *
 * 必须先 await manifest 写入 aixLibraryCdnMap 再加载历史：历史消息可能含
 * `<AixUI component="bcsPanel.StateMachineRunView">` 声明式副屏，引擎
 * resolveBusinessEntry 在渲染时同步查 aixLibraryCdnMap 解析 CDN URL；若 manifest
 * 尚未返回，cdn 为 undefined → UmdPanel 立即报「缺少 CDN 地址」。manifest 请求有
 * 去重（manifestLoadPromise），且总是 resolve（失败返回空 map），不会阻塞历史加载超时。
 *
 * 连接前先开启 SDK history hydration，使连接后、历史安装前到达的 WS 帧进入暂存区；
 * 历史安装完成后再进入 live mode，保证刷新恢复的 pending block 与后续 WS 更新合并到
 * 同一条 run 消息。manifest 仍在 history 渲染前完成，避免副屏 CDN 地址竞态。
 */
export function useManifestHistoryLoader({
  provider,
  sessionId,
  historyRefreshNonce,
  setHasMoreHistory,
  setIsLoadingMoreHistory,
  setMessages,
}: {
  provider: GroupChatProvider | null;
  sessionId: string | null;
  historyRefreshNonce: number;
  setHasMoreHistory: (v: boolean) => void;
  setIsLoadingMoreHistory: (v: boolean) => void;
  setMessages: (messages: ChatMessage[]) => void;
}): void {
  useEffect(() => {
    if (!provider || !sessionId) return;
    let cancelled = false;
    setHasMoreHistory(false);
    setIsLoadingMoreHistory(false);
    provider.beginHistoryHydration();
    void (async () => {
      try {
        await provider.connect();
        if (cancelled) return;
        await queryAndRegisterManifestLibraryCdn();
        if (cancelled) return;
        const history: ChatMessage[] = await provider.loadHistory();
        if (cancelled) return;
        setMessages(history);
        setHasMoreHistory(provider.hasMoreHistory);
        provider.enterLiveMode();
      } catch (error: unknown) {
        if (cancelled) return;
        // 初始化失败也必须退出暂存态，后续重试会重新开启一个 hydration window。
        provider.enterLiveMode();
        toast.error(error instanceof Error ? error.message : '协作会话初始化失败');
      }
    })();
    return () => {
      cancelled = true;
      provider.disconnect();
    };
    // setMessages 来自 useChat，回调引用稳定（useCallback）；不纳入依赖避免重复触发。
    // historyRefreshNonce：点击会话 tab 时递增，即使是同一会话也强制重新拉取历史。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [provider, sessionId, historyRefreshNonce]);
}
