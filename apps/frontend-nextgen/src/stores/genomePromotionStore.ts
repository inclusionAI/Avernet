/** Only uncertain operation identities are persisted, never credentials or captured Bot content. */
import type { RsiScope } from '@/types/rsi';
import { create } from 'zustand';
import { createJSONStorage, persist } from 'zustand/middleware';

export interface GenomeAttempt {
  revision: string;
  expectedRevision: string;
  promotionId: string | null;
  correlationId: string;
}
export const genomeOperationTag = (attempt: Pick<GenomeAttempt, 'correlationId'>) =>
  `[client-operation:${attempt.correlationId}]`;
export const genomeScopeKey = (scope: RsiScope) => JSON.stringify([scope.ownerId, scope.botId, scope.actorId ?? '']);
interface GenomePromotionState {
  attempts: Record<string, GenomeAttempt>;
  begin(scope: RsiScope, attempt: Omit<GenomeAttempt, 'correlationId'>): GenomeAttempt;
  track(scope: RsiScope, promotionId: string): void;
  settle(scope: RsiScope): void;
}
export const useGenomePromotionStore = create<GenomePromotionState>()(persist((set, get) => ({
  attempts: {},
  begin: (scope, attempt) => {
    const key = genomeScopeKey(scope);
    if (get().attempts[key]) throw new Error('A previous promotion is still unconfirmed');
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    const correlationId = Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
    const pending = { ...attempt, correlationId };
    set({ attempts: { ...get().attempts, [key]: pending } });
    return pending;
  },
  track: (scope, promotionId) => {
    const key = genomeScopeKey(scope);
    const attempt = get().attempts[key];
    if (attempt) set({ attempts: { ...get().attempts, [key]: { ...attempt, promotionId } } });
  },
  settle: (scope) => {
    const attempts = { ...get().attempts };
    delete attempts[genomeScopeKey(scope)];
    set({ attempts });
  },
}), {
  name: 'avernet-genome-promotions-v1',
  storage: createJSONStorage(() => sessionStorage),
  partialize: (state) => ({ attempts: state.attempts }),
}));
