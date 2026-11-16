// Coding Bot 归档恢复结果轮询。只在存在 operationIds 时工作，完成后刷新普通列表，
// 并按提交时的 focus 标记自动打开唯一成功恢复的会话。
import type { CodingBotSessionArchiveHost } from '@/capabilities';
import type {
  CodingBotRestoredSessionItem,
  CodingBotSessionArchiveItem,
  ConversationBotView,
  ConversationOrigin,
} from '@/domain/conversation/types';
import { isManagedConversationSection } from '@/domain/conversation/types';
import { useConversationStore } from '@/stores/conversationStore';
import { useEffect } from 'react';
import {
  baseInput,
  refreshNormalSessions,
  restoredSessionsOf,
  validateTarget,
} from './conversationSessionArchiveRequests';
import { mergeSessionArchiveList, patchSessionArchives, restoredItemsWith } from './conversationSessionArchiveState';

export interface RestoredSessionFocusInput {
  view: ConversationBotView;
  origin: ConversationOrigin;
  sessionId: string;
}

export interface ConversationSessionArchivePollingOptions {
  host: CodingBotSessionArchiveHost | null;
  botByBotId: Map<string, ConversationBotView>;
  userIdRef: { current: string | null };
  focusByOperation: { current: Map<string, boolean> };
  onRestoredSessionFocusRef: { current: (input: RestoredSessionFocusInput) => void };
}

export function useConversationSessionArchivePolling({
  host,
  botByBotId,
  userIdRef,
  focusByOperation,
  onRestoredSessionFocusRef,
}: ConversationSessionArchivePollingOptions): void {
  const operationKey = useConversationStore((state) =>
    JSON.stringify(
      Object.entries(state.sessionsByBotId)
        .map(([botId, view]) => [botId, view.scope, view.sessionArchives?.operationIds ?? []] as const)
        .filter(([botId]) => botByBotId.has(botId)),
    ),
  );

  useEffect(() => {
    if (!host || operationKey === '[]') return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      const store = useConversationStore.getState();
      for (const [botId, view] of Object.entries(store.sessionsByBotId)) {
        const codingView = botByBotId.get(botId);
        if (!codingView || !codingView.bot.isAgentCodingBot || !isManagedConversationSection(codingView.section)) continue;
        const origin = store.originByManagedBotId[botId] ?? 'mine';
        const revision = store.sessionFilterRevisionByBotId[botId] ?? 0;
        for (const operationId of view.sessionArchives?.operationIds ?? []) {
          const result = await host.list({
            ...baseInput(codingView.bot, userIdRef.current ?? ''),
            operationId,
            limit: 100,
          });
          if (cancelled) return;
          if (!validateTarget(botId, origin, revision)) continue;
          if (result.ok) {
            const current = useConversationStore.getState().sessionsByBotId[botId]?.sessionArchives;
            const items = current?.items ?? [];
            const merged = mergeSessionArchiveList(items, result.items);
            const operationItems = result.items.filter(
              (item: CodingBotSessionArchiveItem) => item.restore?.operationId === operationId,
            );
            const completed =
              operationItems.length > 0 &&
              operationItems.every((item) => item.restoreStatus === 'succeeded' || item.restoreStatus === 'failed');
            const succeeded = operationItems.filter(
              (item: CodingBotSessionArchiveItem) => item.restoreStatus === 'succeeded',
            );
            const succeededKeys = succeeded.map((item) => item.sessionKey);
            const restoredDetails = await restoredSessionsOf(host, codingView, userIdRef.current, succeededKeys);
            const restoredDetailByKey = new Map(restoredDetails.map((item) => [item.sessionKey, item]));
            const restoredNow: CodingBotRestoredSessionItem[] = succeeded.map((item) => {
              const detail = restoredDetailByKey.get(item.sessionKey);
              return {
                sessionKey: item.sessionKey,
                archiveId: detail?.archiveId ?? item.archiveId,
                restoredAt: detail?.restoredAt ?? item.restoredAt ?? '',
                conversation: detail?.conversation,
                sessionSource: item.sessionSource,
              };
            });
            patchSessionArchives(botId, {
              items: merged,
              restoreError: null,
              restoredItems: restoredItemsWith([...restoredNow, ...(current?.restoredItems ?? [])]),
              operationIds: completed
                ? (useConversationStore.getState().sessionsByBotId[botId]?.sessionArchives?.operationIds ?? []).filter(
                    (id) => id !== operationId,
                  )
                : undefined,
            });
            if (completed) {
              await refreshNormalSessions(codingView, origin, userIdRef.current);
              const focus = focusByOperation.current.get(operationId) && succeeded.length === 1;
              focusByOperation.current.delete(operationId);
              if (focus && restoredNow[0]) {
                onRestoredSessionFocusRef.current({
                  view: codingView,
                  origin,
                  sessionId: restoredNow[0].conversation?.sessionId ?? restoredNow[0].sessionKey,
                });
              }
            }
          }
        }
      }
      if (!cancelled) timer = setTimeout(poll, 3000);
    };
    void poll();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [
    botByBotId,
    focusByOperation,
    host,
    onRestoredSessionFocusRef,
    operationKey,
    userIdRef,
  ]);
}
