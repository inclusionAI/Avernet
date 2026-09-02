import { CollaborationSquareError } from '@/services/collaborationSquare/collaborationSquareError';
import { MockCollaborationSquareAdapter } from '@/services/collaborationSquare/mockCollaborationSquareAdapter';
import { afterEach, describe, expect, it, jest } from '@jest/globals';

const originalFetch = global.fetch;

afterEach(() => {
  global.fetch = originalFetch;
  jest.restoreAllMocks();
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

// sample-only transport：覆盖四态 + 未知/缺失状态（应被过滤）。
const fixtureDtos = [
  {
    task_id: 'task-1',
    name: '梳理 Q3 路线图',
    goal: '输出路线图文档',
    acceptance_criteria: ['覆盖方向'],
    status: 'pending_claim',
    publisher_bot_name: '产品协作助手',
    published_at: '2026-08-19T09:00:00Z',
  },
  {
    task_id: 'task-2',
    name: '竞品研究',
    goal: '输出竞品矩阵',
    acceptance_criteria: ['对比'],
    status: 'claimed',
    publisher_bot_name: '研究分析助手',
    published_at: '2026-08-20T10:00:00Z',
    claimed_bot_name: '运维协作助手',
    claimed_at: '2026-08-21T08:00:00Z',
  },
  {
    task_id: 'task-3',
    name: '代码评审',
    goal: '梳理 CR 流程',
    acceptance_criteria: ['流程图'],
    status: 'reviewing',
    publisher_bot_name: '研发协作助手',
    published_at: '2026-08-18T14:00:00Z',
  },
  {
    task_id: 'task-4',
    name: '故障复盘',
    goal: '输出复盘报告',
    acceptance_criteria: ['根因'],
    status: 'completed',
    publisher_bot_name: '研发协作助手',
    published_at: '2026-08-17T11:00:00Z',
    completed_at: '2026-08-25T17:00:00Z',
  },
  {
    task_id: 'task-5',
    name: '未知状态任务',
    goal: '应被过滤',
    acceptance_criteria: ['x'],
    status: 'drafting',
    publisher_bot_name: '产品协作助手',
    published_at: '2026-08-16T09:00:00Z',
  },
  {
    task_id: 'task-6',
    name: '缺失状态任务',
    goal: '应被过滤',
    acceptance_criteria: ['y'],
    publisher_bot_name: '产品协作助手',
    published_at: '2026-08-15T09:00:00Z',
  },
];

describe('MockCollaborationSquareAdapter task methods', () => {
  it('listPublicTasks 走 tasks mock 路由，过滤未知/缺失状态，total 为过滤后数量', async () => {
    const fetchMock = jest.fn<typeof fetch>().mockResolvedValue(jsonResponse(fixtureDtos));
    global.fetch = fetchMock;

    const page = await new MockCollaborationSquareAdapter().listPublicTasks();

    expect(page.items.map((task) => task.id)).toEqual(['task-2', 'task-1', 'task-3', 'task-4']);
    expect(page.total).toBe(4);
    expect(fetchMock.mock.calls[0][0]).toBe('/api/mock/collaboration-square/tasks');
  });

  it('listPublicTasks 返回按 publishedAt 倒序（最新发布在前）', async () => {
    global.fetch = jest.fn<typeof fetch>().mockResolvedValue(jsonResponse(fixtureDtos));
    const page = await new MockCollaborationSquareAdapter().listPublicTasks();
    expect(page.items.map((t) => t.id)).toEqual(['task-2', 'task-1', 'task-3', 'task-4']);
  });

  it('search 命中 name 或 goal，大小写不敏感', async () => {
    global.fetch = jest.fn<typeof fetch>().mockResolvedValue(jsonResponse(fixtureDtos));
    const adapter = new MockCollaborationSquareAdapter();

    expect((await adapter.listPublicTasks({ search: '路线图' })).items.map((t) => t.id)).toEqual(['task-1']);
    expect((await adapter.listPublicTasks({ search: '矩阵' })).items.map((t) => t.id)).toEqual(['task-2']);
    // 大小写不敏感命中 name
    expect((await adapter.listPublicTasks({ search: 'q3' })).items.map((t) => t.id)).toEqual(['task-1']);
    // 命中 goal
    expect((await adapter.listPublicTasks({ search: 'cr' })).items.map((t) => t.id)).toEqual(['task-3']);
  });

  it('status 过滤：all 不限，具体状态仅返回该状态', async () => {
    global.fetch = jest.fn<typeof fetch>().mockResolvedValue(jsonResponse(fixtureDtos));
    const adapter = new MockCollaborationSquareAdapter();

    expect((await adapter.listPublicTasks({ status: 'claimed' })).items.map((t) => t.id)).toEqual(['task-2']);
    expect((await adapter.listPublicTasks({ status: 'reviewing' })).items.map((t) => t.id)).toEqual(['task-3']);
    expect((await adapter.listPublicTasks({ status: 'completed' })).items.map((t) => t.id)).toEqual(['task-4']);
    expect((await adapter.listPublicTasks({ status: 'all' })).items).toHaveLength(4);
  });

  it('offset/limit 分页，total 仍为过滤后总数', async () => {
    global.fetch = jest.fn<typeof fetch>().mockResolvedValue(jsonResponse(fixtureDtos));
    const adapter = new MockCollaborationSquareAdapter();

    const page = await adapter.listPublicTasks({ offset: 1, limit: 2 });
    expect(page.items.map((t) => t.id)).toEqual(['task-1', 'task-3']);
    expect(page.total).toBe(4);
  });

  it('search 与 status 组合过滤', async () => {
    global.fetch = jest.fn<typeof fetch>().mockResolvedValue(jsonResponse(fixtureDtos));
    const adapter = new MockCollaborationSquareAdapter();

    // '复盘' 命中 task-4 的 goal，叠加 completed 状态筛选
    const page = await adapter.listPublicTasks({ search: '复盘', status: 'completed' });
    expect(page.items.map((t) => t.id)).toEqual(['task-4']);
    expect(page.total).toBe(1);
  });

  it('getPublicTask 命中返回单条映射后的 PublicTask', async () => {
    global.fetch = jest.fn<typeof fetch>().mockResolvedValue(jsonResponse(fixtureDtos[0]));
    const adapter = new MockCollaborationSquareAdapter();

    const task = await adapter.getPublicTask('task-1');
    expect(task).toEqual(
      expect.objectContaining({
        id: 'task-1',
        name: '梳理 Q3 路线图',
        status: 'pending_claim',
        publisherBotName: '产品协作助手',
        acceptanceCriteria: ['覆盖方向'],
      }),
    );
  });

  it('getPublicTask 未命中（404）抛 target_invalid', async () => {
    global.fetch = jest.fn<typeof fetch>().mockResolvedValue(notFoundResponse());
    const adapter = new MockCollaborationSquareAdapter();

    await expect(adapter.getPublicTask('missing')).rejects.toMatchObject({ code: 'target_invalid' });
  });

  it('getPublicTask 返回未知状态时降级为 target_invalid（不伪成功）', async () => {
    global.fetch = jest.fn<typeof fetch>().mockResolvedValue(jsonResponse(fixtureDtos[4]));
    const adapter = new MockCollaborationSquareAdapter();

    await expect(adapter.getPublicTask('task-5')).rejects.toBeInstanceOf(CollaborationSquareError);
    await expect(adapter.getPublicTask('task-5')).rejects.toMatchObject({ code: 'target_invalid' });
  });
});
