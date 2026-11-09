/** @jest-environment node */
import * as botsController from '@/services/backendApi/bots/botController';
import { generateBotAvatar } from '@/domain/botAvatar';
import * as botController from '@/services/backendApi/collaboration/collaborationBotController';
import * as friendConnectionController from '@/services/backendApi/collaboration/collaborationFriendConnectionController';
import * as publicBotController from '@/services/backendApi/collaboration/publicBotController';
import { collaborationCandidateService, type CollaborationBotView } from '@/services/workspace/collaborationCandidateService';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';

jest.mock('@/services/backendApi/collaboration/collaborationBotController');
jest.mock('@/services/backendApi/bots/botController');
jest.mock('@/services/backendApi/collaboration/collaborationFriendConnectionController');
jest.mock('@/services/backendApi/collaboration/publicBotController');

const bc = botController as unknown as Record<string, jest.Mock<any>>;
const bots = botsController as unknown as Record<string, jest.Mock<any>>;
const connections = friendConnectionController as unknown as Record<string, jest.Mock<any>>;
const publicBots = publicBotController as unknown as Record<string, jest.Mock<any>>;

function candidateView(partial: Partial<CollaborationBotView> & { id: string; name: string }): CollaborationBotView {
  return {
    online: true,
    status: 'online',
    reachability: 'reachable',
    visibility: 'public',
    ...partial,
  };
}

function discoveryResponse(items: Array<Record<string, unknown>>) {
  return {
    code: 200000 as const,
    data: { items, total: items.length },
  };
}

beforeEach(() => {
  jest.resetAllMocks();
});

