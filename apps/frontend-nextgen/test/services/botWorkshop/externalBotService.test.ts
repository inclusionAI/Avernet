import { listBots } from '@/services/backendApi/bots/botController';
import { listMyBots } from '@/services/backendApi/collaboration/collaborationBotController';
import { externalBotService } from '@/services/botWorkshop/externalBotService';

jest.mock('@/services/backendApi/bots/botController', () => ({ listBots: jest.fn() }));
jest.mock('@/services/backendApi/collaboration/collaborationBotController', () => ({ listMyBots: jest.fn() }));

const mine = listMyBots as jest.MockedFunction<typeof listMyBots>;
const owned = listBots as jest.MockedFunction<typeof listBots>;

beforeEach(() => jest.clearAllMocks());

test('按需合并真实分页，并从 BCN mine 中排除平台内 TC Bot', async () => {
  mine
    .mockResolvedValueOnce({
      code: 20000,
      message: 'OK',
      data: {
        total: 3,
        items: [
          { kind: 'bot', bot_id: 'tc-1:1001', name: 'TC Bot' },
          {
            kind: 'bot',
            bot_id: 'external-1',
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
        items: [{ kind: 'bot', bot_id: 'external-2', name: 'External Two', status: 'online' }],
      },
    });
  owned.mockResolvedValue({
    code: 200000,
    message: 'OK',
    data: { total: 1, items: [{ bot_id: 'tc-1', bot_name: 'TC Bot' }] },
  });

  const result = await externalBotService.list({ pageSize: 2 });

  expect(result.map((item) => item.id)).toEqual(['external-1', 'external-2']);
  expect(result[0]).toMatchObject({ name: 'External One', description: '外部描述' });
  expect(mine).toHaveBeenNthCalledWith(1, { kind: 'bot', offset: 0, limit: 2 });
  expect(mine).toHaveBeenNthCalledWith(2, { kind: 'bot', offset: 2, limit: 2 });
});
