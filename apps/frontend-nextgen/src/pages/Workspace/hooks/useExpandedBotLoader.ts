import type { ChatBotView } from '@/services/workspace/botSessionService';
import type { MutableRefObject } from 'react';
import { useEffect, useRef } from 'react';

/**
 * 展开 bot 的会话首屏懒加载（从 useBotSessionMap 抽出以守行数预算）：
 * - bot 从「未展开 → 展开」的 transition 必然重拉首页——切换 bot 时拿到最新会话列表
 *   （并发去重由 loadFirstPage 内 inFlightRef 承担；旧数据保留展示，成功后整体替换，无骨架闪烁）；
 * - 持续展开的 bot 依赖 loadedRef 去重（chatBots 数组刷新等不重复请求），未加载成功过的允许重试。
 * 身份切换的清缓存/在途失效由 useBotSessionMap 渲染期重置负责，本 hook 不感知代际。
 */
export function useExpandedBotLoader(
  chatBots: ChatBotView[],
  expandedBotIds: string[],
  activeIdentityId: string | null,
  loadFirstPage: (bot: ChatBotView, userId: string) => Promise<void>,
  loadedRef: MutableRefObject<Set<string>>,
): void {
  const prevExpandedRef = useRef<Set<string>>(new Set());
  useEffect(() => {
    if (!activeIdentityId) return;
    const prev = prevExpandedRef.current;
    const next = new Set(expandedBotIds);
    prevExpandedRef.current = next;
    for (const bot of chatBots) {
      if (!bot.chatable || bot.isAgentCodingBot || !next.has(bot.botId)) continue;
      const justExpanded = !prev.has(bot.botId);
      if (loadedRef.current.has(bot.botId) && !justExpanded) continue;
      void loadFirstPage(bot, activeIdentityId);
    }
  }, [activeIdentityId, chatBots, expandedBotIds, loadFirstPage, loadedRef]);
}
