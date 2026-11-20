import { reconcileOwnedBotGrants } from '@/services/collaboration/botGrantReconciliationService';
import { useEffect } from 'react';
import { useHumanIdentity } from './useHumanIdentity';

const reconciledUsers = new Set<string>();

/** Trigger one silent full-fleet reconciliation when a Human identity is ready. */
export function useBotGrantReconciliation(): void {
  const { identity, status } = useHumanIdentity();

  useEffect(() => {
    const userId = identity?.userId;
    if (status !== 'ready' || !userId || reconciledUsers.has(userId)) return;
    reconciledUsers.add(userId);
    void reconcileOwnedBotGrants();
  }, [identity?.userId, status]);
}
