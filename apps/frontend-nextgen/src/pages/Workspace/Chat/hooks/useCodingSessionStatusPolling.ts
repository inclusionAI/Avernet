import type {
  CodingBotSessionStatusHost,
  CodingBotSessionStatusQueryResult,
} from '@/capabilities';
import { useConversationStore } from '@/stores/conversationStore';
import { useSessionStatusStore } from '@/stores/sessionStatusStore';
import { useEffect, useRef } from 'react';
import { codingSessionStatusListSnapshot, knownSessionIds } from './conversationSessionStatusKnownIds';

const SESSION_STATUS_POLL_INTERVAL_MS = 20_000;

export interface CodingBotSessionStatusTarget {
  botId: string;
  realBotId: string;
  ownerId?: string;
}

let statusContextSequence = 0;

function isErrorCancelled(error: unknown, signal: AbortSignal): boolean {
  return signal.aborted || (error instanceof DOMException && error.name === 'AbortError');
}

export function useCodingSessionStatusPolling({
  activeBot,
  userId,
  provider,
}: {
  activeBot: CodingBotSessionStatusTarget | null;
  userId: string | null;
  provider: CodingBotSessionStatusHost | null;
}): void {
  const activeBotRef = useRef(activeBot);
  activeBotRef.current = activeBot;
  const botIdentity = activeBot ? [activeBot.botId, activeBot.realBotId, activeBot.ownerId ?? ''].join(':') : '';
  const ownerId = activeBot?.ownerId ?? '';
  const realBotId = activeBot?.realBotId ?? '';
  const viewBotId = activeBot?.botId ?? '';

  useEffect((): (() => void) | undefined => {
    if (!botIdentity || !userId || !provider || !viewBotId || !realBotId) return undefined;

    const contextId = `coding-session-status-${++statusContextSequence}`;
    const controller = new AbortController();
    let inFlight = false;
    let pendingListRefresh = false;
    let timer: ReturnType<typeof setInterval> | null = null;
    let unsubscribe: (() => void) | undefined;
    const currentListSnapshot = () =>
      codingSessionStatusListSnapshot(useConversationStore.getState(), viewBotId);
    let lastListFingerprint = currentListSnapshot().fingerprint;

    const queryOnce = async () => {
      if (controller.signal.aborted) return;
      const bot = activeBotRef.current;
      if (!bot || bot.botId !== viewBotId || bot.realBotId !== realBotId || bot.ownerId !== ownerId) return;
      if (inFlight) return;
      inFlight = true;
      let result: CodingBotSessionStatusQueryResult | null = null;
      try {
        result = await provider.query({
          viewBotId: bot.botId,
          botId: bot.realBotId,
          userId,
          ownerId: bot.ownerId,
          sessionIds: [...knownSessionIds(bot.botId)],
          signal: controller.signal,
        });
      } catch (error) {
        if (!isErrorCancelled(error, controller.signal)) {
          console.warn('[CodingSessionStatus] query failed:', error);
        }
      } finally {
        inFlight = false;
        if (pendingListRefresh && !controller.signal.aborted) {
          pendingListRefresh = false;
          void queryOnce();
        }
      }
      if (controller.signal.aborted || !result?.ok) return;

      const knownIds = knownSessionIds(viewBotId);
      useSessionStatusStore
        .getState()
        .patch(
          contextId,
          result.items.filter((item) => knownIds.has(item.sessionId)),
          knownIds,
          { snapshot: true },
        );
      useSessionStatusStore.getState().commit(contextId);
    };

    useSessionStatusStore.getState().begin(contextId, viewBotId);
    void queryOnce();

    timer = setInterval(() => {
      if (typeof document !== 'undefined' && document.hidden) return;
      void queryOnce();
    }, SESSION_STATUS_POLL_INTERVAL_MS);

    unsubscribe = useConversationStore.subscribe(() => {
      useSessionStatusStore.getState().patch(contextId, [], knownSessionIds(viewBotId));
      const snapshot = currentListSnapshot();
      if (!snapshot.queryable || snapshot.fingerprint === lastListFingerprint) return;
      lastListFingerprint = snapshot.fingerprint;
      if (inFlight) {
        pendingListRefresh = true;
        return;
      }
      void queryOnce();
    });

    const handleVisibilityChange = () => {
      if (document.hidden || controller.signal.aborted) return;
      void queryOnce();
    };
    document.addEventListener('visibilitychange', handleVisibilityChange);

    return () => {
      controller.abort();
      if (timer) clearInterval(timer);
      unsubscribe?.();
      document.removeEventListener('visibilitychange', handleVisibilityChange);
      useSessionStatusStore.getState().end(contextId);
    };
  }, [botIdentity, provider, realBotId, userId, viewBotId, ownerId]);
}
