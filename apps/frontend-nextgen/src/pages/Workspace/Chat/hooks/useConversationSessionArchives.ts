// Coding Bot 已归档会话模型。归档接口始终通过 CodingBotConversationHost
// capability 调用，TeamClaw 不复制 relay connection / token。
import type {
  CodingBotSessionArchiveHost,
  CodingBotSessionArchiveListResult,
  CodingBotSessionArchivePreviewResult,
  CodingBotSessionArchiveRestoreResult,
} from '@/capabilities';
import type {
  CodingBotSessionArchiveItem,
  ConversationBotView,
  ConversationOrigin,
  ConversationSessionScope,
} from '@/domain/conversation/types';
import { isManagedConversationSection } from '@/domain/conversation/types';
import { useConversationStore } from '@/stores/conversationStore';
import { useCallback, useEffect, useMemo, useRef } from 'react';
import {
  emptySessionArchives,
  mergeSessionArchiveList,
  patchSessionArchives,
} from './conversationSessionArchiveState';
import {
  archiveHost,
  baseInput,
  emptySessions,
  refreshNormalSessions,
  validateTarget,
} from './conversationSessionArchiveRequests';
import { useConversationSessionArchivePolling } from './useConversationSessionArchivePolling';

const ARCHIVE_PAGE_SIZE = 50;

export interface ConversationSessionArchivesModel {
  host: CodingBotSessionArchiveHost | null;
  load(view: ConversationBotView, more?: boolean): Promise<void>;
  retry(view: ConversationBotView): Promise<void>;
  restore(input: {
    view: ConversationBotView;
    origin: ConversationOrigin;
    sessionKeys: string[];
    focusRestored?: boolean;
  }): Promise<CodingBotSessionArchiveRestoreResult>;
  preview(input: { view: ConversationBotView; item: CodingBotSessionArchiveItem }): Promise<CodingBotSessionArchivePreviewResult>;
}

