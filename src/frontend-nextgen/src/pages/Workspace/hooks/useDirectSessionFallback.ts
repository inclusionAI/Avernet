import type { BotChatSessionView, ChatBotView } from '@/services/workspace/botSessionService';
import { botSessionService } from '@/services/workspace/botSessionService';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { type Dispatch, type MutableRefObject, type SetStateAction, useEffect, useRef, useState } from 'react';

/**
 * 外链直达旧会话兜底：首页按 10 条分页，若选中的 sessionId 不在已加载首页内（如外链直达很久之前的会话），
 * 按当前展开的 bot 直接拉取该会话详情并补入列表，避免右侧聊天区因列表未覆盖而空白。每个 sessionId 只补取一次。
 * 陈旧选中自愈（延迟轮换）：详情反查失败只在回调里打「已确认删除」标记，轮换由 effect 在最新提交的列表上执行，
 * 避免 createSession 等「选中先提交、列表后提交」的中间帧被误判劫持；展开的 bot 已不在可用列表 → 清空选中。
 * 仅 chat 视图生效：group 视图下好友 bot 列表不加载（useFriendBots 门控），「bot 不存在」会误清单聊记忆。
 */
export function useDirectSessionFallback(
  activeIdentityId: string | null,
  chatBots: ChatBotView[],
  expandedBotIds: string[],
  rawByBotId: Record<string, BotChatSessionView[]>,
  setRawByBotId: Dispatch<SetStateAction<Record<string, BotChatSessionView[]>>>,
  loadedRef: MutableRefObject<Set<string>>,
  isBotListsLoading: boolean,
): void {
  const fetchedRef = useRef<Set<string>>(new Set());
  const genRef = useRef(0);
  const selectedBotSessionId = useWorkspaceStore((s) => s.selectedBotSessionId);
  const view = useWorkspaceStore((s) => s.view);

  // 「已确认删除」标记：详情反查失败只在回调里打标，轮换由 effect 在最新列表上执行，
  // 避免 createSession 等「选中先提交、列表后提交」的中间帧被误判为陈旧选中而劫持。
  const [confirmedGoneId, setConfirmedGoneId] = useState<string | null>(null);

  // 身份切换：渲染期同步自增代际并清空已补取集合/已确认删除标记（与 useBotSessionMap 的
  // 渲染期清缓存同一渲染趟完成），杜绝「map 已清空但代际未跳」的 commit→effect 窗口内
  // 旧身份在途响应通过代际校验回填新列表。
  const [lastIdentityId, setLastIdentityId] = useState(activeIdentityId);
  if (lastIdentityId !== activeIdentityId) {
    setLastIdentityId(activeIdentityId);
    genRef.current += 1;
    fetchedRef.current.clear();
    setConfirmedGoneId(null);
  }

  useEffect(() => {
    if (!activeIdentityId || view !== 'chat') return;
    const targetSessionId = selectedBotSessionId;
    if (!targetSessionId) return;
    // 已在已加载列表内：无需补取/轮换；并清除可能过期的「已确认删除」标记（如新建会话竞态窗口）。
    if (Object.values(rawByBotId).some((list) => list.some((s) => s.sessionId === targetSessionId))) {
      if (confirmedGoneId === targetSessionId) setConfirmedGoneId(null);
      return;
    }
    const bot = chatBots.find((b) => expandedBotIds.includes(b.botId));
    // 已确认删除且最新列表仍不含它：回落该 bot 列表首条或清空（在 effect 中执行，列表已是提交后的最新值）。
    if (confirmedGoneId === targetSessionId) {
      setConfirmedGoneId(null);
      const list = bot ? rawByBotId[bot.botId] ?? [] : [];
      useWorkspaceStore.getState().selectBotSession(list[0]?.sessionId ?? null);
      return;
    }
    if (fetchedRef.current.has(targetSessionId)) return;
    if (!bot) {
      // 展开的 bot 已不在可用列表（被删/解除好友），且 bot 列表（含好友 bot，加载慢于我的 bot）
      // 全部就绪后才允许清空悬空选中，避免右侧聊天区永远空白、记忆无法自愈。
      if (!isBotListsLoading && chatBots.length > 0 && expandedBotIds.length > 0)
        useWorkspaceStore.getState().selectBotSession(null);
      return;
    }
    // 仅在该 bot 首页已加载后才补取，避免与 loadFirstPage 竞态。
    if (!loadedRef.current.has(bot.botId)) return;
    fetchedRef.current.add(targetSessionId);
    const generation = genRef.current;
    void botSessionService.getSessionDetail(bot, activeIdentityId, targetSessionId).then((res) => {
      if (generation !== genRef.current) return;
      if (!res.ok || !res.data) {
        // 请求在途时用户可能已点选其他会话：仅当选中仍是陈旧项时才打标。
        if (useWorkspaceStore.getState().selectedBotSessionId !== targetSessionId) return;
        setConfirmedGoneId(targetSessionId);
        return;
      }
      setRawByBotId((current) => {
        const list = current[bot.botId] ?? [];
        if (list.some((s) => s.sessionId === res.data!.sessionId)) return current;
        return { ...current, [bot.botId]: [res.data!, ...list] };
      });
    });
  }, [
    activeIdentityId,
    view,
    rawByBotId,
    expandedBotIds,
    chatBots,
    selectedBotSessionId,
    setRawByBotId,
    loadedRef,
    confirmedGoneId,
    isBotListsLoading,
  ]);
}
