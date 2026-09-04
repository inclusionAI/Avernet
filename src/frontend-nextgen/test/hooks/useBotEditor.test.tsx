/** @jest-environment jsdom */
import { useBotEditor } from '@/hooks/useBotEditor';
import { botEditorService } from '@/services/botWorkshop/botEditorService';
import { renderHook, waitFor } from '@testing-library/react';
import { toast } from 'sonner';

jest.mock('@/services/botWorkshop/botEditorService', () => ({
  botEditorService: { load: jest.fn() },
}));
jest.mock('sonner', () => ({
  toast: { error: jest.fn(), success: jest.fn(), warning: jest.fn() },
}));

const mockedLoad = botEditorService.load as jest.MockedFunction<typeof botEditorService.load>;

afterEach(() => jest.clearAllMocks());

it('编辑配置加载异常后结束 loading，避免页面永久转圈', async () => {
  mockedLoad.mockRejectedValueOnce(new Error('能力集子资源加载失败'));

  const { result } = renderHook(() => useBotEditor('bot-1'));

  await waitFor(() => expect(result.current.loading).toBe(false));
  expect(toast.error).toHaveBeenCalledWith('能力集子资源加载失败');
});

it('等待 Bot 详情就绪后使用 Owner 加载本地 Skill', async () => {
  mockedLoad.mockRejectedValue(new Error('test load stopped'));
  const initialProps: { enabled: boolean; ownerId?: string } = { enabled: false };
  const { rerender } = renderHook(
    ({ enabled, ownerId }: { enabled: boolean; ownerId?: string }) =>
      useBotEditor('bot-1', false, 'space-1', enabled, ownerId),
    { initialProps },
  );
  expect(mockedLoad).not.toHaveBeenCalled();
  rerender({ enabled: true, ownerId: 'owner-1' });
  await waitFor(() => expect(mockedLoad).toHaveBeenCalledWith('bot-1', false, 'owner-1'));
});
