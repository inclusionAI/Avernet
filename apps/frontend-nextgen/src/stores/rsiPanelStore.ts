/** Presentation only. A Bot's RSI runs are shared across its chat sessions. */
import { createRsiRequestKey } from '@/utils/rsiRequestKey';
import type { RsiScope } from '@/types/rsi';
import { create } from 'zustand';
import { createJSONStorage, persist } from 'zustand/middleware';
export type { RsiScope } from '@/types/rsi';
export function rsiScopeKey(scope: RsiScope): string {
  return JSON.stringify([scope.ownerId, scope.botId]);
}
interface PendingAttempt {
  key: string;
  bindingId: string;
}
interface RsiPanelState {
  scope?: RsiScope;
  attempts: Record<string, PendingAttempt>;
  open(scope: RsiScope): void;
  close(): void;
  beginAttempt(scope: RsiScope, bindingId: string): PendingAttempt;
  confirmAttempt(scope: RsiScope, key: string): void;
}
export const useRsiPanelStore = create<RsiPanelState>()(
  persist(
    (set, get) => ({
      scope: undefined,
      attempts: {},
      open: (scope) => set({ scope }),
      close: () => set({ scope: undefined }),
      beginAttempt: (scope, bindingId) => {
        const scopeKey = rsiScopeKey(scope);
        const existing = get().attempts[scopeKey];
        if (existing) return existing;
        const key = createRsiRequestKey();
        const attempt = { key, bindingId };
        set({ attempts: { ...get().attempts, [scopeKey]: attempt } });
        return attempt;
      },
      confirmAttempt: (scope, key) => {
        const scopeKey = rsiScopeKey(scope);
        if (get().attempts[scopeKey]?.key !== key) return;
        const attempts = { ...get().attempts };
        delete attempts[scopeKey];
        set({ attempts });
      },
    }),
    {
      name: 'avernet-rsi-submissions-v1',
      storage: createJSONStorage(() => sessionStorage),
      // No credentials, config drafts or server state; only uncertain submission IDs.
      partialize: (state) => ({ attempts: state.attempts }),
    },
  ),
);
