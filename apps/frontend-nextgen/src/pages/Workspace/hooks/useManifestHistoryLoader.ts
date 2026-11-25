import { collectSenderBots, type SenderBotRef } from '@/services/bcs/collabSenderBots';
import { queryAndRegisterManifestLibraryCdn, registerBotCdnList } from '@/services/bcs/libraryCdnInjector';
import type { GroupChatProvider } from '@/services/workspace/groupChatProvider';
import type { ChatMessage } from '@tc-chat/core';
import { useEffect, useRef } from 'react';
import { toast } from 'sonner';
import type { SessionEnterOutcome } from './useSessionDisplayStatus';

/**
 * 协作群会话切换时统一执行连接、CDN 装配与历史 hydration 的加载器。
 *
 * 副屏 CDN 注册必须在 setMessages 之前完成：历史消息可能含
 * `<AixUI component="lib.Comp">` 声明式副屏，引擎 resolveBusinessEntry 在渲染时同步查
 * aixLibraryCdnMap 解析 CDN URL；晚注册不会回填已渲染消息。两个数据源：
 * ① BCS manifest（全局清单，queryAndRegisterManifestLibraryCdn 去重且总是 resolve，
 *    失败返回空 map）；② 历史消息发送方 Bot 的 render screens 副屏配置
 * （registerBotCdnList，Bot 自配库如 rpp-prod 靠此来源，单 Bot 失败静默降级为空桶）。
 * 发送方集合在 loadHistory 返回后即可枚举，与 manifest 一起 allSettled 装配完再挂载；
 * 另并入会话参与者 Bot（preRegisterBots）——成员 Bot 的 live 回复同样渲染时同步查 map，
 * 历史窗口里未发过言的成员 MUST 提前入桶，否则首条回复副屏报「缺少 CDN 地址」
 * （晚注册不回填）。参与者经 ref 读最新值（会话详情在加载过程中补齐），不进 effect 依赖。
 *
 * 连接前先开启 SDK history hydration，使连接后、历史安装前到达的 WS 帧进入暂存区；
 * 历史安装完成后再进入 live mode，保证刷新恢复的 pending block 与后续 WS 更新合并到
 * 同一条 run 消息。
 *
 * 进入结果经 onEnterOutcome 上报（useSessionDisplayStatus 合成显示状态的输入）：
 * effect 重跑置 pending；enterLiveMode 成功置 ready（携带 historyEmpty——历史为 0 条的真空会话）；
 * 失败（含取消外的任何抛错）置 failed。就绪最终生效由 useGroupChat 按「消息实际进入渲染」收敛。
 */
export function useManifestHistoryLoader({
  provider,
  sessionId,
  historyRefreshNonce,
  preRegisterBots,
  setHasMoreHistory,
  setIsLoadingMoreHistory,
  setMessages,
  onEnterOutcome,
}: {
  provider: GroupChatProvider | null;
  sessionId: string | null;
  historyRefreshNonce: number;
  /** 会话参与者 Bot（预注册源，见顶部注释）；与时序无关，经 ref 读最新值。 */
  preRegisterBots?: readonly SenderBotRef[];
  setHasMoreHistory: (v: boolean) => void;
  setIsLoadingMoreHistory: (v: boolean) => void;
  setMessages: (messages: ChatMessage[]) => void;
  /** outcome 为 ready 时携带 historyEmpty（历史 0 条，真空会话可直接就绪显示空态文案）。 */
  onEnterOutcome?: (outcome: SessionEnterOutcome, historyEmpty?: boolean) => void;
}): void {
  // 参与者集合随会话详情异步补齐；注册发生在 loadHistory 返回后的临界区内，读最新值即可，
  // 不作为 effect 依赖（避免参与者补齐触发历史重载）。
  const preRegisterRef = useRef(preRegisterBots);
  preRegisterRef.current = preRegisterBots;

  useEffect(() => {
    if (!provider || !sessionId) return;
    let cancelled = false;
    setHasMoreHistory(false);
    setIsLoadingMoreHistory(false);
    onEnterOutcome?.('pending');
    provider.beginHistoryHydration();
    void (async () => {
      try {
        if (provider.liveEnabled) await provider.connect();
        if (cancelled) return;
        // manifest 起跑即并行（内部去重、永不 reject）；发送方集合需等历史返回后归并。
        const manifestCdn = queryAndRegisterManifestLibraryCdn();
        const history: ChatMessage[] = await provider.loadHistory();
        if (cancelled) return;
        // 副屏 CDN 注册（manifest + 历史发送方 Bot + 会话参与者 Bot）在 setMessages 前完成装配，
        // 单源失败降级为空映射，不阻塞历史挂载（allSettled）；复合键去重由 registerBotCdnList 承担。
        await Promise.allSettled([
          manifestCdn,
          registerBotCdnList([...collectSenderBots(history), ...(preRegisterRef.current ?? [])]),
        ]);
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
