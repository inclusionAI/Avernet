/** @jest-environment jsdom */
import { useServicePublications } from '@/hooks/useServicePublications';
import { botEditorService } from '@/services/botWorkshop/botEditorService';
import { act, renderHook, waitFor } from '@testing-library/react';

jest.mock('@/services/botWorkshop/botEditorService', () => ({
  botEditorService: {
    listLifecycle: jest.fn(),
    advanceLifecycle: jest.fn(),
    restartLifecycle: jest.fn(),
    cancelStaging: jest.fn(),
    offlineLifecycle: jest.fn(),
    retryLifecycle: jest.fn(),
    upgradeLifecycle: jest.fn(),
    deleteLifecycleDraft: jest.fn(),
  },
}));
jest.mock('sonner', () => ({ toast: { success: jest.fn(), error: jest.fn() } }));
const mockInvalidRetryAction = () => ({
  publicationId: 17,
  cardId: 'service:bot-1:17',
  version: 2,
  status: 'deploying',
  internalStatus: 'validating',
  availableActions: ['retry'],
  createdAt: '2026-08-26T10:00:00Z',
  updatedAt: '2026-08-26T10:00:00Z',
});

const mockRetryTarget = (publicationId: number = 18, cardId: string = 'service:bot-1:18') => ({
  publicationId,
  cardId,
  version: 2,
  status: 'deploying',
  internalStatus: 'failed',
  availableActions: ['retry'],
  createdAt: '2026-08-26T10:00:00Z',
  updatedAt: '2026-08-26T10:00:00Z',
});

beforeEach(() => {
  jest.clearAllMocks();
  (botEditorService.listLifecycle as jest.Mock).mockResolvedValue([]);
});

test('发布查询及所有操作始终透传 Bot owner，切换 owner 后重新查询', async () => {
  (botEditorService.listLifecycle as jest.Mock).mockResolvedValue([mockRetryTarget(17, 'service:bot-1:17')]);
  const { result, rerender } = renderHook(({ owner }) => useServicePublications('bot-1', owner), {
    initialProps: { owner: 'owner-1' },
  });
  await waitFor(() => expect(result.current.loading).toBe(false));
  expect(botEditorService.listLifecycle).toHaveBeenCalledWith('bot-1', 'owner-1');
  await act(async () => {
    await result.current.advance('prestable');
    await result.current.restart('online');
    await result.current.cancel();
    await result.current.offline();
    await result.current.retry('service:bot-1:17');
    await result.current.upgrade(17);
    await result.current.deleteDraft();
  });
  expect(botEditorService.advanceLifecycle).toHaveBeenCalledWith('bot-1', 'prestable', 'owner-1');
  expect(botEditorService.restartLifecycle).toHaveBeenCalledWith('bot-1', 'online', 'owner-1');
  expect(botEditorService.upgradeLifecycle).toHaveBeenCalledWith('bot-1', 17, 'owner-1');
  for (const method of ['cancelStaging', 'offlineLifecycle', 'retryLifecycle', 'deleteLifecycleDraft'] as const) {
    expect(botEditorService[method]).toHaveBeenCalledWith('bot-1', 'owner-1');
  }
  rerender({ owner: 'owner-2' });
  await waitFor(() => expect(botEditorService.listLifecycle).toHaveBeenLastCalledWith('bot-1', 'owner-2'));
});

test('valid failed target calls retry for owner', async () => {
  (botEditorService.listLifecycle as jest.Mock).mockResolvedValue([
    { ...mockInvalidRetryAction(), cardId: 'service:bot-1:17' },
    mockRetryTarget(),
  ]);
  const { result } = renderHook(() => useServicePublications('bot-1', 'owner-1'));
  await waitFor(() => expect(result.current.loading).toBe(false));
  await act(async () => {
    await result.current.retry('service:bot-1:18');
  });
  expect(botEditorService.retryLifecycle).toHaveBeenCalledWith('bot-1', 'owner-1');
});

test('retry guard rejects non failed backend action', async () => {
  (botEditorService.listLifecycle as jest.Mock).mockResolvedValue([mockInvalidRetryAction(), mockRetryTarget()]);
  const { result } = renderHook(() => useServicePublications('bot-1', 'owner-1'));
  await waitFor(() => expect(result.current.loading).toBe(false));
  await act(async () => {
    await result.current.retry('service:bot-1:17');
  });
  expect(botEditorService.retryLifecycle).not.toHaveBeenCalled();
});

test('retry guard rejects target mismatch', async () => {
  (botEditorService.listLifecycle as jest.Mock).mockResolvedValue([mockRetryTarget()]);
  const { result } = renderHook(() => useServicePublications('bot-1', 'owner-1'));
  await waitFor(() => expect(result.current.loading).toBe(false));
  await act(async () => {
    await result.current.retry('service:bot-1:19');
  });
  expect(botEditorService.retryLifecycle).not.toHaveBeenCalled();
});
