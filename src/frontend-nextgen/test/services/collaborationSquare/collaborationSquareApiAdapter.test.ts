import * as botSessionController from '@/services/backendApi/bots/privateBotSessionController';
import * as collaborationBotController from '@/services/backendApi/collaboration/collaborationBotController';
import * as friendConnectionController from '@/services/backendApi/collaboration/collaborationFriendConnectionController';
import * as groupController from '@/services/backendApi/collaboration/collaborationGroupController';
import * as publicBotController from '@/services/backendApi/collaboration/publicBotController';
import { PublicBotCatalogError } from '@/services/backendApi/collaboration/publicBotController';
import * as sessionController from '@/services/backendApi/collaboration/sessionController';
import { BackendRequestError } from '@/services/backendApi/httpClient';
import { CollaborationSquareApiAdapter } from '@/services/collaborationSquare/collaborationSquareApiAdapter';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';

const mockedSearchPublicBots = jest.spyOn(publicBotController, 'searchPublicBots');
const mockedDiscoverPublicBots = jest.spyOn(publicBotController, 'discoverPublicBots');
const mockedListPublicGroups = jest.spyOn(groupController, 'listPublicGroups');
const mockedQueryCollaborationBots = jest.spyOn(collaborationBotController, 'queryCollaborationBots');
const mockedListFriendConnections = jest.spyOn(friendConnectionController, 'listFriendConnections');
const mockedListFriendConnectionRequests = jest.spyOn(friendConnectionController, 'listFriendConnectionRequests');
const mockedCreateFriendConnectionRequest = jest.spyOn(friendConnectionController, 'createFriendConnectionRequest');
const mockedCreateBotSession = jest.spyOn(botSessionController, 'createBotSession');
const mockedCreateGroupSession = jest.spyOn(sessionController, 'createSession');
const humanContext = { actorId: 'human_327325', userId: '327325' };

beforeEach(() => {
  mockedSearchPublicBots.mockReset();
  mockedDiscoverPublicBots.mockReset();
  mockedListPublicGroups.mockReset();
  mockedQueryCollaborationBots.mockReset();
  mockedListFriendConnections.mockReset();
  mockedListFriendConnectionRequests.mockReset();
  mockedCreateFriendConnectionRequest.mockReset();
  mockedCreateBotSession.mockReset();
  mockedCreateGroupSession.mockReset();
});

