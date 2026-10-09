/** @jest-environment jsdom */
import { useBotWorkshopLocks } from '@/hooks/useBotWorkshopLocks';
import { botEditLockService } from '@/services/botWorkshop/botEditLockService';
import { mapBotDto } from '@/services/botWorkshop/botMapper';
import { act, renderHook } from '@testing-library/react';
import { history } from '@umijs/max';

jest.mock('@umijs/max', () => ({ history: { push: jest.fn() } }));
jest.mock('sonner', () => ({ toast: { loading: jest.fn(), success: jest.fn(), error: jest.fn() } }));
jest.mock('@/services/botWorkshop/botEditLockService', () => ({
  botEditLockService: { claim: jest.fn(), release: jest.fn() },
}));
const bot = mapBotDto({
  bot_id: 'b1',
  owner_entity_id: 'owner-1',
  bot_type: 'service',
  display_state: 'service_draft',
  engine: 'openclaw',
}).item;
beforeEach(() => jest.clearAllMocks());
test('获取成功刷新后进入草稿编辑', async () => {
  (botEditLockService.claim as jest.Mock).mockResolvedValue(undefined);
  const load = jest.fn().mockResolvedValue(undefined);
  const { result } = renderHook(() => useBotWorkshopLocks(load));
  await act(async () => result.current.claimLock(bot));
  expect(load).toHaveBeenCalledWith({ silent: true });
  expect(history.push).toHaveBeenCalledWith(
    '/bot-workshop/detail?type=edit&id=b1&runtime_stage=draft&owner_id=owner-1',
  );
});
test('抢锁失败同步最新状态但不跳转', async () => {
  (botEditLockService.claim as jest.Mock).mockRejectedValue(new Error('被抢先持锁'));
  const load = jest.fn().mockResolvedValue(undefined);
  const { result } = renderHook(() => useBotWorkshopLocks(load));
  await act(async () => {
    await expect(result.current.claimLock(bot)).rejects.toThrow('被抢先持锁');
  });
  expect(load).toHaveBeenCalledWith({ silent: true });
  expect(history.push).not.toHaveBeenCalled();
});
test('释放后同步状态并留在列表', async () => {
  (botEditLockService.release as jest.Mock).mockResolvedValue(undefined);
  const load = jest.fn().mockResolvedValue(undefined);
  const { result } = renderHook(() => useBotWorkshopLocks(load));
  await act(async () => result.current.releaseLock(bot));
  expect(load).toHaveBeenCalledWith({ silent: true });
  expect(history.push).not.toHaveBeenCalled();
});
