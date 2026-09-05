import type { CollaborationSquareGateway } from '../src/services/collaborationSquare/collaborationSquareGateway';
import type { PublicBot } from '../src/domain/collaborationSquare/types';
import {
  CollaborationSquareError,
  CollaborationSquareService,
} from '../src/services/collaborationSquare/collaborationSquareService';
import { MockCollaborationSquareAdapter } from '../src/services/collaborationSquare/mockCollaborationSquareAdapter';

const humanContext = { actorId: 'human_327325', userId: '327325' };

const publicBot = (id: string, name = '项目助手'): PublicBot => ({
  id,
  name,
  ownerName: 'Owner',
  description: '',
  capabilities: [],
  relationshipStatus: 'none',
});

function createGateway(overrides: Partial<CollaborationSquareGateway> = {}): CollaborationSquareGateway {
  return {
    listBotPage: jest.fn(async () => ({ items: [], total: 0 })),
    listBots: jest.fn(async () => []),
    discoverBots: jest.fn(async () => []),
    getBotProfile: jest.fn(async () => ({
      id: 'b1',
      name: '助手',
      ownerName: 'Owner',
      description: '',
      capabilities: [],
    })),
    requestBotFriendship: jest.fn(async () => ({ status: 'applying' as const })),
    openBotConversation: jest.fn(async () => ({ sessionId: 'bot-session' })),
    listGroupPage: jest.fn(async () => ({ items: [], total: 0 })),
    listGroups: jest.fn(async () => []),
    listGroupMembers: jest.fn(async () => []),
    createGroupSession: jest.fn(async () => ({ sessionId: 's1' })),
    listPublicTasks: jest.fn(async () => ({ items: [], total: 0 })),
    getPublicTask: jest.fn(async () => ({
      id: 'task-1',
      name: '任务',
      goal: '',
      acceptanceCriteria: [],
      status: 'pending_claim' as const,
      publisherBotName: '发布者',
      publishedAt: '',
    })),
    ...overrides,
  };
}

