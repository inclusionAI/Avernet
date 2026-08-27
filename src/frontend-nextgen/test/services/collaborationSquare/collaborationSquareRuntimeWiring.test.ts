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

describe('collaboration square BOT runtime wiring', () => {
  it('uses the real Bot Catalog Search endpoint without requesting the BOT Mock route', async () => {
    const fetchMock = jest.fn<typeof fetch>().mockResolvedValue(
      jsonResponse({
        code: 200000,
        data: {
          items: [
            {
              bot_id: 'bot-real-1',
              bot_type: 'assistant',
              description: '真实目录数据',
              engine: 'OpenClaw',
              entity_id: 'entity-real-1',
              name: '真实 Bot',
              owner_name: 'Owner',
              status: 'online',
            },
          ],
          total: 1,
        },
      }),
    );
    global.fetch = fetchMock;

    await expect(collaborationSquareBotService.listBots({ search: '真实', page: 1, pageSize: 20 })).resolves.toEqual([
      expect.objectContaining({ id: 'bot-real-1', name: '真实 Bot' }),
    ]);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe(
      '/openapi/v1/bots/catalog/search?search=%E7%9C%9F%E5%AE%9E&page=1&page_size=20',
    );
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes('/api/mock/collaboration-square/bots'))).toBe(
      false,
    );
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

    await expect(collaborationSquareBotService.listBots()).rejects.toMatchObject({
      code: 'unauthenticated',
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes('/api/mock/collaboration-square/bots'))).toBe(
      false,
    );
  });

  it('uses the real Bot Catalog Discovery endpoint without requesting Search or BOT Mock routes', async () => {
    const fetchMock = jest.fn<typeof fetch>().mockResolvedValue(
      jsonResponse({
        code: 200000,
        data: {
          items: [
            {
              bot_id: 'bot-smart-1',
              bot_type: 'assistant',
              description: '擅长会议纪要',
              engine: 'OpenClaw',
              entity_id: 'entity-smart-1',
              name: '会议助手',
              owner_name: 'Owner',
              status: 'online',
              recommendation: { score: 0.98, reason: 'semantic-match' },
            },
          ],
          total: 1,
        },
      }),
    );
    global.fetch = fetchMock;

    await expect(collaborationSquareBotService.discoverBots({ keyword: '整理会议纪要' })).resolves.toEqual([
      expect.objectContaining({ id: 'bot-smart-1', name: '会议助手' }),
    ]);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe(
      '/openapi/v1/bots/catalog/discover?keyword=%E6%95%B4%E7%90%86%E4%BC%9A%E8%AE%AE%E7%BA%AA%E8%A6%81&top_k=20&min_score=0.1&runtime_state=online',
    );
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes('/openapi/v1/bots/catalog/search'))).toBe(false);
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes('/api/mock/collaboration-square/bots'))).toBe(
      false,
    );
  });

  it('surfaces a Discovery protocol error and never falls back to Search or BOT Mock data', async () => {
    const fetchMock = jest.fn<typeof fetch>().mockResolvedValue(
      jsonResponse({
        code: 20000,
        data: { items: [], total: 0 },
      }),
    );
    global.fetch = fetchMock;

    await expect(collaborationSquareBotService.discoverBots({ keyword: '规划' })).rejects.toMatchObject({
      code: 'protocol_error',
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes('/openapi/v1/bots/catalog/search'))).toBe(false);
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes('/api/mock/collaboration-square/bots'))).toBe(
      false,
    );
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
        actorId: 'human_327325',
        userId: '327325',
      }),
    ).resolves.toEqual({ status: 'applying' });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe('/openapi/v1/collaboration/friend-connections/requests');
    expect(fetchMock.mock.calls[0][1]).toEqual(
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ to_actor: { type: 'bot', id: 'bot-real-1' } }),
      }),
    );
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes('/api/mock/collaboration-square'))).toBe(false);
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
        actorId: 'human_327325',
        userId: '327325',
      }),
    ).resolves.toEqual({ sessionId: 'session-real-1' });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe('/openapi/v1/bots/bot-real-1/sessions?user_id=327325&owner_id=2088');
    expect(fetchMock.mock.calls[0][1]).toEqual(expect.objectContaining({ method: 'POST', body: '{}' }));
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes('/api/mock/collaboration-square'))).toBe(false);
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

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe(
      '/openapi/v1/collaboration/groups?visibility=public&kind=normal&q=%E5%85%AC%E5%BC%80&offset=0&limit=20',
    );
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes('/api/mock/collaboration-square/groups'))).toBe(
      false,
    );
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
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes('/api/mock/collaboration-square/groups'))).toBe(
      false,
    );
  });
});
