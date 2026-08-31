import { botEditorController } from '@/services/backendApi/bots/botEditorController';
import { botManagementService } from '@/services/botWorkshop/botManagementService';
import { mapBotDto } from '@/services/botWorkshop/botMapper';

jest.mock('@/services/backendApi/bots/botEditorController', () => ({
  botEditorController: { getEditLock: jest.fn(), stealEditLock: jest.fn() },
}));
jest.mock('@/services/backendApi/admin/spaceController', () => ({ listSpaces: jest.fn() }));
jest.mock('@/services/backendApi/bots/botCollaborationController', () => ({ botCollaborationController: {} }));
jest.mock('@/services/backendApi/bots/botController', () => ({ changeBotSpace: jest.fn() }));

const getEditLock = botEditorController.getEditLock as jest.MockedFunction<typeof botEditorController.getEditLock>;

test('只为草稿服务 Bot 加载锁，并映射持锁人和创建时间', async () => {
  getEditLock.mockResolvedValue({
    data: {
      locked: true,
      holder_user_id: 'u2',
      holder_name: '李四',
      created_at: '2026-08-26T10:00:00Z',
      has_collaborators: true,
      is_owner_holder: false,
      need_lock: true,
    },
  });
  const serviceBot = mapBotDto({
    bot_id: 'service-1',
    owner_entity_id: 'owner-1',
    bot_type: 'service',
    kind: 'service',
    engine: 'openclaw',
    display_state: 'service_draft',
  }).item;
  const personalBot = mapBotDto({
    bot_id: 'personal-1',
    owner_entity_id: 'owner-1',
    bot_type: 'personal',
    engine: 'openclaw',
    status: 'ACTIVE',
  }).item;

  const result = await botManagementService.loadServiceLocks([serviceBot, personalBot], 'u1');

  expect(getEditLock).toHaveBeenCalledTimes(1);
  expect(getEditLock).toHaveBeenCalledWith('service-1', 'owner-1');
  expect(result[0].lock).toEqual({
    status: 'other',
    holderUserId: 'u2',
    holderName: '李四',
    lockedAt: '2026-08-26T10:00:00Z',
  });
  expect(result[1].lock).toBeUndefined();
});
