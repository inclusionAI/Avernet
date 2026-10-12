import { BotConfigApiGateway } from '@/services/lab/botConfigApiGateway';

jest.mock('@/services/backendApi/lab/botConfigController', () => ({
  listBrowseSubscriptions: jest.fn(),
  upsertBrowseSubscription: jest.fn(),
  deleteBrowseSubscription: jest.fn(),
}));

const { listBrowseSubscriptions, upsertBrowseSubscription, deleteBrowseSubscription } = jest.requireMock(
  '@/services/backendApi/lab/botConfigController',
) as Record<string, jest.Mock>;

describe('BotConfigApiGateway', () => {
  beforeEach(() => jest.clearAllMocks());

  it('listSubscriptions 解码成功信封并映射为领域视图', async () => {
    listBrowseSubscriptions.mockResolvedValue({
      code: 200000,
      message: 'success',
      data: {
        items: [
          { bot_id: 'b1', owner_user_id: 'u1', mode: 'framework', note: 'x' },
          { bot_id: 'b2', owner_user_id: 'u1', note: null },
        ],
        total: 2,
      },
    });
    const result = await new BotConfigApiGateway().listSubscriptions('u1');
    expect(result).toEqual([
      { botId: 'b1', ownerUserId: 'u1', mode: 'framework', note: 'x' },
      { botId: 'b2', ownerUserId: 'u1', mode: undefined, note: null },
    ]);
  });

  it('listSubscriptions 过滤空 bot_id 兜底', async () => {
    listBrowseSubscriptions.mockResolvedValue({
      code: 200000,
      data: { items: [{ bot_id: '', owner_user_id: 'u1' }], total: 1 },
    });
    await expect(new BotConfigApiGateway().listSubscriptions('u1')).resolves.toEqual([]);
  });

  it('listSubscriptions 收到失败信封抛错', async () => {
    listBrowseSubscriptions.mockResolvedValue({ code: 500000, message: 'boom', data: null });
    await expect(new BotConfigApiGateway().listSubscriptions('u1')).rejects.toThrow('boom');
  });

  it('upsertSubscription 解码并映射返回', async () => {
    upsertBrowseSubscription.mockResolvedValue({
      code: 201000,
      data: { bot_id: 'b1', owner_user_id: 'u1', mode: 'framework', note: 'n' },
    });
    const result = await new BotConfigApiGateway().upsertSubscription({
      botId: 'b1',
      ownerUserId: 'u1',
      note: 'n',
    });
    expect(result).toEqual({ botId: 'b1', ownerUserId: 'u1', mode: 'framework', note: 'n' });
  });

  it('deleteSubscription 成功信封不抛错', async () => {
    deleteBrowseSubscription.mockResolvedValue({ code: 200000, data: { deleted: true } });
    await expect(new BotConfigApiGateway().deleteSubscription('b1')).resolves.toBeUndefined();
  });

  it('deleteSubscription 失败信封抛错（用 message）', async () => {
    deleteBrowseSubscription.mockResolvedValue({ code: 500000, message: '取消失败', data: null });
    await expect(new BotConfigApiGateway().deleteSubscription('b1')).rejects.toThrow('取消失败');
  });
});
