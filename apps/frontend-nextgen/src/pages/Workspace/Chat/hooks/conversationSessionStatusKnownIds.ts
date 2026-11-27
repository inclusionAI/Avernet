import type { ConversationOrigin, ConversationSessionScope } from '@/domain/conversation/types';
import { useConversationStore } from '@/stores/conversationStore';

interface KnownConversationSessionList {
  items?: Array<{ sessionId: string }>;
  loading?: boolean;
  isLoadingMore?: boolean;
  error?: string | null;
}

interface KnownConversationSessions {
  origin?: ConversationOrigin;
  scope?: ConversationSessionScope;
  sessions?: KnownConversationSessionList;
  friendGroups?: Record<string, { sessions: KnownConversationSessionList; state?: string }>;
}

interface ConversationStatusStoreState {
  sessionsByBotId?: Record<string, KnownConversationSessions | undefined>;
  friendBotSessionsByBotId?: Record<string, KnownConversationSessionList | undefined>;
  originByManagedBotId?: Record<string, ConversationOrigin | undefined>;
  effectiveScopeByManagedBotId?: Record<string, ConversationSessionScope | undefined>;
  scopeByFriendBotId?: Record<string, ConversationSessionScope | undefined>;
  sessionKeyByBotId?: Record<string, string | undefined>;
  sessionFilterRevisionByBotId?: Record<string, number | undefined>;
}

export interface CodingSessionStatusListSnapshot {
  sessionIds: Set<string>;
  fingerprint: string;
  queryable: boolean;
}

/**
 * Full session keys are the only valid status key. They are collected from both
 * the managed main list and per-friend groups, so mine/others share one status
 * surface without copying grouped-session knowledge into status code.
 */
export function collectKnownSessionIds(state: ConversationStatusStoreState, botId: string): Set<string> {
  const managed = state.sessionsByBotId?.[botId];
  const friendSessions = state.friendBotSessionsByBotId?.[botId];
  return new Set(
    [
      ...(managed?.sessions?.items ?? []),
      ...Object.values(managed?.friendGroups ?? {}).flatMap((group) => group.sessions.items ?? []),
      ...(friendSessions?.items ?? []),
    ].map((session) => session.sessionId),
  );
}

/**
 * A stable list snapshot drives immediate status refresh inside one expanded Bot.
 * It changes for mine/others/collab filter transitions and for every loaded-page
 * ID change, but does not create request pressure while the replacement list is
 * still loading.
 */
export function codingSessionStatusListSnapshot(
  state: ConversationStatusStoreState,
  botId: string,
): CodingSessionStatusListSnapshot {
  const managed = state.sessionsByBotId?.[botId];
  const friendSessions = state.friendBotSessionsByBotId?.[botId];
  const managedOrigin = state.originByManagedBotId?.[botId] ?? 'mine';
  const managedScope = state.effectiveScopeByManagedBotId?.[botId] ?? 'all';
  const friendScope = state.scopeByFriendBotId?.[botId] ?? 'all';
  const sessionKey = state.sessionKeyByBotId?.[botId] ?? '';
  const revision = state.sessionFilterRevisionByBotId?.[botId] ?? 0;
  const primary = managed ? managed.sessions : friendSessions;
  const criteriaMatch = !managed || (managed.origin === managedOrigin && managed.scope === managedScope);
  const settled = !primary?.loading && !primary?.isLoadingMore && (primary?.error ?? null) === null;
  const sessionIds = collectKnownSessionIds(state, botId);
  const fingerprint = JSON.stringify([
    managed ? managedOrigin : 'friend',
    managed ? managedScope : friendScope,
    sessionKey,
    revision,
    [...sessionIds].sort(),
  ]);
  return {
    sessionIds,
    fingerprint,
    queryable: Boolean(primary) && criteriaMatch && settled,
  };
}

export function knownSessionIds(botId: string): Set<string> {
  return collectKnownSessionIds(useConversationStore.getState(), botId);
}