export function useConversationSessionArchives(options: {
  userId: string | null;
  managedBots: ConversationBotView[];
  onRestoredSessionFocus(input: { view: ConversationBotView; origin: ConversationOrigin; sessionId: string }): void;
}): ConversationSessionArchivesModel {
  const { userId, managedBots, onRestoredSessionFocus } = options;
  const host = archiveHost();
  const botByBotId = useMemo(() => {
    const map = new Map<string, ConversationBotView>();
    for (const view of managedBots) map.set(view.bot.botId, view);
    return map;
  }, [managedBots]);
  const inFlight = useRef(new Set<string>());
  const focusByOperation = useRef(new Map<string, boolean>());
  const userIdRef = useRef(userId);
  userIdRef.current = userId;
  const onRestoredSessionFocusRef = useRef(onRestoredSessionFocus);
  onRestoredSessionFocusRef.current = onRestoredSessionFocus;

  const load = useCallback(
    async (view: ConversationBotView, more = false): Promise<void> => {
      const currentUserId = userIdRef.current;
      if (!currentUserId || !host || !view.bot.isAgentCodingBot) return;
      const botId = view.bot.botId;
      const store = useConversationStore.getState();
      const origin = store.originByManagedBotId[botId] ?? 'mine';
      const scope = store.effectiveScopeByManagedBotId[botId] ?? 'all';
      if (!isManagedConversationSection(view.section) || scope !== 'archived') return;
      const revision = store.sessionFilterRevisionByBotId[botId] ?? 0;
      const current = store.sessionsByBotId[botId]?.sessionArchives ?? emptySessionArchives();
      const cursor = more ? current.nextCursor : null;
      const requestKey = JSON.stringify([botId, origin, 'archived', revision, more ? cursor : 'first']);
      if (inFlight.current.has(requestKey)) return;
      if (more && (!cursor || current.loading || current.isLoadingMore)) return;
      if (!more && !current.error && !current.loading && (current.availability !== 'unknown' || current.items.length > 0)) {
        return;
      }
      inFlight.current.add(requestKey);
      patchSessionArchives(botId, {
        ...(more
          ? { isLoadingMore: true, loadMoreError: null }
          : { loading: true, error: null, availability: 'unknown' }),
      });
      const result: CodingBotSessionArchiveListResult = await host.list({
        ...baseInput(view.bot, currentUserId),
        ...(cursor ? { cursor } : {}),
        limit: ARCHIVE_PAGE_SIZE,
      });
      inFlight.current.delete(requestKey);
      if (!validateTarget(botId, origin, revision)) return;
      if (!result.ok) {
        patchSessionArchives(
          botId,
          more
            ? { isLoadingMore: false, loadMoreError: result.message }
            : {
                loading: false,
                error: result.message,
                availability: 'unsupported',
                restoreAvailability: 'unsupported',
              },
        );
        return;
      }
      const previous = more ? current.items : [];
      patchSessionArchives(botId, {
        items: mergeSessionArchiveList(previous, result.items),
        nextCursor: result.nextCursor,
        catalogRevision: result.catalogRevision,
        loading: false,
        isLoadingMore: false,
        error: null,
        loadMoreError: null,
        availability: result.availability,
        restoreAvailability: result.restoreAvailability,
      });
    },
    [host],
  );

  const restore = useCallback(
    async (input: {
      view: ConversationBotView;
      origin: ConversationOrigin;
      sessionKeys: string[];
      focusRestored?: boolean;
    }): Promise<CodingBotSessionArchiveRestoreResult> => {
      const { view, origin, sessionKeys, focusRestored } = input;
      const currentUserId = userIdRef.current;
      if (!host || !view.bot.isAgentCodingBot) {
        return { ok: false, message: '当前部署未提供 Coding Bot 归档能力。' };
      }
      const keys = Array.from(new Set(sessionKeys));
      if (!keys.length || keys.length > 100 || keys.some((key) => !key)) {
        return { ok: false, message: '归档恢复参数不合法，一次最多恢复 100 个会话。' };
      }
      const botId = view.bot.botId;
      const archiveNow = useConversationStore.getState().sessionsByBotId[botId]?.sessionArchives;
      if (archiveNow?.isSubmitting) return { ok: false, message: '归档恢复正在提交中，请稍后。' };
      patchSessionArchives(botId, { isSubmitting: true, restoreError: null });
      const result = await host.restore({ ...baseInput(view.bot, currentUserId || ''), sessionKeys: keys });
      if (!result.ok) {
        if ((useConversationStore.getState().originByManagedBotId[botId] ?? 'mine') === origin) {
          patchSessionArchives(botId, { isSubmitting: false, restoreError: result.message });
        }
        return result;
      }
      if ((useConversationStore.getState().originByManagedBotId[botId] ?? 'mine') !== origin) return result;
      const previousItems = useConversationStore.getState().sessionsByBotId[botId]?.sessionArchives?.items ?? [];
      const updated = previousItems.map((item: CodingBotSessionArchiveItem) => {
        const request = result.requested.find(
          (summary: { sessionKey: string }) => summary.sessionKey === item.sessionKey,
        );
        return request ? { ...item, restore: request, restoreStatus: request.status } : item;
      });
      for (const operationId of result.operationIds) {
        focusByOperation.current.set(operationId, Boolean(focusRestored));
      }
      patchSessionArchives(botId, {
        isSubmitting: false,
        items: updated,
        operationIds: Array.from(new Set([...(archiveNow?.operationIds ?? []), ...result.operationIds])),
      });
      void refreshNormalSessions(view, origin, userIdRef.current);
      return result;
    },
    [host],
  );

  const preview = useCallback<ConversationSessionArchivesModel['preview']>(
    async (input: { view: ConversationBotView; item: CodingBotSessionArchiveItem }) => {
      const { view, item } = input;
      const currentUserId = userIdRef.current;
      if (!host || !view.bot.isAgentCodingBot) {
        return { ok: false, message: '当前部署未提供 Coding Bot 归档摘要能力。' };
      }
      return host.preview({
        ...baseInput(view.bot, currentUserId || ''),
        sessionKey: item.sessionKey,
        archiveId: item.archiveId,
      });
    },
    [host],
  );

  useConversationSessionArchivePolling({
    host,
    botByBotId,
    userIdRef,
    focusByOperation,
    onRestoredSessionFocusRef,
  });

  // Archive is lazy: only current selected Coding Bot + archived scope triggers the first fetch.
  const effectKey = useConversationStore((state) =>
    JSON.stringify(
      Object.keys(state.expandedBotIds)
        .filter((botId) => state.selectedBotId === botId && (state.effectiveScopeByManagedBotId[botId] ?? 'all') === 'archived')
        .map((botId) => [
          botId,
          state.selectedSection,
          state.originByManagedBotId[botId],
          state.effectiveScopeByManagedBotId[botId],
          state.sessionFilterRevisionByBotId[botId],
        ]),
    ),
  );
  useEffect(() => {
    const store = useConversationStore.getState();
    for (const botId of Object.keys(store.expandedBotIds)) {
      const view = botByBotId.get(botId);
      if (
        view?.bot.isAgentCodingBot &&
        isManagedConversationSection(view.section) &&
        store.selectedBotId === botId &&
        store.selectedSection === view.section &&
        (store.effectiveScopeByManagedBotId[botId] ?? 'all') === 'archived'
      ) {
        void load(view, false);
      }
    }
  }, [botByBotId, effectKey, load]);

  return {
    host,
    load,
    retry: (view: ConversationBotView) => load(view, false),
    restore,
    preview,
  };
}

export type { ConversationSessionScope };
export { emptySessions };
