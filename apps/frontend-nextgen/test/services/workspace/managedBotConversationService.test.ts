import type { ChatBotView } from '@/services/workspace/botSessionService';
import {
  managedBotConversationService,
  mapConversationFriendUser,
} from '@/services/workspace/managedBotConversationService';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';

jest.mock('@/services/backendApi/bots/privateBotSessionController');
jest.mock('@/services/backendApi/collaboration/collaborationFriendConnectionController');

import * as ctrl from '@/services/backendApi/bots/privateBotSessionController';
import { listFriendConnections } from '@/services/backendApi/collaboration/collaborationFriendConnectionController';

const mocked = ctrl as unknown as Record<string, jest.Mock<any>>;
const relationsMocked = listFriendConnections as unknown as jest.Mock<any>;

beforeEach(() => {
  jest.clearAllMocks();
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