describe('collaborationCandidateService', () => {
  it.each(['/avatars/saved.svg', undefined, null, '', '  '])(
    'preserves query/candidates avatar_url and shares management defaults for missing avatars (%p)',
    async (avatarUrl) => {
      const dto = {
        kind: 'bot', bot_id: 'b1:owner', name: 'Alpha', avatar_url: avatarUrl,
        status: 'online', reachability: 'reachable', visibility: 'public',
      };
      connections.listFriendConnections.mockResolvedValue({
        code: 20000, data: { items: [{ actor: { type: 'bot', id: dto.bot_id } }], total: 1 },
      });
      bc.queryCollaborationBots.mockResolvedValue({ code: 20000, data: { items: [dto], total: 1 } });
      bc.listBotCandidates.mockResolvedValue({
        code: 20000, data: { items: [{ bot: dto, is_friend: true }], total: 1 },
      });

      const friends = await collaborationCandidateService.listFriends('actor:owner', { detailSource: 'collaboration' });
      const candidates = await collaborationCandidateService.listCandidates('actor:owner');
      const expected = avatarUrl?.trim() || generateBotAvatar('b1');
      for (const result of [friends, candidates]) {
        expect(result).toMatchObject({ ok: true, data: { items: [{ id: dto.bot_id, avatarUrl: expected }] } });
      }
      expect(bc.queryCollaborationBots).toHaveBeenCalledWith({ bot_ids: [dto.bot_id] });
      expect(bots.listBotMetadata).not.toHaveBeenCalled();
    },
  );

  it('listMine filters human identities and returns bots only', async () => {
    bc.listMyBots.mockResolvedValue({
      code: 20000,
      data: {
        items: [
          { kind: 'human', bot_id: 'human_1', name: '我' },
          {
            kind: 'bot',
            bot_id: 'mine-1',
            name: '我的 Bot',
            status: 'online',
            reachability: 'reachable',
            visibility: 'public',
          },
        ],
        total: 2,
      },
    });

    const res = await collaborationCandidateService.listMine({ offset: 0, limit: 50 });

    expect(bc.listMyBots).toHaveBeenCalledWith({ offset: 0, limit: 100 });
    expect(res.ok && res.data.items).toEqual([expect.objectContaining({ id: 'mine-1', name: '我的 Bot' })]);
    expect(res.ok && res.data.hasMore).toBe(false);
  });

  it('listFriends loads Bot actor friend connections and enriches bot details', async () => {
    connections.listFriendConnections.mockResolvedValue({
      code: 20000,
      data: {
        items: [
          { actor: { type: 'bot', id: 'b1:actor-1' }, name: 'Alpha' },
          { actor: { type: 'bot', id: 'b2:actor-1' }, name: 'Beta' },
          { actor: { type: 'bot', id: 'b1:actor-1' }, name: 'Alpha duplicate' },
        ],
        total: 2,
      },
    });
    bots.listBotMetadata.mockResolvedValue({
      code: 200000,
      data: {
        items: [
          { bot_id: 'b1', owner_id: 'actor-1', bot_name: 'Alpha', status: 'ACTIVE' },
          { bot_id: 'b2', owner_id: 'actor-1', bot_name: 'Beta', status: 'INACTIVE' },
        ],
        total: 2,
      },
    });

    const res = await collaborationCandidateService.listFriends('actor-1:900003', { actorType: 'bot' });

    expect(connections.listFriendConnections).toHaveBeenCalledWith({
      actor_type: 'bot',
      actor_id: 'actor-1:900003',
    });
    expect(bc.listBotFriendships).not.toHaveBeenCalled();
    expect(bots.listBotMetadata).toHaveBeenCalledWith(
      { user_id: '900003', page: 1, page_size: 2 },
      {
        bots: [
          { bot_id: 'b1', owner_id: 'actor-1' },
          { bot_id: 'b2', owner_id: 'actor-1' },
        ],
      },
    );
    expect(res).toEqual({
      ok: true,
      data: {
        items: [
          {
            id: 'b1:actor-1',
            name: 'Alpha',
            avatarUrl: generateBotAvatar('b1'),
            online: true,
            status: 'online',
            botStatus: 'ACTIVE',
            reachability: 'reachable',
            visibility: 'private',
            isFriend: true,
          },
          {
            id: 'b2:actor-1',
            name: 'Beta',
            avatarUrl: generateBotAvatar('b2'),
            online: false,
            status: 'online',
            botStatus: 'INACTIVE',
            reachability: 'reachable',
            visibility: 'private',
            isFriend: true,
          },
        ],
        total: 2,
        offset: 0,
        limit: 50,
        hasMore: false,
      },
    });
  });

  it.each([true, false, null, undefined])(
    'preserves online_available=%p from friend metadata',
    async (onlineAvailable) => {
      connections.listFriendConnections.mockResolvedValue({
        code: 20000,
        data: { items: [{ actor: { type: 'bot', id: 'service:900003' } }], total: 1 },
      });
      bots.listBotMetadata.mockResolvedValue({
        code: 200000,
        data: {
          items: [
            {
              bot_id: 'service',
              owner_id: '900003',
              bot_name: '服务 Bot',
              bot_type: 'service',
              online_available: onlineAvailable,
            },
          ],
          total: 1,
        },
      });

      const res = await collaborationCandidateService.listFriends('human_900003', { actorType: 'human' });

      expect(res).toMatchObject({
        ok: true,
        data: { items: [{ id: 'service:900003', botType: 'service', onlineAvailable }] },
      });
    },
  );

  it('listFriends sends the Human staff id without the human_ prefix', async () => {
    connections.listFriendConnections.mockResolvedValue({
      code: 20000,
      data: {
        items: [{ actor: { type: 'bot', id: 'b1:900003' } }],
        total: 1,
      },
    });
    bots.listBotMetadata.mockResolvedValue({
      code: 200000,
      data: {
        items: [{ bot_id: 'b1', owner_id: '900003', bot_name: '蒜蓉粉丝虾', status: 'online', engine: 'OpenAI' }],
        total: 1,
      },
    });

    const res = await collaborationCandidateService.listFriends('human_900003', {
      actorType: 'human',
      offset: 0,
      limit: 100,
    });

    expect(connections.listFriendConnections).toHaveBeenCalledWith({
      actor_type: 'human',
      actor_id: '900003',
    });
    expect(bc.listBotFriendships).not.toHaveBeenCalled();
    expect(bots.listBotMetadata).toHaveBeenCalledWith(
      { user_id: '900003', page: 1, page_size: 1 },
      { bots: [{ bot_id: 'b1', owner_id: '900003' }] },
    );
    expect(res.ok && res.data.items[0]).toMatchObject({
      id: 'b1:900003',
      name: '蒜蓉粉丝虾',
      engine: 'OpenAI',
    });
  });

  it('listFriends uses collaboration bot query for collaboration details', async () => {
    connections.listFriendConnections.mockResolvedValue({
      code: 20000,
      data: {
        items: [{ actor: { type: 'bot', id: 'b1:900003' } }, { actor: { type: 'bot', id: 'b2:900003' } }],
        total: 2,
      },
    });
    bc.queryCollaborationBots.mockResolvedValue({
      code: 20000,
      data: {
        items: [
          {
            kind: 'bot',
            bot_id: 'b1:900003',
            name: '协作 Alpha',
            status: 'online',
            reachability: 'reachable',
            visibility: 'public',
          },
        ],
        total: 1,
      },
    });

    const res = await collaborationCandidateService.listFriends('human_900003', {
      actorType: 'human',
      detailSource: 'collaboration',
    });

    expect(bc.queryCollaborationBots).toHaveBeenCalledWith({ bot_ids: ['b1:900003', 'b2:900003'] });
    expect(bots.listBotMetadata).not.toHaveBeenCalled();
    expect(res.ok && res.data.items).toEqual([
      expect.objectContaining({ id: 'b1:900003', name: '协作 Alpha', isFriend: true }),
      expect.objectContaining({
        id: 'b2:900003',
        name: 'b2:900003',
        online: false,
        detailsResolved: false,
      }),
    ]);
  });

  it('listFriends keeps connection name when bot details are unavailable', async () => {
    connections.listFriendConnections.mockResolvedValue({
      code: 20000,
      data: {
        items: [{ actor: { type: 'bot', id: 'b1:actor-1' }, name: 'Alpha' }],
        total: 1,
      },
    });
    bots.listBotMetadata.mockResolvedValue({
      code: 200000,
      data: { items: [], total: 0 },
    });

    const res = await collaborationCandidateService.listFriends('actor-1', { actorType: 'bot' });

    expect(res.ok && res.data.items[0]).toMatchObject({
      id: 'b1:actor-1',
      name: 'Alpha',
      avatarUrl: generateBotAvatar('b1'),
      online: false,
      status: 'hidden',
      detailsResolved: false,
    });
    expect(res.ok && res.data.hasMore).toBe(false);
  });

  it('maps friend connection load failures to friendly messages', async () => {
    connections.listFriendConnections.mockRejectedValue(new Error('network'));
    const res = await collaborationCandidateService.listFriends('actor-1', { actorType: 'bot' });
    expect(res).toMatchObject({
      ok: false,
      error: { code: 'COLLABORATION_FRIENDS_LOAD_FAILED' },
    });
  });

  it('listCandidates passes collaboration purpose and name', async () => {
    bc.listBotCandidates.mockResolvedValue({
      code: 20000,
      data: {
        items: [
          {
            is_friend: false,
            bot: {
              kind: 'bot',
              bot_id: 'b3',
              name: 'Gamma',
              status: 'online',
              reachability: 'reachable',
              visibility: 'public',
            },
          },
        ],
      },
    });

    const res = await collaborationCandidateService.listCandidates('actor-1', { name: 'Ga', offset: 10 });

    expect(bc.listBotCandidates).toHaveBeenCalledWith('actor-1', {
      purpose: 'collaboration',
      name: 'Ga',
      offset: 10,
      limit: 50,
    });
    expect(res.ok && res.data.items[0]).toMatchObject({ id: 'b3', name: 'Gamma', isFriend: false });
  });

  it.each([undefined, 'data:image/svg+xml;utf8,%3Csvg%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%2F%3E'])(
    'renders catalog-visible discover results outside the loaded candidate page with avatar %p', async (avatarUrl) => {
      publicBots.discoverPublicBots.mockResolvedValue(discoveryResponse([{
        bot_id: 'discovered-bot', bot_uuid: 'discovered-bot:owner', entity_id: 'owner',
        bot_type: 'service', name: '测试专用', description: '', is_friend: false,
        visibility: 'protected', is_online: true, actor_kind: 'bot', engine: 'openclaw', status: 'ACTIVE',
        avatar_url: avatarUrl, recommendation: { score: 0.83927983045578, short_profile: '' },
      }]));
      const res = await collaborationCandidateService.recommendCandidates({
        actorId: 'actor:viewer', actorKind: 'bot', keyword: '数据分析', candidates: [],
      });
      expect(res).toMatchObject({ ok: true, data: [{
        id: 'discovered-bot:owner', name: '测试专用', online: true, reachability: 'reachable',
        visibility: 'protected', isFriend: false, botType: 'service', engine: 'openclaw',
        avatarUrl: avatarUrl || generateBotAvatar('discovered-bot'), recommendScore: 0.83927983045578,
      }] });
    },
  );

  it('recommendCandidates enriches cached Bots without dropping new discoveries and sorts online first', async () => {
    publicBots.discoverPublicBots.mockResolvedValue(
      discoveryResponse([
        { bot_uuid: 'b1:actor-1', recommendation: { score: 0.9, short_profile: '值班相关' } },
        { bot_id: 'b9', bot_uuid: 'b9:actor-9', name: 'New Bot', is_online: true, recommendation: { score: 0.95 } },
        { bot_uuid: 'b2:actor-1', recommendation: { score: 0.4, short_profile: '分析相关' } },
      ]),
    );

    const res = await collaborationCandidateService.recommendCandidates({
      actorId: 'actor-1',
      actorKind: 'bot',
      keyword: ' 值班 ',
      candidates: [
        candidateView({ id: 'b1:actor-1', name: 'Alpha', online: false }),
        candidateView({ id: 'b2:actor-1', name: 'Beta', online: true }),
      ],
    });

    expect(publicBots.discoverPublicBots).toHaveBeenCalledWith(
      {
        keyword: '值班',
        top_k: 20,
        min_score: 0.01,
        viewer_actor_type: 'bot',
        viewer_actor_id: 'actor-1',
      },
      undefined,
    );
    expect(res.ok && res.data).toHaveLength(3);
    expect(res.ok && res.data[0]).toMatchObject({ id: 'b9:actor-9', name: 'New Bot', online: true });
    // 在线项排在离线项之前；保留推荐分与画像摘要。
    expect(res.ok && res.data[1]).toMatchObject({
      id: 'b2:actor-1',
      name: 'Beta',
      online: true,
      recommendScore: 0.4,
      shortProfile: '分析相关',
    });
    expect(res.ok && res.data[2]).toMatchObject({
      id: 'b1:actor-1',
      name: 'Alpha',
      online: false,
      recommendScore: 0.9,
      shortProfile: '值班相关',
    });
  });

  it('maps current catalog online state without treating ACTIVE as online or losing known reachability', async () => {
    const offline = { bot_id: 'b1', bot_uuid: 'b1:owner', name: 'Catalog Name', status: 'ACTIVE',
      is_online: false, avatar_url: '/current.svg', visibility: 'protected', is_friend: false,
      recommendation: { score: 0.9, short_profile: '' } };
    publicBots.discoverPublicBots.mockResolvedValue(discoveryResponse([
      offline, offline,
      { bot_id: 'b2', bot_uuid: 'b2:owner', is_online: true, recommendation: { score: 0.8 } },
      { bot_id: 'human_1', actor_kind: 'human', is_online: true },
      { name: '没有 Bot ID' },
    ]));
    const result = await collaborationCandidateService.recommendCandidates({
      actorId: 'actor:owner', actorKind: 'bot', keyword: '分析', candidates: [
        candidateView({ id: 'b1:owner', name: 'Stale Name', online: true, isFriend: true, avatarUrl: '/stale.svg' }),
        candidateView({ id: 'b2:owner', name: 'Unreachable', online: false, reachability: 'unreachable' }),
      ],
    });
    expect(result).toMatchObject({ ok: true, data: [
      { id: 'b1:owner', name: 'Catalog Name', online: false, botStatus: 'ACTIVE',
        avatarUrl: '/current.svg', visibility: 'protected', isFriend: false },
      { id: 'b2:owner', online: false, reachability: 'unreachable' },
    ] });
  });

  it('recommendCandidates returns empty without discovery when keyword is blank', async () => {
    const res = await collaborationCandidateService.recommendCandidates({
      actorId: 'actor-1',
      actorKind: 'bot',
      keyword: '   ',
      candidates: [candidateView({ id: 'b1:actor-1', name: 'Alpha' })],
    });

    expect(publicBots.discoverPublicBots).not.toHaveBeenCalled();
    expect(res).toEqual({ ok: true, data: [] });
  });

  it('recommendCandidates maps the human viewer and retains discover-only results', async () => {
    publicBots.discoverPublicBots.mockResolvedValue(
      discoveryResponse([{ bot_uuid: 'b9:actor-9', recommendation: { score: 0.9 } }]),
    );

    const res = await collaborationCandidateService.recommendCandidates({
      actorId: 'human_1',
      actorKind: 'user',
      keyword: '数据分析',
      candidates: [candidateView({ id: 'b1:actor-1', name: 'Alpha' })],
    });

    expect(publicBots.discoverPublicBots).toHaveBeenCalledWith(
      expect.objectContaining({ viewer_actor_type: 'human', viewer_actor_id: 'human_1' }),
      undefined,
    );
    expect(res).toMatchObject({ ok: true, data: [{ id: 'b9:actor-9', recommendScore: 0.9 }] });
  });

  it('recommendCandidates returns domain error when discovery fails', async () => {
    publicBots.discoverPublicBots.mockRejectedValue(new Error('network down'));

    const res = await collaborationCandidateService.recommendCandidates({
      actorId: 'actor-1',
      actorKind: 'bot',
      keyword: '值班',
      candidates: [candidateView({ id: 'b1:actor-1', name: 'Alpha' })],
    });

    expect(res.ok).toBe(false);
    expect(!res.ok && res.error.friendlyMessage).toBe('推荐可协作 Bot 失败，请稍后重试。');
  });
});

it('matches friend metadata by stable BCN address after Owner handoff', async () => {
  connections.listFriendConnections.mockResolvedValue({
    code: 20000, data: { items: [{ actor: { type: 'bot', id: 'b1:old' } }], total: 1 },
  });
  bots.listBotMetadata.mockResolvedValue({
    code: 200000,
    data: { items: [{ bot_id: 'b1', owner_id: 'new', bcn_bot_id: 'b1:old', bot_name: 'Transferred', status: 'ACTIVE' }], total: 1 },
  });
  const result = await collaborationCandidateService.listFriends('new', { actorType: 'human' });
  expect(result).toMatchObject({ ok: true, data: { items: [{ id: 'b1:old', name: 'Transferred' }] } });
});
