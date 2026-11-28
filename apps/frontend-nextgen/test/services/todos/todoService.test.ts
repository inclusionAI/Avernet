import {
  listTodoBotInventory,
  listTodoSpaceMembers,
} from '@/services/backendApi/todos/todoController';
import {
  loadTodoBots,
  loadTodoMentionBots,
  loadTodoOwners,
} from '@/services/todos/todoService';

jest.mock('@/services/backendApi/todos/todoController', () => {
  const actual = jest.requireActual('@/services/backendApi/todos/todoController');
  return {
    ...actual,
    listTodoBotInventory: jest.fn(),
    listTodoSpaceMembers: jest.fn(),
  };
});

const listBots = jest.mocked(listTodoBotInventory);
const listMembers = jest.mocked(listTodoSpaceMembers);

describe('Todo Bot and owner candidates', () => {
  beforeEach(() => {
    listBots.mockReset();
    listMembers.mockReset();
  });

  it('保留当前空间 inventory 中的所有 Bot，不按 public 或 Open Core 可见性过滤', async () => {
    listBots.mockResolvedValue({
      code: 200000,
      data: {
        items: [
          {
            bot_id: 'bot-open',
            bot_name: '公开引擎 Bot',
            owner_id: 'u-1',
            engine: 'openclaw',
            visibility: 'private',
          },
          {
            bot_id: 'bot-moltis',
            bot_name: 'Moltis Bot',
            owner_id: 'u-2',
            engine: 'moltis',
            visibility: 'private',
          },
        ],
        total: 2,
        page: 1,
        page_size: 100,
      },
    });

    const bots = await loadTodoBots(1259);

    expect(bots.map((bot) => bot.id)).toEqual(['bot-open', 'bot-moltis']);
    expect(bots[1]?.runtime.visibleInOpenCore).toBe(false);
    expect(listBots).toHaveBeenCalledWith(1259);
  });

  it('按待办所属空间查询、去重并映射稳定 Bot ID', async () => {
    listBots.mockResolvedValue({
      code: 200000,
      data: {
        items: [
          { bot_id: 'bot-review', bot_name: '复核助手', engine: 'openclaw' },
          { bot_id: 'bot-review', bot_name: '重复记录', engine: 'openclaw' },
          { bot_id: 'bot-write', bot_name: '', engine: 'moltis' },
        ],
        total: 3,
        page: 1,
        page_size: 100,
      },
    });

    await expect(loadTodoMentionBots(1259)).resolves.toEqual([
      { botId: 'bot-review', botName: '复核助手' },
      { botId: 'bot-write', botName: '未命名 Bot' },
    ]);
    expect(listBots).toHaveBeenCalledWith(1259);
  });

  it('分页加载空间成员并映射负责人名称', async () => {
    listMembers
      .mockResolvedValueOnce({
        code: 200000,
        data: {
          items: Array.from({ length: 100 }, (_, index) => ({
            user_id: `u-${index + 1}`,
            display_name: `成员 ${index + 1}`,
          })),
          total: 101,
          page: 1,
          page_size: 100,
        },
      })
      .mockResolvedValueOnce({
        code: 200000,
        data: {
          items: [{ user_id: 'u-101', user_name: '最后成员' }],
          total: 101,
          page: 2,
          page_size: 100,
        },
      });

    const owners = await loadTodoOwners(1259);

    expect(owners).toHaveLength(101);
    expect(owners.at(-1)).toEqual({ userId: 'u-101', userName: '最后成员' });
    expect(listMembers).toHaveBeenNthCalledWith(1, 1259, 1);
    expect(listMembers).toHaveBeenNthCalledWith(2, 1259, 2);
  });
});