describe('collaboration square service', () => {
  test('好友申请按服务端结果更新关系且范围拒绝不伪造 applying', async () => {
    const denied = new CollaborationSquareService(
      createGateway({
        requestBotFriendship: jest.fn(async () => {
          throw new CollaborationSquareError('scope_not_matched', '当前组织不在该 Bot 的好友申请范围内');
        }),
      }),
    );
    await expect(denied.requestBotFriendship('b1', humanContext)).rejects.toMatchObject({ code: 'scope_not_matched' });

    const accepted = new CollaborationSquareService(
      createGateway({
        requestBotFriendship: jest.fn(async () => ({ status: 'friend' as const })),
      }),
    );
    await expect(accepted.requestBotFriendship('b1', humanContext)).resolves.toEqual({ status: 'friend' });
  });

  test('同目标重复好友申请被锁定', async () => {
    let resolveRequest: ((value: { status: 'applying' }) => void) | undefined;
    const gateway = createGateway({
      requestBotFriendship: jest.fn(
        () =>
          new Promise((resolve) => {
            resolveRequest = resolve;
          }),
      ),
    });
    const service = new CollaborationSquareService(gateway);
    const first = service.requestBotFriendship('b1', humanContext);
    await expect(service.requestBotFriendship('b1', humanContext)).rejects.toMatchObject({ code: 'duplicate_action' });
    resolveRequest?.({ status: 'applying' });
    await expect(first).resolves.toEqual({ status: 'applying' });
  });

  test('重复原始 Bot ID 时按好友申请目标分别加锁', async () => {
    let resolveFirst: ((value: { status: 'applying' }) => void) | undefined;
    let resolveSecond: ((value: { status: 'applying' }) => void) | undefined;
    const gateway = createGateway({
      requestBotFriendship: jest
        .fn()
        .mockImplementationOnce(
          () =>
            new Promise((resolve) => {
              resolveFirst = resolve;
            }),
        )
        .mockImplementationOnce(
          () =>
            new Promise((resolve) => {
              resolveSecond = resolve;
            }),
        ),
    });
    const service = new CollaborationSquareService(gateway);
    const first = service.requestBotFriendship('default', humanContext, 'default:entity-a');
    const second = service.requestBotFriendship('default', humanContext, 'default:entity-b');

    resolveFirst?.({ status: 'applying' });
    resolveSecond?.({ status: 'applying' });
    await expect(first).resolves.toEqual({ status: 'applying' });
    await expect(second).resolves.toEqual({ status: 'applying' });
    expect(gateway.requestBotFriendship).toHaveBeenCalledTimes(2);
  });

  test('已是好友时只消费 Adapter 返回的唯一会话并锁定重复导航', async () => {
    let resolveConversation: ((value: { sessionId: string }) => void) | undefined;
    const gateway = createGateway({
      openBotConversation: jest.fn(
        () =>
          new Promise((resolve) => {
            resolveConversation = resolve;
          }),
      ),
    });
    const service = new CollaborationSquareService(gateway);
    const first = service.openBotConversation('b1', humanContext);
    await expect(service.openBotConversation('b1', humanContext)).rejects.toMatchObject({ code: 'duplicate_action' });
    resolveConversation?.({ sessionId: 'conversation-from-adapter' });
    await expect(first).resolves.toEqual({ sessionId: 'conversation-from-adapter' });
  });

  test('创建群会话透传 Human Bot 上下文、创建参数并返回 sessionId', async () => {
    const createGroupSession = jest.fn(async () => ({ sessionId: 'session-42' }));
    const service = new CollaborationSquareService(createGateway({ createGroupSession }));
    await expect(service.createGroupSession('g1', humanContext)).resolves.toEqual({ sessionId: 'session-42' });
    expect(createGroupSession).toHaveBeenCalledWith('g1', humanContext, undefined);

    const options = { title: '测试会话', query: '测试协作目标' };
    await expect(service.createGroupSession('g1', humanContext, options)).resolves.toEqual({ sessionId: 'session-42' });
    expect(createGroupSession).toHaveBeenLastCalledWith('g1', humanContext, options);
  });

  test('unsupported 能力显式失败', async () => {
    const service = new CollaborationSquareService(
      createGateway({
        listBots: jest.fn(async () => {
          throw new CollaborationSquareError('unsupported', '公开 Bot 服务暂不可用');
        }),
      }),
    );
    await expect(service.listBots()).rejects.toMatchObject({ code: 'unsupported' });
  });

  test('Bot 搜索查询与取消信号原样交给 Gateway', async () => {
    const listBots = jest.fn(async () => []);
    const service = new CollaborationSquareService(createGateway({ listBots }));
    const signal = new AbortController().signal;

    await service.listBots({ search: 'workflow', page: 2, pageSize: 10 }, humanContext, signal);

    expect(listBots).toHaveBeenCalledWith({ search: 'workflow', page: 2, pageSize: 10 }, humanContext, signal);
  });

  test('分页查询保留 Gateway 返回的 total', async () => {
    const listBotPage = jest.fn(async () => ({ items: [], total: 48 }));
    const listGroupPage = jest.fn(async () => ({ items: [], total: 36 }));
    const service = new CollaborationSquareService(createGateway({ listBotPage, listGroupPage }));

    await expect(service.listBotPage({ page: 1, pageSize: 24 }, humanContext)).resolves.toEqual({
      items: [],
      total: 48,
    });
    await expect(service.listGroupPage({ offset: 0, limit: 24 })).resolves.toEqual({ items: [], total: 36 });
  });

  test('分享 Bot 用公开名称检索，并以 canonical ID 精确匹配同名结果', async () => {
    const listBotPage = jest.fn(async () => ({
      items: [publicBot('same-name-other'), publicBot('bot:target')],
      total: 2,
    }));
    const service = new CollaborationSquareService(createGateway({ listBotPage }));
    const signal = new AbortController().signal;

    await expect(
      service.resolveSharedBot(
        'bot:target',
        '  项目助手  ',
        humanContext,
        { viewerActorType: 'human', viewerActorId: '327325' },
        signal,
      ),
    ).resolves.toEqual(expect.objectContaining({ id: 'bot:target' }));
    expect(listBotPage).toHaveBeenCalledWith(
      {
        search: '项目助手',
        page: 1,
        pageSize: 100,
        viewerActorType: 'human',
        viewerActorId: '327325',
      },
      humanContext,
      signal,
    );
  });

  test('分享 Bot 首页未命中时继续搜索后续页，最终仍只接受 ID 精确匹配', async () => {
    const listBotPage = jest
      .fn()
      .mockResolvedValueOnce({ items: [publicBot('same-name-other')], total: 101 })
      .mockResolvedValueOnce({ items: [publicBot('bot:target')], total: 101 });
    const service = new CollaborationSquareService(createGateway({ listBotPage }));

    await expect(service.resolveSharedBot('bot:target', '项目助手', humanContext)).resolves.toEqual(
      expect.objectContaining({ id: 'bot:target' }),
    );
    expect(listBotPage).toHaveBeenNthCalledWith(
      2,
      { search: '项目助手', page: 2, pageSize: 100 },
      humanContext,
      undefined,
    );
  });

  test('分享 Bot 名称搜索只有同名不同 ID 时返回未命中', async () => {
    const service = new CollaborationSquareService(
      createGateway({ listBotPage: jest.fn(async () => ({ items: [publicBot('other')], total: 1 })) }),
    );

    await expect(service.resolveSharedBot('target', '项目助手', humanContext)).resolves.toBeNull();
  });

  test('Bot Discovery 与公开群查询原样交给 Gateway', async () => {
    const discoverBots = jest.fn(async () => []);
    const listGroups = jest.fn(async () => []);
    const service = new CollaborationSquareService(createGateway({ discoverBots, listGroups }));
    const signal = new AbortController().signal;

    await service.discoverBots(
      { keyword: '会议纪要', topK: 20, minScore: 0.1, runtimeState: 'online' },
      humanContext,
      signal,
    );
    await service.listGroups({ search: '公开', offset: 0, limit: 20 }, signal);

    expect(discoverBots).toHaveBeenCalledWith(
      { keyword: '会议纪要', topK: 20, minScore: 0.1, runtimeState: 'online' },
      humanContext,
      signal,
    );
    expect(listGroups).toHaveBeenCalledWith({ search: '公开', offset: 0, limit: 20 }, signal);
  });

  test('任务广场查询与取消信号原样交给 Gateway', async () => {
    const listPublicTasks = jest.fn(async () => ({ items: [], total: 0 }));
    const service = new CollaborationSquareService(createGateway({ listPublicTasks }));
    const signal = new AbortController().signal;

    await service.listPublicTasks({ search: '路线图', status: 'pending_claim', offset: 0, limit: 10 }, signal);

    expect(listPublicTasks).toHaveBeenCalledWith(
      { search: '路线图', status: 'pending_claim', offset: 0, limit: 10 },
      signal,
    );
  });

  test('任务详情查询原样交给 Gateway', async () => {
    const getPublicTask = jest.fn(async () => ({
      id: 'task-1',
      name: '任务',
      goal: '',
      acceptanceCriteria: [],
      status: 'pending_claim' as const,
      publisherBotName: '发布者',
      publishedAt: '',
    }));
    const service = new CollaborationSquareService(createGateway({ getPublicTask }));
    const signal = new AbortController().signal;

    await service.getPublicTask('task-1', signal);

    expect(getPublicTask).toHaveBeenCalledWith('task-1', signal);
  });
});

