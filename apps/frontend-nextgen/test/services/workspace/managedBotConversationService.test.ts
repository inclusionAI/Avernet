import type { ChatBotView } from '@/services/workspace/botSessionService';
import {
  managedBotConversationService,
  mapConversationFriendUser,
} from '@/services/workspace/managedBotConversationService';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';

jest.mock('@/services/backendApi/bots/privateBotSessionController');
jest.mock('@/services/backendApi/collaboration/collaborationBotController');
jest.mock('@/services/backendApi/collaboration/collaborationFriendConnectionController');

import * as ctrl from '@/services/backendApi/bots/privateBotSessionController';
import { queryCollaborationBots } from '@/services/backendApi/collaboration/collaborationBotController';
import { listFriendConnections } from '@/services/backendApi/collaboration/collaborationFriendConnectionController';

const mocked = ctrl as unknown as Record<string, jest.Mock<any>>;
const relationsMocked = listFriendConnections as unknown as jest.Mock<any>;

beforeEach(() => {
  jest.clearAllMocks();
  jest.mocked(queryCollaborationBots).mockReset();
  jest.mocked(queryCollaborationBots).mockResolvedValue({ code: 200000, data: { items: [] } });
});

const managedBot: ChatBotView = {
  botId: 'bot-a:2088',
  realBotId: 'bot-a',
  ownerId: '2088',
  displayName: 'Bot A',
  online: true,
  chatable: true,
};

const relationsEnvelope = (ids: string[], total = ids.length) => ({
  code: 20000,
  message: 'OK',
  data: {
    items: ids.map((id) => ({ actor: { type: 'human', id } })),
    total,
    page: 1,
    page_size: 100,
  },
  request_id: 'relations',
});

describe('mapConversationFriendUser', () => {
  it('normalizes human_ prefixes and falls back to the user id when details are missing', () => {
    expect(mapConversationFriendUser({ actor: { type: 'human', id: 'human_447147' }, name: '风太' })).toEqual({
      userId: '447147',
      displayName: '风太',
    });
    expect(mapConversationFriendUser({ actor: { type: 'human', id: '447147' } })).toEqual({
      userId: '447147',
      displayName: '447147',
    });
    expect(mapConversationFriendUser({ actor: { type: 'human', id: '  ' } })).toBeNull();
    expect(mapConversationFriendUser({ actor: { type: 'bot', id: 'bot-b:100' } })).toBeNull();
  });
});

describe('managedBotConversationService.loadFriendUsers', () => {
  it('loads managed Bot friend users as bot actor', async () => {
    relationsMocked.mockResolvedValue(relationsEnvelope(['human_447147']));

    await managedBotConversationService.loadFriendUsers({
      botId: 'bot-a:2088',
      realBotId: 'bot-a',
      ownerId: '2088',
      displayName: 'Bot A',
      online: true,
      chatable: true,
    });
    expect(relationsMocked).toHaveBeenCalledWith(
      expect.objectContaining({
        actor_type: 'bot',
        actor_id: 'bot-a:2088',
        target_type: 'human',
      }),
      expect.anything(),
    );
  });

  it('normalizes Human ids, keeps names and dedupes across pages', async () => {
    relationsMocked.mockImplementation(async (params: { page?: number }) =>
      params.page === 1 ? relationsEnvelope(['human_447147'], 2) : relationsEnvelope(['447147', 'human_448148'], 2),
    );

    const result = await managedBotConversationService.loadFriendUsers(managedBot);

    expect(relationsMocked).toHaveBeenCalledWith(
      expect.objectContaining({ page: 2, page_size: 100 }),
      expect.anything(),
    );
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.data).toEqual([
      { userId: '447147', displayName: '447147' },
      { userId: '448148', displayName: '448148' },
    ]);
  });

  it('keeps names returned with the relation and passes the abort signal through', async () => {
    relationsMocked.mockResolvedValue({
      code: 20000,
      message: 'OK',
      data: {
        items: [{ actor: { type: 'human', id: 'human_447147' }, name: ' 风太 ' }],
        total: 1,
      },
      request_id: 'relations',
    });
    const controller = new AbortController();
    controller.abort();

    const result = await managedBotConversationService.loadFriendUsers(managedBot, controller.signal);

    expect(relationsMocked).toHaveBeenCalledWith(expect.anything(), controller.signal);
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.data).toEqual([{ userId: '447147', displayName: '风太' }]);
  });

  it('returns a user-facing error when relations fail', async () => {
    relationsMocked.mockRejectedValue(new Error('boom'));

    const result = await managedBotConversationService.loadFriendUsers(managedBot);

    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(result.error.friendlyMessage.length).toBeGreaterThan(0);
    expect(queryCollaborationBots).not.toHaveBeenCalled();
  });
});

