import { queryAndRegisterManifestLibraryCdn } from '@/services/bcs/libraryCdnInjector';
import type { GroupChatProvider } from '@/services/workspace/groupChatProvider';
import type { ChatMessage } from '@tc-chat/core';
import { useEffect } from 'react';
import { toast } from 'sonner';

/**
 * 协作群会话切换时「先拉 manifest 再加载历史」的加载器。
 *
 * 必须先 await manifest 写入 aixLibraryCdnMap 再加载历史：历史消息可能含
 * `<AixUI component="bcsPanel.StateMachineRunView">` 声明式副屏，引擎
 * resolveBusinessEntry 在渲染时同步查 aixLibraryCdnMap 解析 CDN URL；若 manifest
 * 尚未返回，cdn 为 undefined → UmdPanel 立即报「缺少 CDN 地址」。manifest 请求有
 * 去重（manifestLoadPromise），且总是 resolve（失败返回空 map），不会阻塞历史加载超时。
 *
 * 同时承担原 useGroupChat 中进入会话即拉 manifest 的数据桥职责（写 window.aixLibraryCdnMap）。
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
    void (async () => {
      await queryAndRegisterManifestLibraryCdn();
      if (cancelled) return;
      provider
        .loadHistory()
        .then((history: ChatMessage[]) => {
          if (cancelled) return;
          setMessages(history);
          if (!cancelled) setHasMoreHistory(provider.hasMoreHistory);
        })
        .catch((error: unknown) => {
          if (cancelled) return;
          toast.error(error instanceof Error ? error.message : '加载历史消息失败');
        });
    })();
    return () => {
      cancelled = true;
    };
    // setMessages 来自 useChat，回调引用稳定（useCallback）；不纳入依赖避免重复触发。
    // historyRefreshNonce：点击会话 tab 时递增，即使是同一会话也强制重新拉取历史。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [provider, sessionId, historyRefreshNonce]);
}
