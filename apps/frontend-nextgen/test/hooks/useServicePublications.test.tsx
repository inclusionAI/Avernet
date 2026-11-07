/** @jest-environment jsdom */
import { useServicePublications } from '@/hooks/useServicePublications';
import { botEditorService } from '@/services/botWorkshop/botEditorService';
import { act, renderHook, waitFor } from '@testing-library/react';

jest.mock('@/services/botWorkshop/botEditorService', () => ({
  botEditorService: {
    listLifecycle: jest.fn(), advanceLifecycle: jest.fn(), restartLifecycle: jest.fn(),
    cancelStaging: jest.fn(), offlineLifecycle: jest.fn(), retryLifecycle: jest.fn(),
    upgradeLifecycle: jest.fn(), deleteLifecycleDraft: jest.fn(),
  },
}));
jest.mock('sonner', () => ({ toast: { success: jest.fn(), error: jest.fn() } }));

beforeEach(() => {
  jest.clearAllMocks();
  (botEditorService.listLifecycle as jest.Mock).mockResolvedValue([]);
});

test('发布查询及所有操作始终透传 Bot owner，切换 owner 后重新查询', async () => {
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
    await result.current.retry();
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