describe('managedBotConversationService Human display names', () => {
  it.each([false, true])(
    'resolves managed/team names (isTeamBot=%s) once after pagination and deduplication',
    async (isTeamBot) => {
      relationsMocked.mockImplementation(async (params: { page?: number }) =>
        params.page === 1 ? relationsEnvelope(['human_447147'], 2) : relationsEnvelope(['447147', 'human_448148'], 2),
      );
      jest.mocked(queryCollaborationBots).mockResolvedValue({
        code: 200000,
        data: {
          items: [
            { bot_id: 'human_448148', kind: 'human', name: '雨太' },
            { bot_id: 'human_unrelated', kind: 'human', name: '其他人' },
            { bot_id: 'human_447147', kind: 'human', name: ' 风太 ' },
          ],
        },
      });
      const controller = new AbortController();
      const result = await managedBotConversationService.loadFriendUsers(
        { ...managedBot, isTeamBot },
        controller.signal,
      );

      expect(result).toEqual({
        ok: true,
        data: [
          { userId: '447147', displayName: '风太' },
          { userId: '448148', displayName: '雨太' },
        ],
      });
      expect(jest.mocked(queryCollaborationBots)).toHaveBeenCalledTimes(1);
      expect(queryCollaborationBots).toHaveBeenCalledWith(
        { bot_ids: ['human_447147', 'human_448148'] },
        controller.signal,
      );
    },
  );

  it('falls back to user IDs for missing or blank names and ignores Bot details', async () => {
    relationsMocked.mockResolvedValue(relationsEnvelope(['447147', '448148', '449149', '450150']));
    jest.mocked(queryCollaborationBots).mockResolvedValue({
      code: 200000,
      data: {
        items: [
          { bot_id: 'human_447147', kind: 'human', name: '风太' },
          { bot_id: 'human_448148', kind: 'human', name: '  ' },
          { bot_id: 'human_449149', kind: 'bot', name: '非用户详情' },
        ],
      },
    });

    expect(await managedBotConversationService.loadFriendUsers(managedBot)).toEqual({
      ok: true,
      data: [
        { userId: '447147', displayName: '风太' },
        { userId: '448148', displayName: '448148' },
        { userId: '449149', displayName: '449149' },
        { userId: '450150', displayName: '450150' },
      ],
    });
  });

  it('prefers resolved names over relationship names', async () => {
    relationsMocked.mockResolvedValue({
      data: { items: [{ actor: { type: 'human', id: 'human_447147' }, name: '旧名称' }], total: 1 },
    });
    jest.mocked(queryCollaborationBots).mockResolvedValue({
      code: 200000,
      data: { items: [{ bot_id: 'human_447147', kind: 'human', name: '风太' }] },
    });
    expect(await managedBotConversationService.loadFriendUsers(managedBot)).toEqual({
      ok: true,
      data: [{ userId: '447147', displayName: '风太' }],
    });
  });

  it('keeps the directory usable when name lookup fails', async () => {
    relationsMocked.mockResolvedValue({
      data: {
        items: [
          { actor: { type: 'human', id: 'human_447147' }, name: '已有名称' },
          { actor: { type: 'human', id: 'human_448148' } },
        ],
        total: 2,
      },
    });
    jest.mocked(queryCollaborationBots).mockRejectedValue(new Error('name lookup unavailable'));
    expect(await managedBotConversationService.loadFriendUsers(managedBot)).toEqual({
      ok: true,
      data: [
        { userId: '447147', displayName: '已有名称' },
        { userId: '448148', displayName: '448148' },
      ],
    });
    expect(queryCollaborationBots).toHaveBeenCalledTimes(1);
  });

  it('falls back when the lookup response has no details', async () => {
    relationsMocked.mockResolvedValue(relationsEnvelope(['447147']));
    jest.mocked(queryCollaborationBots).mockResolvedValue({ code: 200000 });
    expect(await managedBotConversationService.loadFriendUsers(managedBot)).toEqual({
      ok: true,
      data: [{ userId: '447147', displayName: '447147' }],
    });
    expect(queryCollaborationBots).toHaveBeenCalledTimes(1);
  });

  it('does not query names for an empty directory', async () => {
    relationsMocked.mockResolvedValue(relationsEnvelope([]));
    expect(await managedBotConversationService.loadFriendUsers(managedBot)).toEqual({ ok: true, data: [] });
    expect(queryCollaborationBots).not.toHaveBeenCalled();
  });
});

