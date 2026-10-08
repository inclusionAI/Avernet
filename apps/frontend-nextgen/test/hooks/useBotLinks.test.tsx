/** @jest-environment jsdom */
import { useBotLinks } from '@/hooks/useBotLinks';
import { botLinkService } from '@/services/botWorkshop/botLinkService';
import { act, renderHook, waitFor } from '@testing-library/react';
jest.mock('@/services/botWorkshop/botLinkService', () => ({ botLinkService: { list: jest.fn(), add: jest.fn() } }));
jest.mock('sonner', () => ({ toast: { success: jest.fn(), error: jest.fn() } }));
it('同步失败后重新加载服务端，反映已经持久化的部分结果', async () => {
  const service = botLinkService as jest.Mocked<typeof botLinkService>;
  service.list.mockResolvedValue([]);
  service.add.mockRejectedValue(new Error('sync failed'));
  const { result } = renderHook(() => useBotLinks('bot-1'));
  await waitFor(() => expect(result.current.loading).toBe(false));
  await act(async () => {
    await expect(result.current.add([])).rejects.toThrow('sync failed');
  });
  await waitFor(() => expect(service.list).toHaveBeenCalledTimes(2));
});
