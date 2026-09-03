import { createSpace, listSpaces } from '@/services/backendApi/admin/spaceController';
import { botCollaborationController } from '@/services/backendApi/bots/botCollaborationController';
import { botManagementService } from '@/services/botWorkshop/botManagementService';

jest.mock('@/services/backendApi/bots/botEditorController', () => ({
  botEditorController: { stealEditLock: jest.fn() },
}));
jest.mock('@/services/backendApi/admin/spaceController', () => ({ listSpaces: jest.fn(), createSpace: jest.fn() }));
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
  add.mockResolvedValue({ data: { id: 7, user_id: '149608', user_name: '小明', role: 'member' } });

  await expect(botManagementService.addCollaborator('bot-1', '149608', '小明', 'member')).resolves.toEqual({
    id: 7,
    userId: '149608',
    name: '小明',
    role: 'member',
  });
  expect(add).toHaveBeenCalledWith('bot-1', '149608', '小明', 'member');
});
