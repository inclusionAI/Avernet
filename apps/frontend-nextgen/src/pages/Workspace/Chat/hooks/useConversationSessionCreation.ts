import type { ConversationBotView, ConversationSessionScope } from '@/domain/conversation';
import { isManagedConversationSection } from '@/domain/conversation/types';
import { botSessionService } from '@/services/workspace/botSessionService';
import { useConversationStore } from '@/stores/conversationStore';
import { useCallback, useEffect, useRef } from 'react';
import { toast } from 'sonner';
import { writeSessionState } from './conversationSessionCache';

/** 从列表 Hook 提取既有创建链路：仍调用同一服务、缓存及选中合同。 */
export function useConversationSessionCreation(userId: string | null, botByBotId: Map<string, ConversationBotView>) {
  const generation = useRef(0);
  useEffect(() => {
    generation.current += 1;
    return () => {
      generation.current += 1;
    };
  }, [userId]);
  const userIdRef = useRef(userId);
  userIdRef.current = userId;
  const effectiveScopeOf = (botId: string): ConversationSessionScope =>
    useConversationStore.getState().effectiveScopeByManagedBotId[botId] ?? 'all';
  return useCallback(
    async (botId: string): Promise<void> => {
      const view = botByBotId.get(botId);
      const userIdNow = userIdRef.current;
      if (!userIdNow || !view?.bot.chatable || view.bot.isAgentCodingBot) return;
      if (
        isManagedConversationSection(view.section) &&
        (useConversationStore.getState().originByManagedBotId[botId] ?? 'mine') !== 'mine'
      ) {
        return; // others 无交互会话能力。
      }
      const started = generation.current;
      const selectionAtStart = useConversationStore.getState();
      const result = await botSessionService.createSession(view.bot, userIdNow); // 复用既有实现,不重复建。
      if (userIdRef.current !== userIdNow || generation.current !== started) return;
      if (!result.ok) {
        toast.error(result.error.friendlyMessage);
        return;
      }
      const store = useConversationStore.getState();
      const selectionUnchanged =
        store.selectedBotId === selectionAtStart.selectedBotId &&
        store.selectedSection === selectionAtStart.selectedSection &&
        store.selectedOrigin === selectionAtStart.selectedOrigin &&
        store.selectedScope === selectionAtStart.selectedScope &&
        store.selectedSessionId === selectionAtStart.selectedSessionId;
      const created = result.data;
      const scope = isManagedConversationSection(view.section) ? effectiveScopeOf(botId) : 'all';
      const managedCache = isManagedConversationSection(view.section) ? store.sessionsByBotId[botId] : undefined;
      const cachedList =
        managedCache?.sessions ?? (view.section === 'friend' ? store.friendBotSessionsByBotId[botId] : undefined);
      // 仅当缓存即当前读取范围(all)的列表时前置新会话;favorite 列表与
      // 范围不符的缓存交给展开后的重新加载。
      if (
        (view.section === 'friend' || (store.originByManagedBotId[botId] ?? 'mine') === 'mine') &&
        cachedList &&
        (view.section === 'friend' ||
          (scope === 'all' && managedCache?.origin === 'mine' && managedCache?.scope === scope))
      ) {
        writeSessionState(botId, view.section, scope, {
          ...cachedList,
          total: cachedList.total + (cachedList.items.some((item) => item.sessionId === created.sessionId) ? 0 : 1),
          items: [
            { ...created, favorite: false },
            ...cachedList.items.filter((item) => item.sessionId !== created.sessionId),
          ],
        });
      }
      if (selectionUnchanged) {
        if (!store.expandedBotIds[botId]) store.setExpandedBot(botId, true);
        store.selectConversation({
          botId,
          section: view.section,
          origin: 'mine',
          scope,
          friendUserId: null,
          sessionId: created.sessionId,
        });
      }
      toast.success('会话已创建');
    },
    [botByBotId],
  );
}
