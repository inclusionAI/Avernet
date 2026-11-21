/** @jest-environment jsdom */
import { useBotGrantReconciliation } from '@/hooks/useBotGrantReconciliation';
import { useHumanIdentity } from '@/hooks/useHumanIdentity';
import { reconcileOwnedBotGrants } from '@/services/collaboration/botGrantReconciliationService';
import { renderHook, waitFor } from '@testing-library/react';

jest.mock('@/hooks/useHumanIdentity', () => ({ useHumanIdentity: jest.fn() }));
jest.mock('@/services/collaboration/botGrantReconciliationService', () => ({
  reconcileOwnedBotGrants: jest.fn(),
}));

const identity = jest.mocked(useHumanIdentity);
const reconcile = jest.mocked(reconcileOwnedBotGrants);

test('reconciles once per ready Human identity without blocking rendering', async () => {
  identity.mockReturnValue({
    status: 'ready',
    identity: { userId: 'u-1', displayName: 'User 1', online: true },
  });

  const { rerender } = renderHook(() => useBotGrantReconciliation());
  await waitFor(() => expect(reconcile).toHaveBeenCalledTimes(1));
  expect(reconcile).toHaveBeenLastCalledWith();

  rerender();
  expect(reconcile).toHaveBeenCalledTimes(1);

  identity.mockReturnValue({
    status: 'ready',
    identity: { userId: 'u-2', displayName: 'User 2', online: true },
  });
  rerender();
  await waitFor(() => expect(reconcile).toHaveBeenCalledTimes(2));
});