describe('managedBotConversationService.listOtherSessions', () => {
  it('uses managed Bot owner for others Session query', async () => {
    mocked.listBotSessions.mockResolvedValue({
      code: 20000,
      message: 'OK',
      data: {
        items: [
          {
            session_id: 'session-1',
            title: '历史会话_session-1',
            agent_id: 'bot-a',
            model: '',
            message_count: 3,
            gmt_create: '2026-09-15T10:00:00Z',
            gmt_modified: '2026-09-16T10:00:00Z',
          },
        ],
        total: 1,
      },
      request_id: 'sessions',
    });

    const result = await managedBotConversationService.listOtherSessions(managedBot, '447147', 1, 10);

    expect(mocked.listBotSessions).toHaveBeenCalledWith(
      'bot-a',
      expect.objectContaining({
        user_id: '2088',
        owner_id: '2088',
        f_user_id: '447147',
        page: 1,
        page_size: 10,
      }),
    );
    expect(result).toEqual({
      ok: true,
      data: {
        items: [
          expect.objectContaining({
            sessionId: 'session-1',
            botId: 'bot-a:2088',
            title: '历史会话',
          }),
        ],
        page: 1,
        total: 1,
        hasMore: false,
        loading: false,
        error: null,
        isLoadingMore: false,
        loadMoreError: null,
      },
    });
  });

  it('computes hasMore from total and normalizes human_ friend ids', async () => {
    mocked.listBotSessions.mockResolvedValue({
      code: 20000,
      message: 'OK',
      data: {
        items: [
          {
            session_id: 'session-1',
            title: 'a',
            agent_id: '',
            model: '',
            message_count: 1,
            gmt_create: '',
            gmt_modified: '',
          },
          {
            session_id: 'session-2',
            title: 'b',
            agent_id: '',
            model: '',
            message_count: 1,
            gmt_create: '',
            gmt_modified: '',
          },
        ],
        total: 5,
      },
      request_id: 'sessions',
    });

    const result = await managedBotConversationService.listOtherSessions(managedBot, 'human_447147', 1, 2);

    expect(mocked.listBotSessions).toHaveBeenCalledWith(
      'bot-a',
      expect.objectContaining({ f_user_id: '447147', page: 1, page_size: 2 }),
    );
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.data.total).toBe(5);
    expect(result.data.hasMore).toBe(true);
  });

  it('rejects managed Bots without an owner instead of sending an empty owner query', async () => {
    const result = await managedBotConversationService.listOtherSessions(
      { ...managedBot, botId: 'bot-a', realBotId: 'bot-a', ownerId: undefined },
      '447147',
    );

    expect(mocked.listBotSessions).not.toHaveBeenCalled();
    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(result.error.friendlyMessage.length).toBeGreaterThan(0);
  });

  it('maps load failures into user-facing DomainResult errors', async () => {
    mocked.listBotSessions.mockRejectedValue(new Error('boom'));

    const result = await managedBotConversationService.listOtherSessions(managedBot, '447147');

    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(result.error.friendlyMessage.length).toBeGreaterThan(0);
  });
});

