import * as botSessionController from '@/services/backendApi/bots/privateBotSessionController';
import * as friendConnectionController from '@/services/backendApi/collaboration/collaborationFriendConnectionController';
import * as groupController from '@/services/backendApi/collaboration/collaborationGroupController';
import * as publicBotController from '@/services/backendApi/collaboration/publicBotController';
import { PublicBotCatalogError } from '@/services/backendApi/collaboration/publicBotController';
import { BackendRequestError } from '@/services/backendApi/httpClient';
import { CollaborationSquareApiAdapter } from '@/services/collaborationSquare/collaborationSquareApiAdapter';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';

const mockedSearchPublicBots = jest.spyOn(publicBotController, 'searchPublicBots');
const mockedDiscoverPublicBots = jest.spyOn(publicBotController, 'discoverPublicBots');
const mockedListPublicGroups = jest.spyOn(groupController, 'listPublicGroups');
const mockedListFriendConnections = jest.spyOn(friendConnectionController, 'listFriendConnections');
const mockedListFriendConnectionRequests = jest.spyOn(friendConnectionController, 'listFriendConnectionRequests');
const mockedCreateFriendConnectionRequest = jest.spyOn(friendConnectionController, 'createFriendConnectionRequest');
const mockedCreateBotSession = jest.spyOn(botSessionController, 'createBotSession');
const humanContext = { actorId: 'human_327325', userId: '327325' };

beforeEach(() => {
  mockedSearchPublicBots.mockReset();
  mockedDiscoverPublicBots.mockReset();
  mockedListPublicGroups.mockReset();
  mockedListFriendConnections.mockReset();
  mockedListFriendConnectionRequests.mockReset();
  mockedCreateFriendConnectionRequest.mockReset();
  mockedCreateBotSession.mockReset();
});

