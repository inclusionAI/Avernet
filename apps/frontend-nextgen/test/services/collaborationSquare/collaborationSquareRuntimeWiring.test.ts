import {
  collaborationSquareBotService,
  collaborationSquareGroupService,
} from '@/services/collaborationSquare/collaborationSquareService';
import { afterEach, describe, expect, it, jest } from '@jest/globals';

const originalFetch = global.fetch;

afterEach(() => {
  global.fetch = originalFetch;
});

function jsonResponse(body: unknown) {
  return {
    ok: true,
    status: 200,
    headers: { get: () => 'application/json' },
    json: async () => body,
  } as unknown as Response;
}

/** 按路径前缀路由的 fetch stub：BCS 候选/搜索/Friend-connection 与 metadata 请求统一在此供给。 */
function routeFetch(handlers: Array<[prefix: string, body: unknown]>) {
  return jest.fn<typeof fetch>((input: RequestInfo | URL) => {
    const url = String(input);
    const handler = handlers.find(([prefix]) => url.startsWith(prefix));
    return Promise.resolve(jsonResponse(handler ? handler[1] : {}));
  });
}

const humanContext = { actorId: 'human_900003', userId: '900003' };

/** BCS 候选（PhysicalBot 候选投影 + is_friend），对齐 bots.yaml list/search DTO。 */
const BCS_CANDIDATE_ITEM = {
  bot: {
    bot_id: 'planner:owner-8',
    name: '真实候选',
    avatar_url: null,
    created_by: '8',
    descriptor: { summary: '擅长任务规划' },
    visibility: 'public',
    user_visibility: 'public',
    status: 'online',
  },
  is_friend: false,
};

const BCS_CANDIDATES_ENVELOPE = {
  code: 20000,
  message: 'ok',
  data: { items: [BCS_CANDIDATE_ITEM], total: 1, offset: 0, limit: 100 },
};

const EMPTY_FRIEND_CONNECTIONS_ENVELOPE = { code: 20000, data: { items: [], total: 0 } };

/** 好友关系回填与 Frontend metadata 增强的真实链路完成后的公共佐断言。 */
function expectNoCatalogOrMockFallback(fetchMock: jest.Mock) {
  expect(
    fetchMock.mock.calls.some(([url]: [unknown, ...unknown[]]) => String(url).includes('/openapi/v1/bots/catalog')),
  ).toBe(false);
  expect(
    fetchMock.mock.calls.some(([url]: [unknown, ...unknown[]]) =>
      String(url).includes('/api/mock/collaboration-square/bots'),
    ),
  ).toBe(false);
}

