/** @jest-environment jsdom */
import { useBotEngineConfig } from '@/hooks/useBotEngineConfig';
import { botEditorService } from '@/services/botWorkshop/botEditorService';
import { act, renderHook, waitFor } from '@testing-library/react';

jest.mock('@/services/botWorkshop/botEditorService', () => ({
  botEditorService: {
    loadEngineConfig: jest.fn(),
    loadDefaultEngineConfig: jest.fn(),
    saveEngineConfig: jest.fn(),
  },
}));
jest.mock('sonner', () => ({ toast: { error: jest.fn(), success: jest.fn() } }));

const service = botEditorService as jest.Mocked<typeof botEditorService>;

afterEach(() => jest.clearAllMocks());

it('协作者触发加载时也不请求敏感引擎配置', async () => {
  const { result } = renderHook(() => useBotEngineConfig('bot-1', 'owner-1', false));

  await act(async () => result.current.load());

  expect(service.loadEngineConfig).not.toHaveBeenCalled();
});

it('Owner 打开引擎配置时按需加载且同一页面只加载一次', async () => {
  service.loadEngineConfig.mockResolvedValue({ model: 'demo' });
  const { result } = renderHook(() => useBotEngineConfig('bot-1', 'owner-1', true));

  expect(service.loadEngineConfig).not.toHaveBeenCalled();
  await act(async () => result.current.load());
  await waitFor(() => expect(result.current.config).toEqual({ model: 'demo' }));
  await act(async () => result.current.load());

  expect(service.loadEngineConfig).toHaveBeenCalledTimes(1);
  expect(service.loadEngineConfig).toHaveBeenCalledWith('bot-1', 'owner-1');
});

it('Owner 恢复默认配置时读取默认值、写回配置并更新编辑器', async () => {
  service.loadDefaultEngineConfig.mockResolvedValue({ model: 'default-model' });
  service.saveEngineConfig.mockResolvedValue({} as never);
  const { result } = renderHook(() => useBotEngineConfig('bot-1', 'owner-1', true));

  await act(async () => result.current.restoreDefaults());

  expect(service.loadDefaultEngineConfig).toHaveBeenCalledWith('bot-1', 'owner-1');
  expect(service.saveEngineConfig).toHaveBeenCalledWith('bot-1', { model: 'default-model' }, 'owner-1');
  expect(result.current.config).toEqual({ model: 'default-model' });
});
