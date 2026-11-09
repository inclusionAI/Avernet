import { getCapabilities } from '@/capabilities';
import type { ConversationBotView, ConversationSessionScope } from '@/domain/conversation';
import { isManagedConversationSection } from '@/domain/conversation/types';
import { botSessionService } from '@/services/workspace/botSessionService';
import { useConversationStore } from '@/stores/conversationStore';
import { useCallback, useEffect, useRef, useState } from 'react';
import { toast } from 'sonner';
import { writeSessionState } from './conversationSessionCache';

export interface ConversationSessionCreationModel {
  createSession(botId: string): Promise<void>;
  /** 行尾 + 与空态创建按钮共享的 Bot 级在途标记。 */
  isCreatingSession(botId: string): boolean;
}

/** 从列表 Hook 提取既有创建链路：仍调用同一服务、缓存及选中合同。 */
export function useConversationSessionCreation(
  userId: string | null,
  botByBotId: Map<string, ConversationBotView>,
): ConversationSessionCreationModel {
  const generation = useRef(0);
  const pendingBotIdsRef = useRef<Set<string>>(new Set());
  const [, refreshPending] = useState(0);

  useEffect(() => {
    generation.current += 1;
    pendingBotIdsRef.current.clear();
    return () => {
      generation.current += 1;
      pendingBotIdsRef.current.clear();
    };
  }, [userId]);

  const userIdRef = useRef(userId);
  userIdRef.current = userId;

  const effectiveScopeOf = (botId: string): ConversationSessionScope =>
    useConversationStore.getState().effectiveScopeByManagedBotId[botId] ?? 'all';

  const createSession = useCallback(
    async (botId: string): Promise<void> => {
      const view = botByBotId.get(botId);
      const userIdNow = userIdRef.current;
      if (!userIdNow || !view?.bot.chatable) return;
      if (pendingBotIdsRef.current.has(botId)) return;

      const storeBeforeCreate = useConversationStore.getState();
      if (
        isManagedConversationSection(view.section) &&
        (storeBeforeCreate.originByManagedBotId[botId] ?? 'mine') !== 'mine'
      ) {
        return; // others 无交互会话能力。
      }

      // managed/team Coding Bot 的 mutation 依赖目标 Bot 的 AIXCoding controller。
      // Bot 行 “+” 是显式切换入口：先把主舞台切到目标 Bot，再等待子应用注册 controller。
      // Friend Coding Bot 走普通 CC 独立链路，不进入该分支。
      const isManagedCodingBot = isManagedConversationSection(view.section) && view.bot.isAgentCodingBot;
      const shouldActivateTargetRuntime =
        isManagedCodingBot &&
        (storeBeforeCreate.selectedBotId !== botId || storeBeforeCreate.selectedSection !== view.section);
      if (shouldActivateTargetRuntime) {
        storeBeforeCreate.setExpandedBot(botId, true);
        storeBeforeCreate.selectConversation({
          botId,
          section: view.section,
          origin: 'mine',
          scope: effectiveScopeOf(botId),
          friendUserId: null,
          sessionId: null,
        });
      }

      pendingBotIdsRef.current.add(botId);
      refreshPending((value) => value + 1);
      const started = generation.current;
      // 必须 capture 切换后的 selection。否则 A 的选中保护会拦住 B 的创建结果。
      const selectionAtStart = useConversationStore.getState();
      const codingHost = view.bot.isAgentCodingBot ? getCapabilities().getCodingBotConversationHost().value : null;
      let result;
      try {
        if (view.bot.isAgentCodingBot) {
          if (!codingHost?.createSession) {
            toast.error('当前部署未提供 Coding Bot 会话创建能力。');
            return;
          }
          result = await codingHost.createSession({
            botId: view.bot.realBotId,
            userId: userIdNow,
            ownerId: view.bot.ownerId,
            title: '新对话',
          });
          if (!result.ok) {
            if (
              result.code !== 'RUNTIME_CANCELLED' &&
              userIdRef.current === userIdNow &&
              generation.current === started
            ) {
              toast.error(result.message);
            }
            return;
          }
          const summary = result.session;
          if (!summary?.sessionId) {
            if (userIdRef.current === userIdNow && generation.current === started) {
              toast.error('创建会话失败：未返回会话 ID');
            }
            return;
          }
          const now = new Date().toISOString();
          result = {
            ok: true,
            data: {
              sessionId: summary?.sessionId ?? '',
              botId: view.bot.botId,
              title: summary?.title || '新对话',
              messageCount: summary?.messageCount ?? 0,
              gmtModified: summary?.gmtModified || now,
              gmtCreate: summary?.gmtCreate || now,
              model: summary?.model,
              favorite: false,
            },
          };
        } else {
          result = await botSessionService.createSession(view.bot, userIdNow); // 复用既有实现,不重复建。
        }

        if (userIdRef.current !== userIdNow || generation.current !== started) return;
        if (!result.ok) {
          const createError = result as { error?: { friendlyMessage?: string }; message?: string };
          toast.error(createError.error?.friendlyMessage || createError.message || '创建会话失败');
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
        const scope = isManagedConversationSection(view.section)
          ? effectiveScopeOf(botId)
          : store.scopeByFriendBotId[botId] ?? 'all';
        const sessionKey = store.sessionKeyByBotId[botId] ?? '';
        const managedCache = isManagedConversationSection(view.section) ? store.sessionsByBotId[botId] : undefined;
        const cachedList =
          managedCache?.sessions ?? (view.section === 'friend' ? store.friendBotSessionsByBotId[botId] : undefined);
        // 仅当缓存即当前读取范围(all)且无生效搜索时前置新会话;favorite 列表与
        // 过滤列表不混入新会话,交给按当前条件的重新加载。
        const canPrepend =
          Boolean(cachedList) &&
          !sessionKey &&
          scope === 'all' &&
          (view.section === 'friend' ||
            ((store.originByManagedBotId[botId] ?? 'mine') === 'mine' &&
              managedCache?.origin === 'mine' &&
              managedCache?.scope === 'all'));
        if (canPrepend && cachedList) {
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
          // 好友 Bot 的主选中范围恒 'all'(与行选择/URL 投影语义一致),好友筛选只作用于侧栏列表。
          const selectionScope = isManagedConversationSection(view.section) ? scope : 'all';
          // 创建结果成为主会话但不过滤列表(spec: 新会话不能混入过滤结果)。
          store.setSelectedSessionSnapshot({
            section: view.section,
            botId,
            origin: 'mine',
            scope: selectionScope,
            friendUserId: null,
            session: created,
            friend: null,
          });
          store.selectConversation({
            botId,
            section: view.section,
            origin: 'mine',
            scope: selectionScope,
            friendUserId: null,
            sessionId: created.sessionId,
          });
        }
        toast.success('会话已创建');
      } finally {
        pendingBotIdsRef.current.delete(botId);
        refreshPending((value) => value + 1);
      }
    },
    [botByBotId],
  );

  const isCreatingSession = useCallback((botId: string): boolean => pendingBotIdsRef.current.has(botId), []);

  return { createSession, isCreatingSession };
}