describe('collaboration square BOT runtime wiring（BCS v1）', () => {
  it('普通列表经 BCS candidates 端点装配，不回退 Backend catalog 与 BOT Mock 路由', async () => {
    const fetchMock = routeFetch([
      ['/openapi/v1/collaboration/bots/human_900003/candidates', BCS_CANDIDATES_ENVELOPE],
      ['/openapi/v1/collaboration/friend-connections', EMPTY_FRIEND_CONNECTIONS_ENVELOPE],
      ['/openapi/v1/bots/metadata/queries', { code: 200000, data: { items: [], total: 0, offset: 0, limit: 100 } }],
    ]);
    global.fetch = fetchMock;

    await expect(
      collaborationSquareBotService.listBots({ search: '规划', offset: 0, pageSize: 20 }, humanContext),
    ).resolves.toEqual([expect.objectContaining({ id: 'planner:owner-8', name: '真实候选', bcsStatus: 'online' })]);

    // 首个请求即 BCS 公开候选：purpose=discovery + 名称过滤 + 整批 ≤100 的原始 offset 游标。
    expect(fetchMock.mock.calls[0][0]).toBe(
      '/openapi/v1/collaboration/bots/human_900003/candidates?purpose=discovery&name=%E8%A7%84%E5%88%92&offset=0&limit=100',
    );
    expectNoCatalogOrMockFallback(fetchMock);
  });

  it('surfaces an ACE unauthenticated response and never falls back to BOT Mock data', async () => {
    const fetchMock = jest.fn<typeof fetch>().mockResolvedValue(
      jsonResponse({
        actionType: 'LOGIN',
        buserviceErrorCode: 'USER_NOT_LOGIN',
        decisionBy: 'ACE',
      }),
    );
    global.fetch = fetchMock;

    await expect(collaborationSquareBotService.listBots(undefined, humanContext)).rejects.toMatchObject({
      code: 'unauthenticated',
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(
      fetchMock.mock.calls.some(([url]: [unknown, ...unknown[]]) =>
        String(url).includes('/api/mock/collaboration-square/bots'),
      ),
    ).toBe(false);
  });

  it('智能搜索经 BCS 内部 candidates/search 端点，语义附加字段保留且不下发 runtime_state', async () => {
    const fetchMock = routeFetch([
      [
        '/api/v1/collaboration/bots/bot-1%3Aowner-1/candidates/search',
        {
          code: 20000,
          message: 'ok',
          data: {
            items: [{ ...BCS_CANDIDATE_ITEM, score: 0.87, short_profile: '擅长任务规划' }],
            search_mode: 'semantic',
          },
        },
      ],
      ['/openapi/v1/collaboration/friend-connections', EMPTY_FRIEND_CONNECTIONS_ENVELOPE],
      ['/openapi/v1/bots/metadata/queries', { code: 200000, data: { items: [], total: 0, offset: 0, limit: 100 } }],
    ]);
    global.fetch = fetchMock;

    await expect(
      collaborationSquareBotService.discoverBots(
        { keyword: '整理会议纪要', viewerActorType: 'bot', viewerActorId: 'bot-1:owner-1' },
        humanContext,
      ),
    ).resolves.toEqual({
      items: [expect.objectContaining({ id: 'planner:owner-8', score: 0.87, shortProfile: '擅长任务规划' })],
      searchMode: 'semantic',
    });

    expect(fetchMock.mock.calls[0][0]).toBe(
      '/api/v1/collaboration/bots/bot-1%3Aowner-1/candidates/search?q=%E6%95%B4%E7%90%86%E4%BC%9A%E8%AE%AE%E7%BA%AA%E8%A6%81&purpose=discovery',
    );
    expectNoCatalogOrMockFallback(fetchMock);
  });

  it('BCS 错误信封（非 20000）按传输错误上抛，不回退 Search、catalog 或 BOT Mock 路由', async () => {
    const fetchMock = jest.fn<typeof fetch>().mockResolvedValue(
      jsonResponse({
        code: 40000,
        message: 'bad request',
      }),
    );
    global.fetch = fetchMock;

    await expect(collaborationSquareBotService.discoverBots({ keyword: '规划' }, humanContext)).rejects.toMatchObject({
      code: 'network',
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expectNoCatalogOrMockFallback(fetchMock);
  });

  it('uses the real Human to Bot friend request endpoint without requesting BOT Mock routes', async () => {
    const fetchMock = jest.fn<typeof fetch>().mockResolvedValue(
      jsonResponse({
        code: 20100,
        data: { request_ids: ['request-1'], edge_ids: [], status: 'pending', auto_accepted: false },
      }),
    );
    global.fetch = fetchMock;

    await expect(
      collaborationSquareBotService.requestBotFriendship('bot-real-1', {
        actorId: 'human_900003',
        userId: '900003',
      }),
    ).resolves.toEqual({ status: 'applying' });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe('/openapi/v1/collaboration/friend-connections/requests');
    expect(fetchMock.mock.calls[0][1]).toEqual(
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          to_actor: { type: 'bot', id: 'bot-real-1' },
          from_actor: { type: 'human', id: '900003' },
        }),
      }),
    );
    expect(
      fetchMock.mock.calls.some(([url]: [unknown, ...unknown[]]) =>
        String(url).includes('/api/mock/collaboration-square'),
      ),
    ).toBe(false);
  });

  it('uses the real private Bot session endpoint and only consumes its session_id', async () => {
    const fetchMock = jest.fn<typeof fetch>().mockResolvedValue(
      jsonResponse({
        code: 200000,
        data: {
          session_id: 'session-real-1',
          title: '新会话',
          agent_id: 'agent-1',
          model: 'model-1',
          message_count: 0,
          gmt_create: '2026-08-22T00:00:00Z',
          gmt_modified: '2026-08-22T00:00:00Z',
        },
      }),
    );
    global.fetch = fetchMock;

    await expect(
      collaborationSquareBotService.openBotConversation('bot-real-1:2088', {
        actorId: 'human_900003',
        userId: '900003',
      }),
    ).resolves.toEqual({ sessionId: 'session-real-1' });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe(
      '/openapi/v1/bots/bot-real-1/sessions?user_id=900003&owner_id=2088&f_user_id=900003',
    );
    expect(fetchMock.mock.calls[0][1]).toEqual(expect.objectContaining({ method: 'POST', body: '{}' }));
    expect(
      fetchMock.mock.calls.some(([url]: [unknown, ...unknown[]]) =>
        String(url).includes('/api/mock/collaboration-square'),
      ),
    ).toBe(false);
  });
});

