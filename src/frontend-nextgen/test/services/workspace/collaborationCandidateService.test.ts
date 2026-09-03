/** @jest-environment node */
import * as botsController from '@/services/backendApi/bots/botController';
import * as botController from '@/services/backendApi/collaboration/collaborationBotController';
import * as friendConnectionController from '@/services/backendApi/collaboration/collaborationFriendConnectionController';
import { collaborationCandidateService } from '@/services/workspace/collaborationCandidateService';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';

jest.mock('@/services/backendApi/collaboration/collaborationBotController');
jest.mock('@/services/backendApi/bots/botController');
jest.mock('@/services/backendApi/collaboration/collaborationFriendConnectionController');

const bc = botController as unknown as Record<string, jest.Mock<any>>;
const bots = botsController as unknown as Record<string, jest.Mock<any>>;
const connections = friendConnectionController as unknown as Record<string, jest.Mock<any>>;

beforeEach(() => {
  jest.resetAllMocks();
});

describe('collaborationCandidateService', () => {
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

    const res = await collaborationCandidateService.listFriends('actor-1:327325', { actorType: 'bot' });

    expect(connections.listFriendConnections).toHaveBeenCalledWith({
      actor_type: 'bot',
      actor_id: 'actor-1:327325',
    });
    expect(bc.listBotFriendships).not.toHaveBeenCalled();
    expect(bots.listBotMetadata).toHaveBeenCalledWith(
      { user_id: '327325', page: 1, page_size: 2 },
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
            online: true,
            status: 'online',
            reachability: 'reachable',
            visibility: 'private',
            isFriend: true,
          },
          {
            id: 'b2:actor-1',
            name: 'Beta',
            online: false,
            status: 'online',
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

  it('listFriends sends the Human staff id without the human_ prefix', async () => {
    connections.listFriendConnections.mockResolvedValue({
      code: 20000,
      data: {
        items: [{ actor: { type: 'bot', id: 'b1:327325' } }],
        total: 1,
      },
    });
    bots.listBotMetadata.mockResolvedValue({
      code: 200000,
      data: {
        items: [{ bot_id: 'b1', owner_id: '327325', bot_name: '蒜蓉粉丝虾', status: 'online', engine: 'OpenAI' }],
        total: 1,
      },
    });

    const res = await collaborationCandidateService.listFriends('human_327325', {
      actorType: 'human',
      offset: 0,
      limit: 100,
    });

    expect(connections.listFriendConnections).toHaveBeenCalledWith({
      actor_type: 'human',
      actor_id: '327325',
    });
    expect(bc.listBotFriendships).not.toHaveBeenCalled();
    expect(bots.listBotMetadata).toHaveBeenCalledWith(
      { user_id: '327325', page: 1, page_size: 1 },
      { bots: [{ bot_id: 'b1', owner_id: '327325' }] },
    );
    expect(res.ok && res.data.items[0]).toMatchObject({
      id: 'b1:327325',
      name: '蒜蓉粉丝虾',
      engine: 'OpenAI',
    });
  });

  it('listFriends uses collaboration bot query for collaboration details', async () => {
    connections.listFriendConnections.mockResolvedValue({
      code: 20000,
      data: {
        items: [{ actor: { type: 'bot', id: 'b1:327325' } }, { actor: { type: 'bot', id: 'b2:327325' } }],
        total: 2,
      },
    });
    bc.queryCollaborationBots.mockResolvedValue({
      code: 20000,
      data: {
        items: [
          {
            kind: 'bot',
            bot_id: 'b1:327325',
            name: '协作 Alpha',
            status: 'online',
            reachability: 'reachable',
            visibility: 'public',
          },
        ],
        total: 1,
      },
    });

    const res = await collaborationCandidateService.listFriends('human_327325', {
      actorType: 'human',
      detailSource: 'collaboration',
    });

    expect(bc.queryCollaborationBots).toHaveBeenCalledWith({ bot_ids: ['b1:327325', 'b2:327325'] });
    expect(bots.listBotMetadata).not.toHaveBeenCalled();
    expect(res.ok && res.data.items).toEqual([
      expect.objectContaining({ id: 'b1:327325', name: '协作 Alpha', isFriend: true }),
      expect.objectContaining({
        id: 'b2:327325',
        name: 'b2:327325',
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

    expect(res.ok && res.data.items[0]).toMatchObject({ id: 'b1:actor-1', name: 'Alpha' });
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
});
