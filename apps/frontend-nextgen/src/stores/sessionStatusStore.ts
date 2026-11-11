import type { SessionStatusPresentation } from '@/domain/conversation/sessionStatusProtocol';
import { create } from 'zustand';

interface SessionStatusState {
  contextId: string | null;
  botId: string | null;
  statuses: Record<string, SessionStatusPresentation>;
  /** Coding 子应用 surface ready 后，缓存状态才对宿主会话行可见。 */
  ready: boolean;
  /** Coding 子应用是否已发布首个 session 状态快照。 */
  snapshotReady: boolean;
  begin(contextId: string, botId: string): void;
  end(contextId: string): void;
  commit(contextId: string): void;
  patch(
    contextId: string,
    items: Array<{ sessionId: string; status: SessionStatusPresentation | null }>,
    known: Set<string>,
    options?: { snapshot?: boolean; resetSnapshot?: boolean },
  ): void;
}
/**
 * Ephemeral single-surface cache; never persisted across runtime/user changes.
 * It buffers only a child session-status page until host rows exist, then stays
 * bounded by those known rows.
 */
export const useSessionStatusStore = create<SessionStatusState>((set) => ({
  contextId: null,
  botId: null,
  ready: false,
  snapshotReady: false,
  statuses: {},
  begin: (contextId, botId) => set({ contextId, botId, ready: false, snapshotReady: false, statuses: {} }),
  end: (contextId) =>
    set((state) =>
      state.contextId === contextId
        ? { contextId: null, botId: null, ready: false, snapshotReady: false, statuses: {} }
        : state,
    ),
  commit: (contextId) =>
    set((state) => (state.contextId === contextId && !state.ready ? { ...state, ready: true } : state)),
  patch: (contextId, items, known, options) =>
    set((state) => {
      if (state.contextId !== contextId) return state;
      const statuses = { ...state.statuses };
      const snapshotReady =
        options?.snapshot === true ? true : options?.resetSnapshot === true ? false : state.snapshotReady;
      // A child can win the race against the host's first page load. Buffer only
      // the initial snapshot while no host rows exist: there is nothing to render,
      // so the store cannot create or expose rows from it. Once rows exist, the
      // normal authoritative known-row filter resumes.
      const hasKnownRows = known.size > 0;
      const canBufferInitialSnapshot = !hasKnownRows && options?.snapshot === true && !state.snapshotReady;
      let changed = false;
      if (canBufferInitialSnapshot) {
        const incomingIds = new Set(items.map((item) => item.sessionId));
        for (const id of Object.keys(statuses)) {
          if (!incomingIds.has(id)) {
            delete statuses[id];
            changed = true;
          }
        }
      } else {
        for (const id of Object.keys(statuses)) {
          if (!known.has(id)) {
            delete statuses[id];
            changed = true;
          }
        }
      }
      for (const { sessionId, status } of items) {
        if (!canBufferInitialSnapshot && !known.has(sessionId)) continue;
        const previous = statuses[sessionId];
        if (!status) {
          if (previous) {
            delete statuses[sessionId];
            changed = true;
          }
        } else if (
          !previous ||
          previous.code !== status.code ||
          previous.label !== status.label ||
          previous.tone !== status.tone
        ) {
          Object.defineProperty(statuses, sessionId, {
            value: status,
            enumerable: true,
            configurable: true,
            writable: true,
          });
          changed = true;
        }
      }
      return changed || snapshotReady !== state.snapshotReady ? { ...state, statuses, snapshotReady } : state;
    }),
}));
