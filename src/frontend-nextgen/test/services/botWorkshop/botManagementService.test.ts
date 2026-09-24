import { createSpace, listSpaceMembers, listSpaces } from '@/services/backendApi/admin/spaceController';
import { botCollaborationController } from '@/services/backendApi/bots/botCollaborationController';
import { botManagementService } from '@/services/botWorkshop/botManagementService';
import { mapBotDto } from '@/services/botWorkshop/botMapper';

jest.mock('@/services/backendApi/bots/botEditorController', () => ({
  botEditorController: { stealEditLock: jest.fn() },
}));
jest.mock('@/services/backendApi/admin/spaceController', () => ({
  listSpaces: jest.fn(),
  createSpace: jest.fn(),
  listSpaceMembers: jest.fn(),
}));
jest.mock('@/services/backendApi/bots/botCollaborationController', () => ({
  botCollaborationController: { add: jest.fn(), update: jest.fn() },
}));
jest.mock('@/services/backendApi/bots/botController', () => ({ changeBotSpace: jest.fn() }));

const mockedListSpaces = listSpaces as jest.MockedFunction<typeof listSpaces>;
const mockedCreateSpace = createSpace as jest.MockedFunction<typeof createSpace>;

test('变更归属空间只查询当前用户可用空间', async () => {
  mockedListSpaces.mockResolvedValue({ data: { total: 0, items: [] } });

  await botManagementService.listSpaces('149608');

  expect(mockedListSpaces).toHaveBeenCalledWith({
    user_id: '149608',
    page_no: 1,
    page_size: 100,
    scope: 'accessible',
  });
});

test('创建团队后返回可用于迁移的空间', async () => {
  mockedCreateSpace.mockResolvedValue({ data: { space_id: 12, space_name: '研发团队', space_type: 'TEAM' } });

  await expect(botManagementService.createTeamSpace('研发团队', '149608')).resolves.toEqual({
    id: 12,
    name: '研发团队',
    type: 'TEAM',
  });
  expect(mockedCreateSpace).toHaveBeenCalledWith({ space_name: '研发团队' }, { user_id: '149608' });
});

test('添加协作者时同时提交姓名并使用写接口响应', async () => {
  const add = botCollaborationController.add as jest.Mock;
  add.mockResolvedValue({ code: 201000, data: { id: 7, user_id: '149608', user_name: '小明', role: 'member' } });

  await expect(botManagementService.addCollaborator('bot-1', '149608', '小明', 'member')).resolves.toEqual({
    id: 7,
    userId: '149608',
    name: '小明',
    role: 'member',
  });
  expect(add).toHaveBeenCalledWith('bot-1', '149608', '小明', 'member');
});

test('授权候选仅查询 Bot 所属空间成员并保留姓名', async () => {
  const members = listSpaceMembers as jest.Mock;
  members.mockResolvedValue({
    code: 200000,
    data: { total: 1, items: [{ user_id: '1002', display_name: '小华', role: 'MEMBER' }] },
  });
  await expect(botManagementService.listSpaceMembers('145', '149608')).resolves.toEqual([
    { userId: '1002', name: '小华' },
  ]);
  expect(members).toHaveBeenCalledWith('145', { user_id: '149608', page_no: 1, page_size: 100 });
});

test('团队 Bot Owner 名称从空间成员补齐，缺失时由 UI 回退工号', async () => {
  const members = listSpaceMembers as jest.Mock;
  members.mockResolvedValue({ code: 200000, data: { total: 1, items: [{ user_id: '1002', display_name: '小华' }] } });
  const bot = mapBotDto({
    bot_id: 'team-1',
    bot_name: '团队 Bot',
    engine: 'openclaw',
    owner_entity_id: '1002',
    space: { space_id: '146', kind: 'team' },
  }).item;
  const items = await botManagementService.fillOwnerNames([bot], '149608');
  expect(items[0].ownerName).toBe('小华');
});
