// Coding Bot 归档列表的同步态助手。请求仍保留在 capability host 内，
// 这里只负责把跨边界结果落到 Conversation Store，不解析 sessionKey。
import type {
  CodingBotRestoredSessionItem,
  CodingBotSessionArchiveItem,
  ConversationSessionArchiveListState,
} from '@/domain/conversation/types';
import { useConversationStore } from '@/stores/conversationStore';

export const emptySessionArchives = (): ConversationSessionArchiveListState => ({
  items: [],
  nextCursor: null,
  catalogRevision: null,
  loading: false,
  error: null,
  isLoadingMore: false,
  loadMoreError: null,
  availability: 'unknown',
  restoreAvailability: 'unknown',
  operationIds: [],
  isSubmitting: false,
  restoreError: null,
  restoredItems: [],
});

function currentArchive(botId: string): ConversationSessionArchiveListState {
  return useConversationStore.getState().sessionsByBotId[botId]?.sessionArchives ?? emptySessionArchives();
}

export function patchSessionArchives(
  botId: string,
  patch:
    | Partial<ConversationSessionArchiveListState>
    | ((current: ConversationSessionArchiveListState) => Partial<ConversationSessionArchiveListState>),
): void {
  const store = useConversationStore.getState();
  const origin = store.originByManagedBotId[botId] ?? 'mine';
  const scope = store.effectiveScopeByManagedBotId[botId] ?? 'all';
  const prev = store.sessionsByBotId[botId];
  const patchValue = patch instanceof Function ? patch(currentArchive(botId)) : patch;
  // `undefined` means "keep the current field"; a direct spread would otherwise
  // overwrite cancelable polling IDs with undefined while a restore is still running.
  const archives = Object.fromEntries(
    Object.entries(patchValue).filter(([, value]) => value !== undefined),
  );
  useConversationStore.getState().setManagedBotCache(botId, {
    botId,
    origin,
    scope,
    sessions: prev?.sessions ?? { items: [], page: 1, total: 0, hasMore: false, loading: false, error: null, isLoadingMore: false, loadMoreError: null },
    sessionArchives: { ...currentArchive(botId), ...archives },
    friendDirectory: prev?.friendDirectory ?? { items: [], loading: false, error: null },
    friendGroups: prev?.friendGroups ?? {},
  });
}

export function mergeArchiveItem(
  previous: CodingBotSessionArchiveItem | undefined,
  next: CodingBotSessionArchiveItem,
): CodingBotSessionArchiveItem {
  if (!previous) return next;
  if (
    previous.restore &&
    (!next.restore ||
      next.restore.sequence < previous.restore.sequence ||
      (next.restore.operationId === previous.restore.operationId &&
        ['succeeded', 'failed'].includes(previous.restoreStatus) &&
        !['succeeded', 'failed'].includes(next.restoreStatus)))
  ) {
    return { ...next, restore: previous.restore, restoreStatus: previous.restoreStatus };
  }
  return {
    ...next,
    title: next.title ?? previous.title,
    sessionKind: next.sessionKind ?? previous.sessionKind,
    createdAt: next.createdAt ?? previous.createdAt,
    updatedAt: next.updatedAt ?? previous.updatedAt,
    archivedAt: next.archivedAt ?? previous.archivedAt,
    lastActiveAt: next.lastActiveAt ?? previous.lastActiveAt,
    messageCount: next.messageCount ?? previous.messageCount,
    restoredAt: next.restoredAt ?? previous.restoredAt,
  };
}

export function mergeSessionArchiveList(
  previous: CodingBotSessionArchiveItem[],
  page: CodingBotSessionArchiveItem[],
): CodingBotSessionArchiveItem[] {
  const byId = new Map(previous.map((item) => [item.archiveId, item]));
  for (const item of page) byId.set(item.archiveId, mergeArchiveItem(byId.get(item.archiveId), item));
  const sameSessionNewArchiveKeys = new Set(
    page
      .filter((item) => previous.some((old) => old.sessionKey === item.sessionKey && old.archiveId !== item.archiveId))
      .map((item) => item.sessionKey),
  );
  for (const item of previous) {
    // Same session archived again under a new archiveId replaces the old stable
    // archive, unless its restore is still queued/running and must remain visible.
    const replaced = sameSessionNewArchiveKeys.has(item.sessionKey);
    if (replaced && item.restoreStatus !== 'queued' && item.restoreStatus !== 'restoring') {
      byId.delete(item.archiveId);
    }
  }
  return [...byId.values()];
}

export function restoredItemsWith(items: CodingBotRestoredSessionItem[]): CodingBotRestoredSessionItem[] {
  return items.filter((item, index) => items.findIndex((other) => other.sessionKey === item.sessionKey) === index);
}