describe('CollaborationSquareApiAdapter', () => {
  it('maps an empty successful public Bot page to an empty domain list', async () => {
    mockedSearchPublicBots.mockResolvedValue({ code: 200000, data: { items: [], total: 0 } });

    await expect(new CollaborationSquareApiAdapter().listBots()).resolves.toEqual([]);
    expect(mockedSearchPublicBots).toHaveBeenCalledWith({ page: 1, page_size: 20 }, undefined);
  });

  it('maps the confirmed Catalog fields without exposing transport fields', async () => {
    const transportBot = {
      bot_id: 'bot-1',
      bot_type: 'assistant',
      description: '公开描述',
      engine: 'OpenClaw',
      entity_id: 'entity-1',
      name: '公开助手',
      owner_name: 'Owner',
      status: 'online',
      private_token: 'must-not-render',
    };
    mockedSearchPublicBots.mockResolvedValue({
      code: 200000,
      data: {
        items: [transportBot],
        total: 1,
      },
    });

    await expect(new CollaborationSquareApiAdapter().listBots()).resolves.toEqual([
      {
        id: 'bot-1',
        name: '公开助手',
        ownerName: 'Owner',
        description: '公开描述',
        capabilities: [],
        relationshipStatus: 'none',
      },
    ]);
  });

  it('translates the domain query and propagates AbortSignal', async () => {
    mockedSearchPublicBots.mockResolvedValue({ code: 200000, data: { items: [], total: 0 } });
    const signal = new AbortController().signal;

    await new CollaborationSquareApiAdapter().listBots(
      { search: 'workflow', page: 2, pageSize: 10 },
      undefined,
      signal,
    );

    expect(mockedSearchPublicBots).toHaveBeenCalledWith({ search: 'workflow', page: 2, page_size: 10 }, signal);
  });

  it('maps ACE and malformed Catalog responses to typed domain errors', async () => {
    mockedSearchPublicBots.mockRejectedValueOnce(
      new PublicBotCatalogError('unauthenticated', 'Bot Catalog request requires authentication'),
    );
    await expect(new CollaborationSquareApiAdapter().listBots()).rejects.toMatchObject({
      code: 'unauthenticated',
    });

    mockedSearchPublicBots.mockRejectedValueOnce(
      new PublicBotCatalogError('protocol_error', 'Unexpected Bot Catalog business response'),
    );
    await expect(new CollaborationSquareApiAdapter().listBots()).rejects.toMatchObject({
      code: 'protocol_error',
    });
  });

  it('maps transport authentication errors and preserves request cancellation', async () => {
    mockedSearchPublicBots.mockRejectedValueOnce(
      new BackendRequestError('unauthorized', { status: 401, apiPath: '/openapi/v1/bots/catalog/search' }),
    );
    await expect(new CollaborationSquareApiAdapter().listBots()).rejects.toMatchObject({
      code: 'unauthenticated',
    });

    const abortError = new DOMException('aborted', 'AbortError');
    mockedSearchPublicBots.mockRejectedValueOnce(abortError);
    await expect(new CollaborationSquareApiAdapter().listBots()).rejects.toBe(abortError);

    const crossRealmAbortError = new Error('aborted');
    crossRealmAbortError.name = 'AbortError';
    mockedSearchPublicBots.mockRejectedValueOnce(crossRealmAbortError);
    await expect(new CollaborationSquareApiAdapter().listBots()).rejects.toBe(crossRealmAbortError);
  });

  it('maps discovery Bot fields and ignores nested recommendation internals', async () => {
    mockedDiscoverPublicBots.mockResolvedValue({
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
            recommendation: {
              score: 0.98,
              reasons: ['语义匹配'],
              short_profile: '内部推荐摘要',
            },
          },
        ],
        total: 1,
      },
    });
    const signal = new AbortController().signal;

    const result = await new CollaborationSquareApiAdapter().discoverBots(
      { keyword: '整理会议纪要' },
      undefined,
      signal,
    );

    expect(mockedDiscoverPublicBots).toHaveBeenCalledWith(
      {
        keyword: '整理会议纪要',
        top_k: 20,
        min_score: 0.1,
        runtime_state: 'online',
      },
      signal,
    );
    expect(result).toEqual([
      {
        id: 'bot-smart-1',
        name: '会议助手',
        ownerName: 'Owner',
        description: '擅长会议纪要',
        capabilities: [],
        relationshipStatus: 'none',
      },
    ]);
    expect(JSON.stringify(result)).not.toContain('recommendation');
    expect(JSON.stringify(result)).not.toContain('内部推荐摘要');
  });

  it('maps only public normal groups from the real list response', async () => {
    mockedListPublicGroups.mockResolvedValue({
      code: 20000,
      data: {
        items: [
          {
            group_id: 'group-public-1',
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
          {
            group_id: 'group-private-1',
            version: 1,
            kind: 'normal',
            status: 'active',
            visibility: 'private',
            originator_actor_id: 'human-1',
            participants: [],
            driver_bot_uuid: 'bot-1',
            collaboration: { strategy: 'manager_worker' },
            name: '私有群',
            created_at: 1,
            updated_at: 2,
          },
        ],
        total: 2,
      },
    });
    const signal = new AbortController().signal;

    const result = await new CollaborationSquareApiAdapter().listGroups(
      { search: '公开', offset: 0, limit: 20 },
      signal,
    );

    expect(mockedListPublicGroups).toHaveBeenCalledWith({ q: '公开', offset: 0, limit: 20 }, signal);
    expect(result).toEqual([
      expect.objectContaining({
        id: 'group-public-1',
        name: '公开协作群',
        ownerBotName: '主理 Bot',
        ownerUserName: 'Owner',
        memberCount: 2,
      }),
    ]);
  });

  it('enriches Search and Discovery results with approved and pending Friend Connections', async () => {
    mockedSearchPublicBots.mockResolvedValue({
      code: 200000,
      data: {
        items: [
          {
            bot_id: 'bot-friend',
            name: '好友',
            owner_name: 'Owner',
            description: '',
            bot_type: 'assistant',
            engine: 'OpenClaw',
            entity_id: 'e1',
            status: 'online',
          },
          {
            bot_id: 'bot-applying',
            name: '申请中',
            owner_name: 'Owner',
            description: '',
            bot_type: 'assistant',
            engine: 'OpenClaw',
            entity_id: 'e2',
            status: 'online',
          },
          {
            bot_id: 'bot-new',
            name: '新 Bot',
            owner_name: 'Owner',
            description: '',
            bot_type: 'assistant',
            engine: 'OpenClaw',
            entity_id: 'e3',
            status: 'online',
          },
        ],
        total: 3,
      },
    });
    mockedListFriendConnections.mockResolvedValue({
      code: 20000,
      data: {
        items: [
          {
            from_actor: { type: 'human', id: '327325' },
            to_actor: { type: 'bot', id: 'bot-friend' },
            status: 'approved',
          },
        ],
        total: 1,
      },
    });
    mockedListFriendConnectionRequests
      .mockResolvedValueOnce({
        code: 20000,
        data: {
          items: [
            {
              request_id: 'request-1',
              from_actor: { type: 'human', id: '327325' },
              to_actor: { type: 'bot', id: 'bot-applying' },
              status: 'pending',
            },
          ],
          total: 101,
        },
      })
      .mockResolvedValueOnce({
        code: 20000,
        data: {
          items: [
            {
              request_id: 'request-2',
              from_actor: { type: 'human', id: '327325' },
              to_actor: { type: 'bot', id: 'bot-other' },
              status: 'pending',
            },
          ],
          total: 101,
        },
      });

    const result = await new CollaborationSquareApiAdapter().listBots({}, humanContext);

    expect(mockedListFriendConnections).toHaveBeenCalledWith({ actor_type: 'human', actor_id: '327325' }, undefined);
    expect(mockedListFriendConnectionRequests).toHaveBeenNthCalledWith(
      1,
      {
        direction: 'sent',
        status: 'pending',
        actor_type: 'human',
        actor_id: '327325',
        page: 1,
        page_size: 100,
      },
      undefined,
    );
    expect(mockedListFriendConnectionRequests).toHaveBeenNthCalledWith(
      2,
      {
        direction: 'sent',
        status: 'pending',
        actor_type: 'human',
        actor_id: '327325',
        page: 2,
        page_size: 100,
      },
      undefined,
    );
    expect(result.map((bot) => [bot.id, bot.relationshipStatus])).toEqual([
      ['bot-friend', 'friend'],
      ['bot-applying', 'applying'],
      ['bot-new', 'none'],
    ]);
  });

  it('rejects an unexpected Friend Connections business code instead of treating every Bot as unrelated', async () => {
    mockedSearchPublicBots.mockResolvedValue({ code: 200000, data: { items: [], total: 0 } });
    mockedListFriendConnections.mockResolvedValue({ code: 200000, data: { items: [], total: 0 } });

    await expect(new CollaborationSquareApiAdapter().listBots({}, humanContext)).rejects.toMatchObject({
      code: 'protocol_error',
    });
  });

  it.each([
    ['pending', 'applying'],
    ['approved', 'friend'],
    ['public_no_edge', 'none'],
  ] as const)('maps Friend Connection request status %s to %s', async (status, expected) => {
    mockedCreateFriendConnectionRequest.mockResolvedValue({
      code: 20100,
      data: { request_ids: ['request-1'], edge_ids: [], status, auto_accepted: status === 'approved' },
    });

    await expect(new CollaborationSquareApiAdapter().requestBotFriendship('bot-1', humanContext)).resolves.toEqual({
      status: expected,
    });
    expect(mockedCreateFriendConnectionRequest).toHaveBeenCalledWith({ to_actor: { type: 'bot', id: 'bot-1' } });
  });

  it('rejects an unknown Friend Connection request status as a protocol error', async () => {
    mockedCreateFriendConnectionRequest.mockResolvedValue({
      code: 20100,
      data: { request_ids: [], edge_ids: [], status: 'mystery' as never, auto_accepted: false },
    });

    await expect(new CollaborationSquareApiAdapter().requestBotFriendship('bot-1', humanContext)).rejects.toMatchObject(
      { code: 'protocol_error' },
    );
  });

  it('rejects an unexpected Friend Connection request business code', async () => {
    mockedCreateFriendConnectionRequest.mockResolvedValue({
      code: 20000,
      data: { request_ids: [], edge_ids: [], status: 'approved', auto_accepted: true },
    });

    await expect(new CollaborationSquareApiAdapter().requestBotFriendship('bot-1', humanContext)).rejects.toMatchObject(
      { code: 'protocol_error' },
    );
  });

  it('keeps the Bot available when the request endpoint returns bot_not_found', async () => {
    mockedCreateFriendConnectionRequest.mockRejectedValue(
      new BackendRequestError('Bot not found', {
        status: 404,
        data: { code: 40400, data: { error_code: 'bot_not_found' } },
        apiPath: '/openapi/v1/collaboration/friend-connections/requests',
      }),
    );

    await expect(new CollaborationSquareApiAdapter().requestBotFriendship('bot-1', humanContext)).rejects.toMatchObject(
      {
        code: 'network',
        message: '目标 Bot 当前不可用，申请未提交，请稍后重试',
      },
    );
  });

  it('creates a private Bot session with split Bot identity and the normalized Human user id', async () => {
    mockedCreateBotSession.mockResolvedValue({
      code: 200000,
      data: {
        session_id: 'session-1',
        title: '新会话',
        agent_id: 'agent-1',
        model: 'model-1',
        message_count: 0,
        gmt_create: '2026-08-22T00:00:00Z',
        gmt_modified: '2026-08-22T00:00:00Z',
      },
    });

    await expect(new CollaborationSquareApiAdapter().openBotConversation('bot-1:2088', humanContext)).resolves.toEqual({
      sessionId: 'session-1',
    });
    expect(mockedCreateBotSession).toHaveBeenCalledWith('bot-1', { user_id: '327325', owner_id: '2088' }, {});
  });

  it('rejects a session response without session_id as a protocol error', async () => {
    mockedCreateBotSession.mockResolvedValue({ code: 200000, data: {} as never });

    await expect(new CollaborationSquareApiAdapter().openBotConversation('bot-1', humanContext)).rejects.toMatchObject({
      code: 'protocol_error',
    });
  });

  it('rejects an unexpected private Session business code even when session_id is present', async () => {
    mockedCreateBotSession.mockResolvedValue({ code: 20100, data: { session_id: 'session-1' } as never });

    await expect(new CollaborationSquareApiAdapter().openBotConversation('bot-1', humanContext)).rejects.toMatchObject({
      code: 'protocol_error',
    });
  });

  it('maps action authentication errors without falling back to Mock', async () => {
    mockedCreateFriendConnectionRequest.mockRejectedValue(
      new BackendRequestError('forbidden', { status: 403, apiPath: '/friend-connections/requests' }),
    );
    await expect(new CollaborationSquareApiAdapter().requestBotFriendship('bot-1', humanContext)).rejects.toMatchObject(
      { code: 'forbidden' },
    );

    mockedCreateBotSession.mockRejectedValue(
      new BackendRequestError('unauthorized', { status: 401, apiPath: '/sessions' }),
    );
    await expect(new CollaborationSquareApiAdapter().openBotConversation('bot-1', humanContext)).rejects.toMatchObject({
      code: 'unauthenticated',
    });
  });

  it('keeps deferred Bot detail and Group detail/action capabilities explicitly unsupported', async () => {
    const adapter = new CollaborationSquareApiAdapter();

    await expect(adapter.getBotProfile('bot-1')).rejects.toMatchObject({ code: 'unsupported' });
    await expect(adapter.listGroupMembers('group-1')).rejects.toMatchObject({ code: 'unsupported' });
    await expect(adapter.createGroupSession('group-1')).rejects.toMatchObject({ code: 'unsupported' });
  });
});
