import { listCollaboratingBots, type CollaboratingBotDto } from '@/services/backendApi/bots/collaboratingBotController';
import { teamBotConversationService } from '@/services/workspace/teamBotConversationService';
jest.mock('@/services/backendApi/bots/collaboratingBotController', () => ({ listCollaboratingBots: jest.fn() }));
const list = jest.mocked(listCollaboratingBots);
const dto = (overrides: Partial<CollaboratingBotDto> = {}): CollaboratingBotDto => ({
  bot_id: 'shared',
  bot_name: '团队助手',
  bot_desc: '',
  entity_id: 'team-entity',
  owner_id: 'different-owner',
  engine: 'teclaw',
  cluster_name: 'ANDC',
  bot_type: 'personal',
  status: 'ACTIVE',
  collaboration: { id: 1, role: 'member', joined_at: '2026-09-01T10:30:00' },
  ...overrides,
});
beforeEach(() => list.mockReset());

test('entity_id 而不是 owner_id 作为会话 owner 上下文，mine 不标记好友', async () => {
  list.mockResolvedValue({ code: 200000, data: { items: [dto()], total: 1 } });
  const result = await teamBotConversationService.listBots('human_viewer');
  expect(list).toHaveBeenCalledWith({ user_id: 'viewer', page: 1, page_size: 100 }, undefined);
  expect(result).toMatchObject({
    ok: true,
    data: [
      {
        botId: 'shared:team-entity',
        realBotId: 'shared',
        ownerId: 'team-entity',
        displayName: '团队助手',
        engine: 'teclaw',
        chatable: true,
      },
    ],
  });
  expect(result.ok && result.data[0].isFriendBot).not.toBe(true);
});

test('按 total 分页且按完整 bot_id/entity_id 去重，保留后端顺序', async () => {
  list.mockResolvedValueOnce({ code: 200000, data: { items: [dto(), dto({ entity_id: 'other-team' })], total: 101 } });
  list.mockResolvedValueOnce({ code: 200000, data: { items: [dto(), dto({ bot_id: 'last' })], total: 101 } });
  const signal = new AbortController().signal;
  const result = await teamBotConversationService.listBots('viewer', signal);
  expect(list).toHaveBeenNthCalledWith(2, { user_id: 'viewer', page: 2, page_size: 100 }, signal);
  expect(result.ok && result.data.map((b) => b.botId)).toEqual([
    'shared:team-entity',
    'shared:other-team',
    'last:team-entity',
  ]);
});

test('缺 entity_id 不猜 owner 或 viewer，保留不可聊天行', async () => {
  list.mockResolvedValue({ code: 200000, data: { items: [dto({ entity_id: '' })], total: 1 } });
  const result = await teamBotConversationService.listBots('viewer');
  expect(result).toMatchObject({ ok: true, data: [{ chatable: false }] });
  expect(result.ok ? result.data[0].ownerId : null).toBeUndefined();
});

test.each([20000, 403000, undefined])('不把错误/其他方言 code %p 当成空列表', async (code) => {
  list.mockResolvedValue({ code, data: { items: [], total: 0 } });
  expect(await teamBotConversationService.listBots('viewer')).toMatchObject({ ok: false });
});
test('空列表成功、网络失败可重试、未登录不请求', async () => {
  list.mockResolvedValue({ code: 200000, data: { items: [], total: 0 } });
  expect(await teamBotConversationService.listBots('viewer')).toEqual({ ok: true, data: [] });
  list.mockRejectedValue(new Error('offline'));
  expect(await teamBotConversationService.listBots('viewer')).toMatchObject({ ok: false, error: { canRetry: true } });
  list.mockClear();
  expect(await teamBotConversationService.listBots('')).toMatchObject({ ok: false });
  expect(list).not.toHaveBeenCalled();
});
