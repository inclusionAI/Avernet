/** @jest-environment jsdom */
import { useBotAdvancedConfig } from '@/hooks/useBotAdvancedConfig';
import type { BotRuntimeStage } from '@/domain/botWorkshop';
import { botAdvancedConfigService } from '@/services/botWorkshop/botAdvancedConfigService';
import { act, renderHook, waitFor } from '@testing-library/react';

jest.mock('@/services/botWorkshop/botAdvancedConfigService', () => ({
  botAdvancedConfigService: { listIdentityFiles: jest.fn(), getIdentityFile: jest.fn(), saveIdentityFile: jest.fn() },
}));
jest.mock('sonner', () => ({ toast: { success: jest.fn(), error: jest.fn() } }));

beforeEach(() => {
  jest.clearAllMocks();
  jest.mocked(botAdvancedConfigService.listIdentityFiles).mockResolvedValue([{ type: 'RULES', exists: true }]);
  jest.mocked(botAdvancedConfigService.getIdentityFile).mockResolvedValue('# rules');
  jest.mocked(botAdvancedConfigService.saveIdentityFile).mockResolvedValue({ code: 200000, data: {} });
});

test.each(['draft', 'verify', 'online'] as const)('MD 列表、读取、刷新共用 Bot Owner 和 %s 阶段', async (stage) => {
  const { result } = renderHook(() => useBotAdvancedConfig('shared', true, 'owner', true, false, stage));
  await waitFor(() => expect(result.current.loading).toBe(false));
  expect(botAdvancedConfigService.listIdentityFiles).toHaveBeenCalledWith('shared', 'owner', stage);
  await expect(result.current.getFile('RULES')).resolves.toBe('# rules');
  await result.current.getFile('SOUL');
  expect(botAdvancedConfigService.getIdentityFile).toHaveBeenCalledWith('shared', 'RULES', 'owner', stage);
  expect(botAdvancedConfigService.getIdentityFile).toHaveBeenCalledWith('shared', 'SOUL', 'owner', stage);
});

test('草稿保存保留 owner；切换 owner/运行阶段后回调不使用旧闭包，且拒绝运行态保存', async () => {
  const { result, rerender } = renderHook(
    ({ owner, stage }: { owner: string; stage: BotRuntimeStage }) => useBotAdvancedConfig('shared', true, owner, true, false, stage),
    { initialProps: { owner: 'first-owner', stage: 'draft' as BotRuntimeStage } },
  );
  await waitFor(() => expect(result.current.loading).toBe(false));
  await act(async () => { await result.current.saveFile('RULES', '# changed'); });
  expect(botAdvancedConfigService.saveIdentityFile).toHaveBeenCalledWith('shared', 'RULES', '# changed', 'first-owner');

  rerender({ owner: 'new-owner', stage: 'online' });
  await waitFor(() => expect(botAdvancedConfigService.listIdentityFiles).toHaveBeenLastCalledWith('shared', 'new-owner', 'online'));
  await result.current.getFile('RULES');
  expect(botAdvancedConfigService.getIdentityFile).toHaveBeenLastCalledWith('shared', 'RULES', 'new-owner', 'online');
  await expect(result.current.saveFile('RULES', 'must not write')).rejects.toThrow('仅支持查看');
  expect(botAdvancedConfigService.saveIdentityFile).toHaveBeenCalledTimes(1);
});
