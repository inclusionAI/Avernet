import type { BotChatSessionView, ChatBotView } from '@/services/workspace/botSessionService';
import { botSessionService } from '@/services/workspace/botSessionService';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { type Dispatch, type MutableRefObject, type SetStateAction, useEffect, useRef } from 'react';

/**
 * 外链直达旧会话兜底：首页按 10 条分页，若选中的 sessionId 不在已加载首页内（如外链直达很久之前的会话），
 * 按当前展开的 bot 直接拉取该会话详情并补入列表，避免右侧聊天区因列表未覆盖而空白。每个 sessionId 只补取一次。
 */
export function useDirectSessionFallback(
  activeIdentityId: string | null,
  chatBots: ChatBotView[],
  expandedBotIds: string[],
  rawByBotId: Record<string, BotChatSessionView[]>,
  setRawByBotId: Dispatch<SetStateAction<Record<string, BotChatSessionView[]>>>,
  loadedRef: MutableRefObject<Set<string>>,
): void {
  const fetchedRef = useRef<Set<string>>(new Set());
  const genRef = useRef(0);
  const selectedBotSessionId = useWorkspaceStore((s) => s.selectedBotSessionId);

  // 身份切换：清空已补取集合并自增代际，丢弃旧身份在途请求的回填，避免污染新身份会话列表。
  useEffect(() => {
    genRef.current += 1;
    fetchedRef.current.clear();
  }, [activeIdentityId]);

  useEffect(() => {
    if (!activeIdentityId) return;
    const targetSessionId = selectedBotSessionId;
    if (!targetSessionId || fetchedRef.current.has(targetSessionId)) return;
    // 已在已加载列表内则无需补取
    if (Object.values(rawByBotId).some((list) => list.some((s) => s.sessionId === targetSessionId))) return;
    const bot = chatBots.find((b) => expandedBotIds.includes(b.botId));
    // 仅在该 bot 首页已加载后才补取，避免与 loadFirstPage 竞态；找不到展开 bot 则跳过。
    if (!bot || !loadedRef.current.has(bot.botId)) return;
    fetchedRef.current.add(targetSessionId);
    const generation = genRef.current;
    void botSessionService.getSessionDetail(bot, activeIdentityId, targetSessionId).then((res) => {
      if (generation !== genRef.current || !res.ok || !res.data) return;
      setRawByBotId((current) => {
        const list = current[bot.botId] ?? [];
        if (list.some((s) => s.sessionId === res.data!.sessionId)) return current;
        return { ...current, [bot.botId]: [res.data!, ...list] };
      });
    });
  }, [activeIdentityId, rawByBotId, expandedBotIds, chatBots, selectedBotSessionId, setRawByBotId, loadedRef]);
}
