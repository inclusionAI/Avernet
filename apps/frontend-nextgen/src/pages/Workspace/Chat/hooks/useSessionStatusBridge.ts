import { createSessionStatusReceiver } from '@/domain/conversation/sessionStatusReceiver';
import { useConversationStore } from '@/stores/conversationStore';
import { useSessionStatusStore } from '@/stores/sessionStatusStore';
import { useCallback, useLayoutEffect, useMemo, useRef } from 'react';

let subscriptionSequence = 0;
function knownSessionIds(botId: string): Set<string> {
  const state = useConversationStore.getState();
  const managed = state.sessionsByBotId[botId];
  return new Set(
    [
      ...(managed?.sessions.items ?? []),
      ...Object.values(managed?.friendGroups ?? {}).flatMap((group) => group.sessions.items),
      ...(state.friendBotSessionsByBotId[botId]?.items ?? []),
    ].map((session) => session.sessionId),
  );
}

/** Caller supplies the real runtime identity, never sidebar expansion or session selection. */
export function useSessionStatusBridge(viewBotId: string | undefined, realBotId: string, runtimeIdentity: string) {
  const instance = useConversationStore((state) => (viewBotId ? state.instanceSelectionByBotId[viewBotId] : undefined));
  const latest = useRef<object>();
  const bridge = useMemo(() => {
    const contextId = `session-status-${++subscriptionSequence}`;
    const receiver = createSessionStatusReceiver(contextId, realBotId, (event) => {
      if (!viewBotId) return;
      const store = useSessionStatusStore.getState();
      if (event.type === 'reset') {
        store.patch(
          contextId,
          Object.keys(store.statuses).map((sessionId) => ({ sessionId, status: null })),
          knownSessionIds(viewBotId),
          { resetSnapshot: true },
        );
      } else {
        store.patch(
          contextId,
          event.items.map((item) => ({ ...item, status: item.status?.code === 'unknown' ? null : item.status })),
          knownSessionIds(viewBotId),
          // v1 children mark every patch with the snapshot flag. A missing
          // field identifies an older bridge build; its first patch is still an
          // initial status result, so promote it once and do not wait forever.
          { snapshot: event.snapshot === undefined || event.snapshot === true },
        );
      }
    });
    const result = {
      contextId,
      receiver,
      receive: (event: unknown) => {
        if (latest.current === result) receiver.receive(event);
      },
    };
    return result;
  }, [viewBotId, realBotId, runtimeIdentity, instance]);
  latest.current = bridge;
  const commitSessionStatuses = useCallback(() => {
    useSessionStatusStore.getState().commit(bridge.contextId);
  }, [bridge.contextId]);
  useLayoutEffect(() => {
    if (!viewBotId) return undefined;
    useSessionStatusStore.getState().begin(bridge.contextId, viewBotId);
    bridge.receiver.activate();
    const unsubscribe = useConversationStore.subscribe(() => {
      useSessionStatusStore.getState().patch(bridge.contextId, [], knownSessionIds(viewBotId));
    });
    return () => {
      bridge.receiver.dispose();
      unsubscribe();
      useSessionStatusStore.getState().end(bridge.contextId);
    };
  }, [bridge, viewBotId]);
  return {
    sessionStatusContextId: bridge.contextId,
    onSessionStatusEvent: bridge.receive,
    commitSessionStatuses,
  };
}