describe('Mock Adapter 任务方法（留作 dev/测试兜底，不再被 wired service 使用）', () => {
  // 协作广场任务 Service 已切换至真实 ApiAdapter（见 collaborationSquareTaskService）；
  // Mock Adapter 保留作 dev/测试兜底，此处通过本地 Mock-backed service 验证其 task 方法仍可用。
  const mockService = new CollaborationSquareService(new MockCollaborationSquareAdapter());
  const originalFetch = global.fetch;

  afterEach(() => {
    global.fetch = originalFetch;
  });

  function jsonResponse(body: unknown): Response {
    return {
      ok: true,
      status: 200,
      headers: { get: () => 'application/json' },
      json: async () => body,
    } as unknown as Response;
  }

  function notFoundResponse(): Response {
    return {
      ok: false,
      status: 404,
      headers: { get: () => 'application/json' },
      json: async () => ({ code: 'target_invalid' }),
    } as unknown as Response;
  }

  const taskDtos = [
    {
      task_id: 'task-plaza-001',
      name: '梳理路线图',
      goal: '输出路线图',
      acceptance_criteria: ['覆盖'],
      status: 'pending_claim',
      publisher_bot_name: '产品协作助手',
      published_at: '2026-08-19T09:00:00Z',
    },
    {
      task_id: 'task-plaza-002',
      name: '竞品研究',
      goal: '输出矩阵',
      acceptance_criteria: ['对比'],
      status: 'claimed',
      publisher_bot_name: '研究分析助手',
      published_at: '2026-08-20T10:00:00Z',
    },
  ];

  it('listPublicTasks 经 Mock Adapter 返回过滤后页', async () => {
    global.fetch = () => Promise.resolve(jsonResponse(taskDtos));

    const page = await mockService.listPublicTasks({ search: '路线图' });

    expect(page.items.map((task) => task.id)).toEqual(['task-plaza-001']);
    expect(page.total).toBe(1);
  });

  it('getPublicTask 经 Mock Adapter 返回映射后的 PublicTask', async () => {
    global.fetch = () => Promise.resolve(jsonResponse(taskDtos[0]));

    const task = await mockService.getPublicTask('task-plaza-001');

    expect(task).toEqual(
      expect.objectContaining({ id: 'task-plaza-001', status: 'pending_claim', publisherBotName: '产品协作助手' }),
    );
  });

  it('getPublicTask 未命中抛 target_invalid', async () => {
    global.fetch = () => Promise.resolve(notFoundResponse());

    await expect(mockService.getPublicTask('missing')).rejects.toMatchObject({
      code: 'target_invalid',
    });
  });
});
