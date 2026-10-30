import * as bots from '@/services/backendApi/bots/botController';
import { resolveBackendBotNames } from '@/services/community/botDisplayNames';

jest.mock('@/services/backendApi/bots/botController', () => ({
  getBot: jest.fn(),
  listBotMetadata: jest.fn(),
}));

const getBot = bots.getBot as jest.MockedFunction<typeof bots.getBot>;
const listBotMetadata = bots.listBotMetadata as jest.MockedFunction<typeof bots.listBotMetadata>;

beforeEach(() => {
  getBot.mockReset();
  listBotMetadata.mockReset();
});

it('resolves composite BBS bot ids through the backend bot metadata query', async () => {
  listBotMetadata.mockResolvedValueOnce({
    code: 200000,
    data: {
      total: 1,
      items: [
        { bot_id: 'bbs-bot', owner_id: '149844', bot_name: 'BBS 助手' },
      ],
    },
  });

  await expect(resolveBackendBotNames(['bbs-bot:149844'])).resolves.toEqual({
    'bbs-bot:149844': 'BBS 助手',
  });
  expect(listBotMetadata).toHaveBeenCalledWith(
    { page: 1, page_size: 1 },
    { bots: [{ bot_id: 'bbs-bot', owner_id: '149844' }] },
  );
  expect(getBot).not.toHaveBeenCalled();
});

it('resolves bare BBS bot ids through the backend bot detail endpoint', async () => {
  getBot.mockResolvedValueOnce({
    code: 200000,
    data: { bot_id: 'bbs-bot', bot_name: '内容创作 Bot' },
  });

  await expect(resolveBackendBotNames(['bbs-bot'])).resolves.toEqual({
    'bbs-bot': '内容创作 Bot',
  });
  expect(getBot).toHaveBeenCalledWith('bbs-bot', undefined);
  expect(listBotMetadata).not.toHaveBeenCalled();
});

it('falls back to bot detail when a composite metadata query misses', async () => {
  listBotMetadata.mockResolvedValueOnce({
    code: 200000,
    data: { total: 0, items: [] },
  });
  getBot.mockResolvedValueOnce({
    code: 200000,
    data: { bot_id: 'bbs-bot', bot_name: '详情兜底 Bot' },
  });

  await expect(resolveBackendBotNames(['bbs-bot:149844'])).resolves.toEqual({
    'bbs-bot:149844': '详情兜底 Bot',
  });
  expect(getBot).toHaveBeenCalledWith('bbs-bot', '149844');
});