describe('collaboration square public group runtime wiring', () => {
  it('uses the real public normal group endpoint without requesting the Group Mock route', async () => {
    const fetchMock = jest.fn<typeof fetch>().mockResolvedValue(
      jsonResponse({
        code: 20000,
        data: {
          items: [
            {
              group_id: 'group-real-1',
              version: 1,
              kind: 'normal',
              status: 'active',
              visibility: 'public',
              originator_actor_id: 'human-1',
              participants: [
                { actor_id: 'human-1', actor_kind: 'human', name: 'Owner', role: 'consultant', mode: 'present' },
                { actor_id: 'bot-1', actor_kind: 'bot', name: '主理 Bot', role: 'driver', mode: 'auto' },
              ],
              driver_bot_uuid: 'bot-1',
              collaboration: { strategy: 'chat', delivery_policy: { bot_final_delivery: 'send_to_driver' } },
              name: '公开协作群',
              participant_count: 2,
              created_at: 1,
              updated_at: 2,
            },
          ],
          total: 1,
        },
      }),
    );
    global.fetch = fetchMock;

    await expect(collaborationSquareGroupService.listGroups({ search: '公开', offset: 0, limit: 20 })).resolves.toEqual(
      [expect.objectContaining({ id: 'group-real-1', name: '公开协作群' })],
    );

    // listGroups 先取公开群，再经 bots/query 反查群主名（resolveBotNames），故此处共 2 次真实请求。
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[0][0]).toBe(
      '/openapi/v1/collaboration/public-groups?q=%E5%85%AC%E5%BC%80&offset=0&limit=20',
    );
    expect(
      fetchMock.mock.calls.some(([url]: [unknown, ...unknown[]]) =>
        String(url).includes('/openapi/v1/collaboration/bots/query'),
      ),
    ).toBe(true);
    expect(
      fetchMock.mock.calls.some(([url]: [unknown, ...unknown[]]) =>
        String(url).includes('/api/mock/collaboration-square/groups'),
      ),
    ).toBe(false);
  });

  it('surfaces a Group ACE response and never falls back to Group Mock data', async () => {
    const fetchMock = jest.fn<typeof fetch>().mockResolvedValue(
      jsonResponse({
        actionType: 'LOGIN',
        buserviceErrorCode: 'USER_NOT_LOGIN',
        decisionBy: 'ACE',
      }),
    );
    global.fetch = fetchMock;

    await expect(collaborationSquareGroupService.listGroups()).rejects.toMatchObject({
      code: 'unauthenticated',
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(
      fetchMock.mock.calls.some(([url]: [unknown, ...unknown[]]) =>
        String(url).includes('/api/mock/collaboration-square/groups'),
      ),
    ).toBe(false);
  });
});
