/** @jest-environment node */
import * as botsController from '@/services/backendApi/bots/botController';
import * as botController from '@/services/backendApi/collaboration/collaborationBotController';
import { collaborationCandidateService } from '@/services/workspace/collaborationCandidateService';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';

jest.mock('@/services/backendApi/collaboration/collaborationBotController');
jest.mock('@/services/backendApi/bots/botController');

const bc = botController as unknown as Record<string, jest.Mock<any>>;
const bots = botsController as unknown as Record<string, jest.Mock<any>>;

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

  it('listFriends loads friendship relations and enriches bot details', async () => {
    bc.listBotFriendships.mockResolvedValue({
      code: 20000,
      data: {
        items: [
          { bot_uuid: 'actor-1', friend_bot_uuid: 'b1:actor-1', created_at: 1 },
          { bot_uuid: 'actor-1', friend_bot_uuid: 'b2:actor-1', created_at: 2 },
          { bot_uuid: 'actor-1', friend_bot_uuid: 'b1:actor-1', created_at: 3 },
        ],
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

    const res = await collaborationCandidateService.listFriends('actor-1');

    expect(bc.listBotFriendships).toHaveBeenCalledWith('actor-1', { offset: 0, limit: 50 });
    expect(bots.listBotMetadata).toHaveBeenCalledWith(
      { user_id: 'actor-1', page: 1, page_size: 2 },
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

  it('listFriends tolerates friendships without bot details and stays empty', async () => {
    bc.listBotFriendships.mockResolvedValue({
      code: 20000,
      data: {
        items: [{ bot_uuid: 'actor-1', friend_bot_uuid: 'b1:actor-1', created_at: 1 }],
      },
    });
    bots.listBotMetadata.mockResolvedValue({
      code: 200000,
      data: { items: [], total: 0 },
    });

    const res = await collaborationCandidateService.listFriends('actor-1');

    expect(res.ok && res.data.items).toEqual([]);
    expect(res.ok && res.data.hasMore).toBe(false);
  });

  it('maps friendship load failures to friendly messages', async () => {
    bc.listBotFriendships.mockRejectedValue(new Error('network'));
    const res = await collaborationCandidateService.listFriends('actor-1');
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
