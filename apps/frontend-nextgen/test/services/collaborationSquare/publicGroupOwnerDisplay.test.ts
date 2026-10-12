import * as bots from '@/services/backendApi/collaboration/collaborationBotController';
import type { GroupDetailData } from '@/services/backendApi/collaboration/collaborationGroupController';
import * as groups from '@/services/backendApi/collaboration/collaborationGroupController';
import { CollaborationSquareApiAdapter } from '@/services/collaborationSquare/collaborationSquareApiAdapter';

const group = (fields: Partial<GroupDetailData> = {}): GroupDetailData => ({
  group_id: 'group-1',
  version: 1,
  kind: 'normal',
  status: 'active',
  visibility: 'public',
  originator_actor_id: 'human_creator',
  participants: [],
  driver_bot_uuid: 'bot-1',
  collaboration: { strategy: 'chat', delivery_policy: { bot_final_delivery: 'send_to_driver' } },
  name: '公开群',
  created_at: 1,
  updated_at: 1,
  ...fields,
});

afterEach(() => jest.restoreAllMocks());

test.each(['新名称', '未公开'])('目录直接返回群主名 %s 时优先采用，不再反查 Bot', async (name) => {
  jest.spyOn(groups, 'listPublicGroups').mockResolvedValue({
    code: 20000,
    data: {
      items: [group({ driver_bot_name: ` ${name} ` })],
      total: 1,
    },
  });
  const lookup = jest.spyOn(bots, 'queryCollaborationBots').mockResolvedValue({
    code: 20000,
    data: {
      items: [{ bot_id: 'bot-1', kind: 'bot', name: '旧名称' }],
    },
  });
  const result = await new CollaborationSquareApiAdapter().listGroups();
  expect(result[0]).toMatchObject({ ownerBotName: name, driverBotUuid: 'bot-1' });
  expect(lookup).not.toHaveBeenCalled();
});

test('混合新旧响应只反查缺少名称的群，不覆盖接口已给出的名称', async () => {
  jest.spyOn(groups, 'listPublicGroups').mockResolvedValue({
    code: 20000,
    data: {
      items: [
        group({ driver_bot_name: '目录名称' }),
        group({ group_id: 'group-2', driver_bot_uuid: 'bot-2', driver_bot_name: '  ' }),
      ],
      total: 2,
    },
  });
  const lookup = jest.spyOn(bots, 'queryCollaborationBots').mockResolvedValue({
    code: 20000,
    data: {
      items: [{ bot_id: 'bot-2', kind: 'bot', name: '反查名称' }],
    },
  });
  const result = await new CollaborationSquareApiAdapter().listGroups();
  expect(result.map((item) => item.ownerBotName)).toEqual(['目录名称', '反查名称']);
  expect(lookup).toHaveBeenCalledWith({ bot_ids: ['bot-2'] });
});

test.each([
  [' Owner 名称 ', 'human_owner', 'Owner 名称'],
  ['', 'human_owner', 'human_owner'],
  ['   ', ' human_owner ', 'human_owner'],
  [undefined, undefined, '未公开'],
  [' ', ' ', '未公开'],
])('详情 Owner 名称 %p / ID %p 映射为 %p', async (name, id, expected) => {
  const getGroup = jest.spyOn(groups, 'getGroup').mockResolvedValue({
    code: 20000,
    data: group({
      driver_bot_owner_name: name,
      driver_bot_owner: id,
      participants: [
        {
          actor_id: 'human_creator',
          actor_kind: 'human',
          name: '不是 Bot Owner 的创建人',
          role: 'consultant',
          mode: 'auto',
        },
      ],
    }),
  });
  const result = await new CollaborationSquareApiAdapter().listGroupMembers('group-1');
  expect(result).toEqual({
    ownerUserName: expected,
    members: [{ id: 'human_creator', type: 'human', displayName: '不是 Bot Owner 的创建人', role: 'consultant' }],
  });
  expect(getGroup).toHaveBeenCalledTimes(1);
});

test('旧接口名称反查失败仍展示 UUID', async () => {
  jest.spyOn(groups, 'listPublicGroups').mockResolvedValue({ code: 20000, data: { items: [group()], total: 1 } });
  jest.spyOn(bots, 'queryCollaborationBots').mockRejectedValue(new Error('lookup unavailable'));
  const result = await new CollaborationSquareApiAdapter().listGroups();
  expect(result[0]?.ownerBotName).toBe('bot-1');
});