describe('CollaborationSquareApiAdapter', () => {
  it('maps an empty successful public Bot page to an empty domain list', async () => {
    mockedSearchPublicBots.mockResolvedValue({ code: 200000, data: { items: [], total: 0 } });

    await expect(new CollaborationSquareApiAdapter().listBots()).resolves.toEqual([]);
    expect(mockedSearchPublicBots).toHaveBeenCalledWith({ page: 1, page_size: 20 }, undefined);
  });

  it('保留公开 Bot 分页 total，避免映射后的条数误判为末页', async () => {
    mockedSearchPublicBots.mockResolvedValue({
      code: 200000,
      data: {
        items: [],
        total: 48,
      },
    });

    await expect(new CollaborationSquareApiAdapter().listBotPage({ page: 1, pageSize: 24 })).resolves.toEqual({
      items: [],
      total: 48,
    });
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
        friendRequestBotId: 'bot-1:entity-1',
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
        friendRequestBotId: 'bot-smart-1:entity-smart-1',
        shortProfile: '内部推荐摘要',
      },
    ]);
    // 仅暴露 short_profile 为 shortProfile，不透传原始 recommendation 结构。
    expect(JSON.stringify(result)).not.toContain('recommendation');
    expect(result[0]?.shortProfile).toBe('内部推荐摘要');
  });

  it('透传 viewer 身份参数到 Search / Discovery controller', async () => {
    const catalogBot = {
      bot_id: 'bot-1',
      bot_type: 'assistant',
      description: '',
      engine: 'OpenClaw',
      name: 'Bot',
      owner_name: 'Owner',
      status: 'online',
    };
    mockedSearchPublicBots.mockResolvedValue({ code: 200000, data: { items: [catalogBot], total: 1 } });
    mockedDiscoverPublicBots.mockResolvedValue({ code: 200000, data: { items: [], total: 0 } });
    // viewer 存在时关系回填会以该身份查询好友关系/申请，mock 为空页。
    mockedListFriendConnections.mockResolvedValue({ code: 20000, data: { items: [], total: 0 } });
    mockedListFriendConnectionRequests.mockResolvedValue({ code: 20000, data: { items: [], total: 0 } });
    const adapter = new CollaborationSquareApiAdapter();
    const viewer = { viewerActorType: 'human' as const, viewerActorId: '327325' };

    await adapter.listBots({ page: 1, pageSize: 5, ...viewer });
    expect(mockedSearchPublicBots).toHaveBeenCalledWith(
      { page: 1, page_size: 5, viewer_actor_type: 'human', viewer_actor_id: '327325' },
      undefined,
    );
    // Search 路径仅补读 pending 申请，回填 actor 跟随 viewer（human）。
    expect(mockedListFriendConnectionRequests).toHaveBeenCalledWith(
      expect.objectContaining({ actor_type: 'human', actor_id: '327325', direction: 'sent', status: 'pending' }),
      undefined,
    );

    await adapter.discoverBots({ keyword: '代码', ...viewer });
    expect(mockedDiscoverPublicBots).toHaveBeenCalledWith(
      {
        keyword: '代码',
        top_k: 20,
        min_score: 0.1,
        runtime_state: 'online',
        viewer_actor_type: 'human',
        viewer_actor_id: '327325',
      },
      undefined,
    );
  });

  it('将 entity_id 等于当前 human user_id 的公开 Bot 标记为自有 Bot', async () => {
    mockedSearchPublicBots.mockResolvedValue({
      code: 200000,
      data: {
        items: [
          {
            bot_id: 'owned-bot',
            bot_type: 'assistant',
            description: '',
            engine: 'OpenClaw',
            entity_id: '327325',
            name: '我的公开 Bot',
            owner_name: '当前用户',
            status: 'online',
            is_friend: false,
          },
        ],
        total: 1,
      },
    });
    mockedListFriendConnectionRequests.mockResolvedValue({
      code: 20000,
      data: {
        items: [
          {
            request_id: 'request-owned-bot',
            from_actor: { type: 'human', id: '327325' },
            to_actor: { type: 'bot', id: 'owned-bot:327325' },
            status: 'pending',
          },
        ],
        total: 1,
      },
    });

    const result = await new CollaborationSquareApiAdapter().listBotPage(
      { viewerActorType: 'human', viewerActorId: '327325' },
      humanContext,
    );

    expect(result.items[0]).toEqual(
      expect.objectContaining({
        id: 'owned-bot',
        relationshipStatus: 'none',
        isOwnedByViewer: true,
      }),
    );
  });

  it('viewer 为 bot 时不把 bot viewer id 当作 human owner id', async () => {
    mockedSearchPublicBots.mockResolvedValue({
      code: 200000,
      data: {
        items: [
          {
            bot_id: 'bot-1',
            bot_type: 'assistant',
            description: '',
            engine: 'OpenClaw',
            entity_id: 'bot-viewer',
            name: 'Bot',
            owner_name: 'Owner',
            status: 'online',
          },
        ],
        total: 1,
      },
    });
    mockedListFriendConnectionRequests.mockResolvedValue({ code: 20000, data: { items: [], total: 0 } });

    const result = await new CollaborationSquareApiAdapter().listBotPage({
      viewerActorType: 'bot',
      viewerActorId: 'bot-viewer',
    });

    expect(result.items[0]?.isOwnedByViewer).toBeUndefined();
  });

  it('viewer 为 bot 时关系回填以 bot 身份查询好友申请', async () => {
    mockedSearchPublicBots.mockResolvedValue({
      code: 200000,
      data: {
        items: [
          {
            bot_id: 'bot-1',
            bot_type: 'assistant',
            description: '',
            engine: 'OpenClaw',
            name: 'Bot',
            owner_name: 'Owner',
            status: 'online',
          },
        ],
        total: 1,
      },
    });
    mockedListFriendConnectionRequests.mockResolvedValue({ code: 20000, data: { items: [], total: 0 } });

    await new CollaborationSquareApiAdapter().listBots({
      page: 1,
      pageSize: 5,
      viewerActorType: 'bot',
      viewerActorId: 'bot-xyz',
    });

    expect(mockedListFriendConnectionRequests).toHaveBeenCalledWith(
      expect.objectContaining({ actor_type: 'bot', actor_id: 'bot-xyz', direction: 'sent', status: 'pending' }),
      undefined,
    );
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
            participants: [],
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
    mockedQueryCollaborationBots.mockResolvedValue({
      code: 20000,
      data: { items: [{ bot_id: 'bot-1', kind: 'bot', name: '主理 Bot' }] },
    });

    const result = await new CollaborationSquareApiAdapter().listGroups(
      { search: '公开', offset: 0, limit: 20 },
      signal,
    );

    expect(mockedListPublicGroups).toHaveBeenCalledWith({ q: '公开', offset: 0, limit: 20 }, signal);
    // 公开群目录无 participants，群主名经 bots/query 按 driver_bot_uuid 反查。
    expect(mockedQueryCollaborationBots).toHaveBeenCalledWith({ bot_ids: ['bot-1'] });
    expect(result).toEqual([
      expect.objectContaining({
        id: 'group-public-1',
        name: '公开协作群',
        ownerBotName: '主理 Bot',
        ownerUserName: '未公开',
        memberCount: 2,
      }),
    ]);
  });

  it('listGroups: driver bot 查不到时回退展示 uuid，不显示"未公开"', async () => {
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
            participants: [],
            driver_bot_uuid: 'bot-deleted',
            collaboration: { strategy: 'chat', delivery_policy: { bot_final_delivery: 'send_to_driver' } },
            name: '公开协作群',
            participant_count: 1,
            created_at: 1,
            updated_at: 1,
          },
        ],
        total: 1,
      },
    });
    // bots/query 返回空（driver bot 已删除/查不到）。
    mockedQueryCollaborationBots.mockResolvedValue({ code: 20000, data: { items: [] } });
    const result = await new CollaborationSquareApiAdapter().listGroups({ offset: 0, limit: 20 });
    expect(mockedQueryCollaborationBots).toHaveBeenCalledWith({ bot_ids: ['bot-deleted'] });
    expect(result[0]?.ownerBotName).toBe('bot-deleted');
  });

  it('maps Search bot_uuid/is_friend and only reads pending requests for refresh recovery', async () => {
    mockedSearchPublicBots.mockResolvedValue({
      code: 200000,
      data: {
        items: [
          {
            bot_id: 'bot-friend',
            bot_uuid: 'bot-friend:e1',
            name: '好友',
            owner_name: 'Owner',
            description: '',
            bot_type: 'assistant',
            engine: 'OpenClaw',
            entity_id: 'e1',
            status: 'online',
            is_friend: true,
          },
          {
            bot_id: 'bot-applying',
            bot_uuid: 'bot-applying:e2',
            name: '申请中',
            owner_name: 'Owner',
            description: '',
            bot_type: 'assistant',
            engine: 'OpenClaw',
            entity_id: 'e2',
            status: 'online',
            is_friend: false,
          },
        ],
        total: 2,
      },
    });
    mockedListFriendConnectionRequests.mockResolvedValue({
      code: 20000,
      data: {
        items: [
          {
            request_id: 'request-1',
            from_actor: { type: 'human', id: '327325' },
            to_actor: { type: 'bot', id: 'bot-applying:e2' },
            status: 'pending',
          },
        ],
        total: 1,
      },
    });

    const result = await new CollaborationSquareApiAdapter().listBots({}, humanContext);

    expect(mockedListFriendConnections).not.toHaveBeenCalled();
    expect(mockedListFriendConnectionRequests).toHaveBeenCalledWith(
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
    expect(result.map((bot) => [bot.id, bot.relationshipStatus])).toEqual([
      ['bot-friend:e1', 'friend'],
      ['bot-applying:e2', 'applying'],
    ]);
    expect(result.map((bot) => bot.friendRequestBotId)).toEqual([undefined, undefined]);
  });

  it('still enriches Discovery results with approved and pending Friend Connections', async () => {
    mockedDiscoverPublicBots.mockResolvedValue({
      code: 200000,
      data: {
        items: [
          {
            bot_id: 'bot-friend',
            bot_uuid: 'bot-friend:e1',
            name: '好友',
            owner_name: 'Owner',
            description: '',
            bot_type: 'assistant',
            engine: 'OpenClaw',
            entity_id: 'e1',
            status: 'online',
            is_friend: false,
          },
          {
            bot_id: 'bot-applying',
            bot_uuid: 'bot-applying:e2',
            name: '申请中',
            owner_name: 'Owner',
            description: '',
            bot_type: 'assistant',
            engine: 'OpenClaw',
            entity_id: 'e2',
            status: 'online',
            is_friend: false,
          },
          {
            bot_id: 'bot-new',
            bot_uuid: 'bot-new:e3',
            name: '新 Bot',
            owner_name: 'Owner',
            description: '',
            bot_type: 'assistant',
            engine: 'OpenClaw',
            entity_id: 'e3',
            status: 'online',
            is_friend: false,
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
            to_actor: { type: 'bot', id: 'bot-friend:e1' },
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
              to_actor: { type: 'bot', id: 'bot-applying:e2' },
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

    const result = await new CollaborationSquareApiAdapter().discoverBots({ keyword: 'workflow' }, humanContext);

    expect(mockedListFriendConnections).toHaveBeenCalledWith({ actor_type: 'human', actor_id: '327325' }, undefined);
    expect(mockedListFriendConnectionRequests).toHaveBeenCalledTimes(2);
    expect(result.map((bot) => [bot.id, bot.relationshipStatus])).toEqual([
      ['bot-friend:e1', 'friend'],
      ['bot-applying:e2', 'applying'],
      ['bot-new:e3', 'none'],
    ]);
  });

  it('does not use Friend Connections as a fallback when Search returns an empty page', async () => {
    mockedSearchPublicBots.mockResolvedValue({ code: 200000, data: { items: [], total: 0 } });
    mockedListFriendConnections.mockResolvedValue({ code: 200000, data: { items: [], total: 0 } });

    await expect(new CollaborationSquareApiAdapter().listBots({}, humanContext)).resolves.toEqual([]);
    expect(mockedListFriendConnections).not.toHaveBeenCalled();
    expect(mockedListFriendConnectionRequests).not.toHaveBeenCalled();
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
    expect(mockedCreateFriendConnectionRequest).toHaveBeenCalledWith({
      to_actor: { type: 'bot', id: 'bot-1' },
      from_actor: { type: 'human', id: '327325' },
    });
  });

  it('uses an explicit bot_uuid when the catalog provides one', async () => {
    mockedCreateFriendConnectionRequest.mockResolvedValue({
      code: 20100,
      data: { request_ids: ['request-1'], edge_ids: [], status: 'pending', auto_accepted: false },
    });

    await expect(
      new CollaborationSquareApiAdapter().requestBotFriendship('default', humanContext, 'explicit-bot-uuid'),
    ).resolves.toEqual({ status: 'applying' });
    expect(mockedCreateFriendConnectionRequest).toHaveBeenCalledWith({
      to_actor: { type: 'bot', id: 'explicit-bot-uuid' },
      from_actor: { type: 'human', id: '327325' },
    });
  });

  it('falls back to the composite bot_id:entity_id target for friend requests', async () => {
    mockedCreateFriendConnectionRequest.mockResolvedValue({
      code: 20100,
      data: { request_ids: ['request-1'], edge_ids: [], status: 'pending', auto_accepted: false },
    });

    await expect(
      new CollaborationSquareApiAdapter().requestBotFriendship(
        '20260410_kt9ermvn',
        humanContext,
        '20260410_kt9ermvn:431368',
      ),
    ).resolves.toEqual({ status: 'applying' });
    expect(mockedCreateFriendConnectionRequest).toHaveBeenCalledWith({
      to_actor: { type: 'bot', id: '20260410_kt9ermvn:431368' },
      from_actor: { type: 'human', id: '327325' },
    });
  });

  it('honor an explicit fromActor for friend requests (对话协作按当前角色 tab)', async () => {
    mockedCreateFriendConnectionRequest.mockResolvedValue({
      code: 20100,
      data: { request_ids: ['request-1'], edge_ids: [], status: 'pending', auto_accepted: false },
    });

    await expect(
      new CollaborationSquareApiAdapter().requestBotFriendship('bot-1', humanContext, undefined, {
        type: 'bot',
        id: 'bot-xyz',
      }),
    ).resolves.toEqual({ status: 'applying' });
    expect(mockedCreateFriendConnectionRequest).toHaveBeenCalledWith({
      to_actor: { type: 'bot', id: 'bot-1' },
      from_actor: { type: 'bot', id: 'bot-xyz' },
    });
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

  it('creates a public group session from the authenticated human actor', async () => {
    mockedCreateGroupSession.mockResolvedValue({
      code: 20100,
      message: 'Created',
      data: {
        session_id: 'session-1',
        group_id: 'group-1',
        version: 1,
        status: 'running',
        participants: [
          {
            actor_id: 'human_327325',
            actor_kind: 'human',
            role: 'consultant',
            mode: 'present',
          },
        ],
        created_at: 1,
        updated_at: 1,
      },
      request_id: 'r',
    });

    await expect(new CollaborationSquareApiAdapter().createGroupSession('group-1', humanContext)).resolves.toEqual({
      sessionId: 'session-1',
      defaultRole: '顾问',
      memberSource: 'session_temp',
    });
    expect(mockedCreateGroupSession).toHaveBeenCalledWith('group-1', { kind: 'chat', acting_bot_id: 'human_327325' });
  });

  it('rejects a public group session response without session_id or caller role', async () => {
    mockedCreateGroupSession.mockResolvedValue({
      code: 20100,
      message: 'Created',
      data: {
        session_id: '',
        group_id: 'group-1',
        version: 1,
        status: 'running',
        participants: [],
        created_at: 1,
        updated_at: 1,
      },
      request_id: 'r',
    });

    await expect(new CollaborationSquareApiAdapter().createGroupSession('group-1', humanContext)).rejects.toMatchObject(
      {
        code: 'protocol_error',
      },
    );
  });

  it('keeps deferred Bot detail explicitly unsupported; listGroupMembers resolves via group detail', async () => {
    const adapter = new CollaborationSquareApiAdapter();

    await expect(adapter.getBotProfile('bot-1')).rejects.toMatchObject({ code: 'unsupported' });
  });

  it('listGroupMembers 调 getGroup 详情并映射 participants → PublicGroupMember[]', async () => {
    const mockedGetGroup = jest.spyOn(groupController, 'getGroup').mockResolvedValue({
      code: 20000,
      data: {
        group_id: 'group-1',
        version: 1,
        kind: 'normal',
        status: 'active',
        visibility: 'public',
        originator_actor_id: 'human_1',
        participants: [
          { actor_id: 'bot-1', actor_kind: 'bot', name: '主理 Bot', role: 'manager', mode: 'auto' },
          { actor_id: 'human_1', actor_kind: 'human', name: '章梧', role: 'consultant', mode: 'present' },
        ],
        driver_bot_uuid: 'bot-1',
        collaboration: { strategy: 'chat', delivery_policy: { bot_final_delivery: 'send_to_driver' } },
        created_at: 1,
        updated_at: 1,
      },
    });
    const members = await new CollaborationSquareApiAdapter().listGroupMembers('group-1');
    expect(mockedGetGroup).toHaveBeenCalledWith('group-1');
    expect(members).toEqual([
      { id: 'bot-1', displayName: '主理 Bot', type: 'bot', role: 'manager' },
      { id: 'human_1', displayName: '章梧', type: 'human', role: 'consultant' },
    ]);
    mockedGetGroup.mockRestore();
  });
});
