import { botWorkshopService } from '@/services/botWorkshop/botWorkshopService';
import { loadTodoMentionBots } from '@/services/todos/todoService';

jest.mock('@/services/botWorkshop/botWorkshopService', () => ({
  botWorkshopService: { list: jest.fn() },
}));

const listBots = jest.mocked(botWorkshopService.list);

describe('Todo mention Bot candidates', () => {
  beforeEach(() => {
    listBots.mockReset();
  });

  it('按待办所属空间查询并映射稳定 Bot ID', async () => {
    listBots.mockResolvedValue({
      items: [
        { id: 'bot-review', name: '复核助手' },
        { id: 'bot-review', name: '重复记录' },
        { id: 'bot-write', name: '' },
      ],
    } as Awaited<ReturnType<typeof botWorkshopService.list>>);

    await expect(loadTodoMentionBots(1259)).resolves.toEqual([
      { botId: 'bot-review', botName: '复核助手' },
      { botId: 'bot-write', botName: 'bot-write' },
    ]);
    expect(listBots).toHaveBeenCalledWith({
      spaceId: '1259',
      page: 1,
      pageSize: 100,
    });
  });
});
