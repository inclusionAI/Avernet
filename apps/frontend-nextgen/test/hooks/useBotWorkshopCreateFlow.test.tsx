/** @jest-environment jsdom */
import { useBotWorkshopCreateFlow } from '@/hooks/useBotWorkshopCreateFlow';
import { reconcileOwnedBotGrants } from '@/services/backendApi/collaboration/botGrantReconciliationController';
import { botWorkshopService } from '@/services/botWorkshop';
import { act, renderHook } from '@testing-library/react';

jest.mock('@/services/backendApi/collaboration/botGrantReconciliationController', () => ({
  reconcileOwnedBotGrants: jest.fn(),
}));
jest.mock('@/services/botWorkshop', () => ({
  botWorkshopService: { create: jest.fn() },
  BackendRequestError: class extends Error {},
}));
jest.mock('@/hooks/useBotCreateAuthorization', () => ({
  useBotCreateAuthorization: () => ({
    authorization: undefined,
    beginAuthorization: jest.fn(),
    cancelAuthorization: jest.fn(),
  }),
}));
jest.mock('sonner', () => ({
  toast: { success: jest.fn(), warning: jest.fn(), error: jest.fn() },
}));

test('schedules one-Bot compensation after creation without delaying list refresh', async () => {
  jest.mocked(botWorkshopService.create).mockResolvedValue({
    type: 'created',
    bot: { id: 'bot-1', name: 'Created Bot' },
  } as Awaited<ReturnType<typeof botWorkshopService.create>>);
  jest.mocked(reconcileOwnedBotGrants).mockReturnValue(new Promise(() => {}));
  const load = jest.fn().mockResolvedValue(undefined);
  const setCreateScenario = jest.fn();
  const { result } = renderHook(() => useBotWorkshopCreateFlow({ load, setCreateScenario }));

  await act(async () => {
    await result.current.submitCreate({ name: 'Created Bot' } as never);
  });

  expect(reconcileOwnedBotGrants).toHaveBeenCalledWith('bot-1');
  expect(load).toHaveBeenCalledTimes(1);
  expect(result.current.creating).toBe(false);
});
