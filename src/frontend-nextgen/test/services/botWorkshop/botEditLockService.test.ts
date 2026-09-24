import { botEditorController } from '@/services/backendApi/bots/botEditorController';
import { botEditLockService } from '@/services/botWorkshop/botEditLockService';
import { mapBotDto } from '@/services/botWorkshop/botMapper';

jest.mock('@/services/backendApi/bots/botEditorController', () => ({
  botEditorController: { acquireEditLock: jest.fn(), stealEditLock: jest.fn(), releaseEditLock: jest.fn() },
}));
const bot = mapBotDto({
  bot_id: 'bot-1',
  owner_entity_id: 'owner-1',
  bot_type: 'service',
  display_state: 'service_draft',
  engine: 'openclaw',
  edit_lock: { locked: false, need_lock: true },
}).item;
beforeEach(() => jest.clearAllMocks());

test('保留未持锁但需要锁的信息，正常获取锁而非强制抢锁', async () => {
  expect(bot.needsEditLock).toBe(true);
  expect(bot.lock).toBeUndefined();
  (botEditorController.acquireEditLock as jest.Mock).mockResolvedValue({ code: 200000, data: { acquired: true } });
  await botEditLockService.claim(bot);
  expect(botEditorController.acquireEditLock).toHaveBeenCalledWith('bot-1', 'owner-1');
  expect(botEditorController.stealEditLock).not.toHaveBeenCalled();
});
test('他人持锁时调用抢占接口', async () => {
  (botEditorController.stealEditLock as jest.Mock).mockResolvedValue({ code: 200000, data: { acquired: true } });
  await botEditLockService.claim({ ...bot, lock: { status: 'other' } });
  expect(botEditorController.stealEditLock).toHaveBeenCalledWith('bot-1', 'owner-1');
});
test.each([false, null, undefined])('acquired=%s 不得误报成功', async (acquired) => {
  (botEditorController.acquireEditLock as jest.Mock).mockResolvedValue({
    code: 200000,
    data: { acquired, need_lock: true },
  });
  await expect(botEditLockService.claim(bot)).rejects.toThrow('未获取到编辑锁');
});
test('协作者已移除无需锁时允许继续编辑', async () => {
  (botEditorController.acquireEditLock as jest.Mock).mockResolvedValue({
    code: 200000,
    data: { acquired: false, need_lock: false, locked: false },
  });
  await expect(botEditLockService.claim(bot)).resolves.toBeUndefined();
});
test('释放锁并校验 released 标志', async () => {
  (botEditorController.releaseEditLock as jest.Mock).mockResolvedValue({ code: 200000, data: { released: true } });
  await botEditLockService.release(bot);
  expect(botEditorController.releaseEditLock).toHaveBeenCalledWith('bot-1', 'owner-1');
  (botEditorController.releaseEditLock as jest.Mock).mockResolvedValue({ code: 200000, data: { released: false } });
  await expect(botEditLockService.release(bot)).rejects.toThrow('释放编辑锁失败');
});
test('业务错误信息透传', async () => {
  (botEditorController.acquireEditLock as jest.Mock).mockResolvedValue({ code: 403000, message: '无操作权限' });
  await expect(botEditLockService.claim(bot)).rejects.toThrow('无操作权限');
});
