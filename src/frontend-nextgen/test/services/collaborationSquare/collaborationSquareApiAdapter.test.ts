import * as botSessionController from '@/services/backendApi/bots/privateBotSessionController';
import * as bbsTaskController from '@/services/backendApi/collaboration/bbsTaskController';
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
const mockedListBbsTasks = jest.spyOn(bbsTaskController, 'listBbsTasks');
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
  mockedListBbsTasks.mockReset();
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
        isOwnedByLoggedInUser: true,
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

    expect(result.items[0]?.isOwnedByLoggedInUser).toBeUndefined();
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

  it('enriches Discovery results with connected actors and pending Friend Connection requests', async () => {
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
            actor: { type: 'bot', id: 'bot-friend:e1' },
            is_online: false,
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

  describe('listPublicTasks (BBS list 端点)', () => {
    const bbsItem = (
      overrides: Partial<{
        task_id: string;
        title: string;
        goal: string;
        status: string;
        publisher: string | null;
        assignee_id: string;
        assignee_name: string;
        relay_create_time: string;
        relay_begin_time: string;
        relay_end_time: string;
      }> = {},
    ) => ({
      task_id: 'bbs-1',
      title: '梳理需求',
      goal: '输出路线图',
      acceptances: [{ id: 'a1', description: '覆盖方向' }],
      status: 'PENDING',
      publisher: 'bot-pub-1',
      relay_create_time: '2026-09-01T09:00:00Z',
      ...overrides,
    });

    it('映射 BBS envelope（code 200000）到 PublicTask 列表，publisher 经 bots/query 反查', async () => {
      mockedListBbsTasks.mockResolvedValue({
        code: 200000,
        message: 'OK',
        data: {
          total: 2,
          items: [
            bbsItem({ status: 'RUNNING', assignee_id: 'bot-asg', assignee_name: '运维助手', relay_begin_time: 't1' }),
            bbsItem({ task_id: 'bbs-2', status: 'SUCCESS', assignee_id: 'bot-asg', relay_end_time: 't2' }),
          ],
        },
        request_id: 'r',
      });
      // publisher 反查（与群主 driver_bot_uuid 反查同法）。
      mockedQueryCollaborationBots.mockResolvedValue({
        code: 20000,
        data: { items: [{ bot_id: 'bot-pub-1', kind: 'bot', name: '产品协作助手' }] },
      });

      const result = await new CollaborationSquareApiAdapter().listPublicTasks({}, undefined);

      // 空 query 不下发分页/过滤参数（交服务端默认 page=1, page_size=20）。
      expect(mockedListBbsTasks).toHaveBeenCalledWith({}, undefined);
      expect(mockedQueryCollaborationBots).toHaveBeenCalledWith({ bot_ids: ['bot-pub-1'] });
      expect(result).toEqual({
        items: [
          {
            id: 'bbs-1',
            name: '梳理需求',
            goal: '输出路线图',
            acceptanceCriteria: ['覆盖方向'],
            status: 'claimed',
            publisher: 'bot-pub-1',
            publisherBotName: '产品协作助手',
            publishedAt: '2026-09-01T09:00:00Z',
            claimedBotName: '运维助手',
            claimedAt: 't1',
          },
          {
            id: 'bbs-2',
            name: '梳理需求',
            goal: '输出路线图',
            acceptanceCriteria: ['覆盖方向'],
            status: 'completed',
            publisher: 'bot-pub-1',
            publisherBotName: '产品协作助手',
            publishedAt: '2026-09-01T09:00:00Z',
            claimedBotName: 'bot-asg',
            claimedAt: undefined,
            completedAt: 't2',
          },
        ],
        total: 2,
      });
    });

    it('DONE 项前向映射为 reviewing 并填完成时间（adapter 级覆盖 DONE→reviewing 分支）', async () => {
      mockedListBbsTasks.mockResolvedValue({
        code: 200000,
        data: { total: 1, items: [bbsItem({ task_id: 'bbs-rev', status: 'DONE', relay_end_time: 't3' })] },
        request_id: 'r',
      });
      mockedQueryCollaborationBots.mockResolvedValue({ code: 20000, data: { items: [] } });

      const result = await new CollaborationSquareApiAdapter().listPublicTasks({}, undefined);

      expect(result.items[0]).toEqual(
        expect.objectContaining({ id: 'bbs-rev', status: 'reviewing', completedAt: 't3' }),
      );
    });

    it('publisher 为复合 bot_id:owner 时拆 realBotId 反查，结果按复合 id 命中（不透传 :owner）', async () => {
      mockedListBbsTasks.mockResolvedValue({
        code: 200000,
        data: { total: 1, items: [bbsItem({ task_id: 't-comp', publisher: 'bot-pub-1:431368' })] },
      });
      // bots/query 注册表按裸 bot_id 建索引，返回的 bot_id 不带 :owner。
      mockedQueryCollaborationBots.mockResolvedValue({
        code: 20000,
        data: { items: [{ bot_id: 'bot-pub-1', kind: 'bot', name: '产品协作助手' }] },
      });

      const result = await new CollaborationSquareApiAdapter().listPublicTasks({}, undefined);

      // 下发裸 realBotId，不把 :owner 透传进 bots/query。
      expect(mockedQueryCollaborationBots).toHaveBeenCalledWith({ bot_ids: ['bot-pub-1'] });
      expect(result.items[0]?.publisherBotName).toBe('产品协作助手');
    });

    it('publisher nameMap 未命中兜底用 ID，publisher 为 null 时 publisherBotName 为 undefined', async () => {
      mockedListBbsTasks.mockResolvedValue({
        code: 200000,
        data: {
          total: 2,
          items: [
            bbsItem({ task_id: 't-miss', publisher: 'bot-missing' }),
            bbsItem({ task_id: 't-null', publisher: null }),
          ],
        },
      });
      mockedQueryCollaborationBots.mockResolvedValue({ code: 20000, data: { items: [] } });

      const result = await new CollaborationSquareApiAdapter().listPublicTasks({}, undefined);

      const byId = Object.fromEntries(result.items.map((t) => [t.id, t]));
      expect(byId['t-miss']?.publisherBotName).toBe('bot-missing');
      expect(byId['t-null']?.publisherBotName).toBeUndefined();
    });

    it('无 publisher 时不下发 bots/query 反查', async () => {
      mockedListBbsTasks.mockResolvedValue({
        code: 200000,
        data: { total: 1, items: [bbsItem({ task_id: 't-null', publisher: null })] },
      });
      await new CollaborationSquareApiAdapter().listPublicTasks({}, undefined);
      expect(mockedQueryCollaborationBots).not.toHaveBeenCalled();
    });

    it('assignee_name 缺失时按 assignee_id 拆 realBotId 反查回填 claimedBotName', async () => {
      mockedListBbsTasks.mockResolvedValue({
        code: 200000,
        data: {
          total: 1,
          items: [
            bbsItem({
              task_id: 't-asg',
              status: 'RUNNING',
              publisher: null,
              assignee_id: 'bot-asg:2088',
              relay_begin_time: 't1',
            }),
          ],
        },
      });
      mockedQueryCollaborationBots.mockResolvedValue({
        code: 20000,
        data: { items: [{ bot_id: 'bot-asg', kind: 'bot', name: '运维助手' }] },
      });

      const result = await new CollaborationSquareApiAdapter().listPublicTasks({}, undefined);

      // publisher 为 null 不参与反查；仅 assignee 拆 realBotId 下发。
      expect(mockedQueryCollaborationBots).toHaveBeenCalledWith({ bot_ids: ['bot-asg'] });
      expect(result.items[0]?.claimedBotName).toBe('运维助手');
    });

    it('空 data 数组返回空列表且不下发 bots/query', async () => {
      mockedListBbsTasks.mockResolvedValue({ code: 200000, data: { total: 0, items: [] } });
      const result = await new CollaborationSquareApiAdapter().listPublicTasks({}, undefined);
      expect(result).toEqual({ items: [], total: 0 });
      expect(mockedQueryCollaborationBots).not.toHaveBeenCalled();
    });

    it('未知 status 的 item 被 mapper 丢弃（不入列），total 仍为服务端值', async () => {
      mockedListBbsTasks.mockResolvedValue({
        code: 200000,
        data: {
          total: 3,
          items: [
            bbsItem({ task_id: 'valid', status: 'PENDING' }),
            bbsItem({ task_id: 'drop-cancelled', status: 'CANCELLED' }),
            bbsItem({ task_id: 'drop-failed', status: 'FAILED' }),
          ],
        },
      });
      mockedQueryCollaborationBots.mockResolvedValue({ code: 20000, data: { items: [] } });
      const result = await new CollaborationSquareApiAdapter().listPublicTasks({}, undefined);
      expect(result.items.map((t) => t.id)).toEqual(['valid']);
      // total 取自服务端（3），不随 mapper 丢弃未知态而变。
      expect(result.total).toBe(3);
    });

    it('offset/limit 换算为 1-based page/page_size 下发', async () => {
      mockedListBbsTasks.mockResolvedValue({ code: 200000, data: { total: 0, items: [] } });
      await new CollaborationSquareApiAdapter().listPublicTasks({ offset: 24, limit: 24 }, undefined);
      expect(mockedListBbsTasks).toHaveBeenCalledWith({ page: 2, page_size: 24 }, undefined);
    });

    it('search 换算为 search_word 下发；空/空白不下发', async () => {
      mockedListBbsTasks.mockResolvedValue({ code: 200000, data: { total: 0, items: [] } });
      await new CollaborationSquareApiAdapter().listPublicTasks({ search: '需求', offset: 0, limit: 24 }, undefined);
      expect(mockedListBbsTasks).toHaveBeenCalledWith({ page: 1, page_size: 24, search_word: '需求' }, undefined);
      // 空白 search 不下发 search_word。
      await new CollaborationSquareApiAdapter().listPublicTasks({ search: '   ', offset: 0, limit: 24 }, undefined);
      expect(mockedListBbsTasks).toHaveBeenLastCalledWith({ page: 1, page_size: 24 }, undefined);
    });

    it('广场态 status 映射为 BBS 原始态下发；all 不下发 status', async () => {
      mockedListBbsTasks.mockResolvedValue({ code: 200000, data: { total: 0, items: [] } });
      const adapter = new CollaborationSquareApiAdapter();
      await adapter.listPublicTasks({ status: 'claimed', offset: 0, limit: 24 }, undefined);
      expect(mockedListBbsTasks).toHaveBeenLastCalledWith({ page: 1, page_size: 24, status: 'RUNNING' }, undefined);
      await adapter.listPublicTasks({ status: 'reviewing', offset: 0, limit: 24 }, undefined);
      expect(mockedListBbsTasks).toHaveBeenLastCalledWith({ page: 1, page_size: 24, status: 'DONE' }, undefined);
      await adapter.listPublicTasks({ status: 'completed', offset: 0, limit: 24 }, undefined);
      expect(mockedListBbsTasks).toHaveBeenLastCalledWith({ page: 1, page_size: 24, status: 'SUCCESS' }, undefined);
      await adapter.listPublicTasks({ status: 'pending_claim', offset: 0, limit: 24 }, undefined);
      expect(mockedListBbsTasks).toHaveBeenLastCalledWith({ page: 1, page_size: 24, status: 'PENDING' }, undefined);
      // all 不下发 status。
      await adapter.listPublicTasks({ status: 'all', offset: 0, limit: 24 }, undefined);
      expect(mockedListBbsTasks).toHaveBeenLastCalledWith({ page: 1, page_size: 24 }, undefined);
    });

    it('total 为服务端过滤后行数，不等于本页 mapped 条数（驱动 hasMore）', async () => {
      mockedListBbsTasks.mockResolvedValue({
        code: 200000,
        data: { total: 100, items: [bbsItem({ task_id: 'a' }), bbsItem({ task_id: 'b' })] },
      });
      mockedQueryCollaborationBots.mockResolvedValue({ code: 20000, data: { items: [] } });
      const result = await new CollaborationSquareApiAdapter().listPublicTasks({ offset: 0, limit: 24 }, undefined);
      expect(result.items.map((t) => t.id)).toEqual(['a', 'b']);
      expect(result.total).toBe(100);
    });

    it('code != 200000 抛 protocol_error', async () => {
      mockedListBbsTasks.mockResolvedValue({ code: 20000, data: { total: 0, items: [] } } as never);
      await expect(new CollaborationSquareApiAdapter().listPublicTasks({}, undefined)).rejects.toMatchObject({
        code: 'protocol_error',
      });
    });

    it('ACE 登录失效映射为 unauthenticated', async () => {
      mockedListBbsTasks.mockResolvedValue({
        actionType: 'LOGIN',
        buserviceErrorCode: 'USER_NOT_LOGIN',
        decisionBy: 'ACE',
        buserviceErrorMsg: 'http://login',
      } as never);
      await expect(new CollaborationSquareApiAdapter().listPublicTasks({}, undefined)).rejects.toMatchObject({
        code: 'unauthenticated',
      });
    });

    it('AbortError 原样透传，不吞成 network', async () => {
      const abortError = new DOMException('aborted', 'AbortError');
      mockedListBbsTasks.mockRejectedValue(abortError);
      await expect(new CollaborationSquareApiAdapter().listPublicTasks({}, undefined)).rejects.toBe(abortError);
    });

    it('BackendRequestError 401 映射为 unauthenticated', async () => {
      mockedListBbsTasks.mockRejectedValue(
        new BackendRequestError('unauthorized', { status: 401, apiPath: '/api/v1/collaboration/tasks/bbs/list' }),
      );
      await expect(new CollaborationSquareApiAdapter().listPublicTasks({}, undefined)).rejects.toMatchObject({
        code: 'unauthenticated',
      });
    });

    it('getPublicTask 仍为 unsupported（详情改用列表项，不发请求）', async () => {
      await expect(new CollaborationSquareApiAdapter().getPublicTask('bbs-1')).rejects.toMatchObject({
        code: 'unsupported',
      });
    });
  });
});
