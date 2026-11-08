import { listBots } from '@/services/backendApi/bots/botController';
import { listMyBots } from '@/services/backendApi/collaboration/collaborationBotController';
import { externalBotService } from '@/services/botWorkshop/externalBotService';

jest.mock('@/services/backendApi/bots/botController', () => ({ listBots: jest.fn() }));
jest.mock('@/services/backendApi/collaboration/collaborationBotController', () => ({ listMyBots: jest.fn() }));

const mine = listMyBots as jest.MockedFunction<typeof listMyBots>;
const owned = listBots as jest.MockedFunction<typeof listBots>;

beforeEach(() => jest.clearAllMocks());

test('合并 mine 分页后仅保留 bot_ 前缀的外部 Bot，不依赖 TC Bot 目录', async () => {
  mine
    .mockResolvedValueOnce({
      code: 20000,
      message: 'OK',
      data: {
        total: 3,
        items: [
          { kind: 'bot', bot_id: '20260828_mpjo5k61:85020', name: '测试 TC Bot' },
          {
            kind: 'bot',
            bot_id: 'bot_0e0ed32d',
            name: 'External One',
            descriptor: { summary: '外部描述', domains: [], scopes: [], skills: [] },
          },
        ],
      },
    })
    .mockResolvedValueOnce({
      code: 20000,
      message: 'OK',
      data: {
        total: 3,
        items: [{ kind: 'bot', bot_id: 'bot_258dfd15', name: 'External Two', status: 'online' }],
      },
    });
  owned.mockResolvedValue({
    code: 200000,
    message: 'OK',
    data: { total: 0, items: [] },
  });

  const result = await externalBotService.list({ pageSize: 2 });

  expect(result.map((item) => item.id)).toEqual(['bot_0e0ed32d', 'bot_258dfd15']);
  expect(result[0]).toMatchObject({ name: 'External One', description: '外部描述' });
  expect(mine).toHaveBeenNthCalledWith(1, { kind: 'bot', offset: 0, limit: 2 });
  expect(mine).toHaveBeenNthCalledWith(2, { kind: 'bot', offset: 2, limit: 2 });
  expect(owned).not.toHaveBeenCalled();
});