describe('managedBotConversationService.listOtherMessages', () => {
  it('queries the managed Bot owner for others message history and keeps raw counts', async () => {
    mocked.listBotSessionMessages.mockResolvedValue({
      code: 20000,
      message: 'OK',
      data: {
        items: [
          {
            message_id: 'm2',
            session_id: 's1',
            role: 'assistant',
            content: '回复',
            gmt_create: '2026-09-16T10:01:00Z',
          },
          {
            message_id: 'm1',
            session_id: 's1',
            role: 'user',
            content: '你好',
            gmt_create: '2026-09-16T10:00:00Z',
          },
        ],
        total: 5,
      },
      request_id: 'messages',
    });

    const result = await managedBotConversationService.listOtherMessages(managedBot, '447147', 's1', 2, 2);

    expect(mocked.listBotSessionMessages).toHaveBeenCalledWith(
      'bot-a',
      's1',
      expect.objectContaining({
        user_id: '2088',
        owner_id: '2088',
        f_user_id: '447147',
        page: 2,
        page_size: 2,
      }),
    );
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.data.messages.map((item) => item.id)).toEqual(['m1', 'm2']);
    expect(result.data.page).toBe(2);
    expect(result.data.total).toBe(5);
    expect(result.data.rawCount).toBe(2);
    expect(result.data.hasMore).toBe(true);
  });

  it('rejects invalid context (missing owner or empty sessionId) without calling the controller', async () => {
    const noOwner = await managedBotConversationService.listOtherMessages(
      { ...managedBot, botId: 'bot-a', realBotId: 'bot-a', ownerId: undefined },
      '447147',
      's1',
    );
    const emptySession = await managedBotConversationService.listOtherMessages(managedBot, '447147', '  ');

    expect(mocked.listBotSessionMessages).not.toHaveBeenCalled();
    expect(noOwner.ok).toBe(false);
    expect(emptySession.ok).toBe(false);
  });

  it('is read-only: the service exposes no create/update/delete/favorite methods', () => {
    expect(Object.keys(managedBotConversationService).sort()).toEqual([
      'listOtherMessages',
      'listOtherSessions',
      'loadFriendUsers',
    ]);
  });
});

it.each(['service', 'personal'])(
  'owned %s other-user session reads share the conversation runtime',
  async (botType) => {
    mocked.listBotSessions.mockResolvedValue({ code: 200000, data: { items: [], total: 0 } });
    mocked.listBotSessionMessages.mockResolvedValue({ code: 200000, data: { items: [], total: 0 } });
    const bot = { ...managedBot, botType, runtimeStage: 'online' as const };
    const stage = botType === 'service' ? 'online' : undefined;
    await managedBotConversationService.listOtherSessions(bot, 'friend');
    await managedBotConversationService.listOtherMessages(bot, 'friend', 'session');
    expect(mocked.listBotSessions).toHaveBeenLastCalledWith(
      'bot-a',
      expect.objectContaining({ f_user_id: 'friend', owner_id: '2088' }),
    );
    expect(mocked.listBotSessionMessages).toHaveBeenLastCalledWith(
      'bot-a',
      'session',
      expect.objectContaining({ f_user_id: 'friend', owner_id: '2088' }),
    );
    expect(mocked.listBotSessions.mock.calls.at(-1)[1].stage).toBe(stage);
    expect(mocked.listBotSessionMessages.mock.calls.at(-1)[2].stage).toBe(stage);
    expect(Object.hasOwn(mocked.listBotSessions.mock.calls.at(-1)[1], 'stage')).toBe(botType === 'service');
    expect(Object.hasOwn(mocked.listBotSessionMessages.mock.calls.at(-1)[2], 'stage')).toBe(botType === 'service');
  },
);
