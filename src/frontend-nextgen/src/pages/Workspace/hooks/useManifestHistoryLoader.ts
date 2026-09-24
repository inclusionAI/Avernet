import { queryAndRegisterManifestLibraryCdn } from '@/services/bcs/libraryCdnInjector';
import type { GroupChatProvider } from '@/services/workspace/groupChatProvider';
import type { ChatMessage } from '@tc-chat/core';
import { useEffect } from 'react';
import { toast } from 'sonner';
import type { SessionEnterOutcome } from './useSessionDisplayStatus';

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
 *
 * 进入结果经 onEnterOutcome 上报（useSessionDisplayStatus 合成显示状态的输入）：
 * effect 重跑置 pending；enterLiveMode 成功置 ready（携带 historyEmpty——历史为 0 条的真空会话）；
 * 失败（含取消外的任何抛错）置 failed。就绪最终生效由 useGroupChat 按「消息实际进入渲染」收敛。
 */
export function useManifestHistoryLoader({
  provider,
  sessionId,
  historyRefreshNonce,
  setHasMoreHistory,
  setIsLoadingMoreHistory,
  setMessages,
  onEnterOutcome,
}: {
  provider: GroupChatProvider | null;
  sessionId: string | null;
  historyRefreshNonce: number;
  setHasMoreHistory: (v: boolean) => void;
  setIsLoadingMoreHistory: (v: boolean) => void;
  setMessages: (messages: ChatMessage[]) => void;
  /** outcome 为 ready 时携带 historyEmpty（历史 0 条，真空会话可直接就绪显示空态文案）。 */
  onEnterOutcome?: (outcome: SessionEnterOutcome, historyEmpty?: boolean) => void;
}): void {
  useEffect(() => {
    if (!provider || !sessionId) return;
    let cancelled = false;
    setHasMoreHistory(false);
    setIsLoadingMoreHistory(false);
    onEnterOutcome?.('pending');
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
        onEnterOutcome?.('ready', history.length === 0);
      } catch (error: unknown) {
        if (cancelled) return;
        // 初始化失败也必须退出暂存态，后续重试会重新开启一个 hydration window。
        provider.enterLiveMode();
        onEnterOutcome?.('failed');
        toast.error(error instanceof Error ? error.message : '协作会话初始化失败');
      }
    })();
    return () => {
      cancelled = true;
      provider.disconnect();
    };
    // setMessages / onEnterOutcome 来自 useChat 与 useState，回调引用稳定（useCallback/setState）；
    // 不纳入依赖避免重复触发。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [provider, sessionId, historyRefreshNonce]);
}
