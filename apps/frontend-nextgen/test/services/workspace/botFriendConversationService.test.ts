import { listBotSessionMessages, listBotSessions } from '@/services/backendApi/bots/privateBotSessionController';
import { queryCollaborationBots } from '@/services/backendApi/collaboration/collaborationBotController';
import {
  listFriendConnections,
  type ListFriendConnectionsParams,
} from '@/services/backendApi/collaboration/collaborationFriendConnectionController';
import {
  botFriendConversationService,
  normalizeFriendUserId,
  resolveBotFriendContext,
  toFriendQueryId,
} from '@/services/workspace/botFriendConversationService';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';

jest.mock('@/services/backendApi/collaboration/collaborationBotController', () => ({
  queryCollaborationBots: require('jest-mock').fn(),
}));
jest.mock('@/services/backendApi/collaboration/collaborationFriendConnectionController', () => ({
  listFriendConnections: require('jest-mock').fn(),
}));
jest.mock('@/services/backendApi/bots/privateBotSessionController', () => ({
  listBotSessions: require('jest-mock').fn(),
  listBotSessionMessages: require('jest-mock').fn(),
}));

const mockListFriendConnections = listFriendConnections as jest.MockedFunction<typeof listFriendConnections>;
const mockQueryCollaborationBots = queryCollaborationBots as jest.MockedFunction<typeof queryCollaborationBots>;
const mockListBotSessions = listBotSessions as jest.MockedFunction<typeof listBotSessions>;
const mockListBotSessionMessages = listBotSessionMessages as jest.MockedFunction<typeof listBotSessionMessages>;

const relationEnvelope = (ids: string[], total = ids.length) => ({
  code: 20000,
  message: 'OK',
  data: {
    items: ids.map((id) => ({ actor: { type: id.startsWith('bot-') ? ('bot' as const) : ('human' as const), id } })),
    total,
    page: 1,
    page_size: 100,
  },
  request_id: 'relations',
});

beforeEach(() => {
  jest.clearAllMocks();
  mockListFriendConnections.mockReset();
  mockQueryCollaborationBots.mockReset();
  mockListBotSessions.mockReset();
  mockListBotSessionMessages.mockReset();
});

describe('botFriendConversationService identity normalization', () => {
  it('normalizes Human prefixes and resolves current Bot context', () => {
    expect(normalizeFriendUserId('human_447147')).toBe('447147');
    expect(normalizeFriendUserId('447147')).toBe('447147');
    expect(toFriendQueryId('human', '447147')).toBe('human_447147');
    expect(toFriendQueryId('bot', 'bot-b:100')).toBe('bot-b:100');
    expect(resolveBotFriendContext('bot-a:327325', 'human_447147')).toEqual({
      currentBotIdentityId: 'bot-a:327325',
      currentBotId: 'bot-a',
      ownerUserId: '327325',
      friendUserId: '447147',
    });
    expect(resolveBotFriendContext('bot-without-owner', '447147')).toBeNull();
  });
});

describe('botFriendConversationService directory', () => {
  it('loads both target types, follows pagination and resolves all names in one query', async () => {
    mockListFriendConnections.mockImplementation(async (params: ListFriendConnectionsParams) => {
      if (params.target_type === 'human' && params.page === 1) return relationEnvelope(['447147'], 2);
      if (params.target_type === 'human' && params.page === 2) return relationEnvelope(['448148'], 2);
      return relationEnvelope(['bot-b:100']);
    });
    mockQueryCollaborationBots.mockResolvedValue({
      code: 20000,
      message: 'OK',
      data: {
        items: [
          { bot_id: 'human_447147', kind: 'human', name: '风太', status: 'hidden' },
          { bot_id: 'human_448148', kind: 'human', name: '雨太', status: 'online' },
          { bot_id: 'bot-b:100', kind: 'bot', name: '皮皮虾', status: 'online', reachability: 'reachable' },
        ],
        total: 3,
      },
      request_id: 'details',
    });

    const result = await botFriendConversationService.loadDirectory('bot-a:327325');

    expect(mockListFriendConnections).toHaveBeenCalledWith(
      expect.objectContaining({
        actor_type: 'bot',
        actor_id: 'bot-a:327325',
        target_type: 'human',
        page: 1,
        page_size: 100,
      }),
      expect.any(AbortSignal),
    );
    expect(mockListFriendConnections).toHaveBeenCalledWith(
      expect.objectContaining({ target_type: 'human', page: 2, page_size: 100 }),
      expect.any(AbortSignal),
    );
    expect(mockQueryCollaborationBots).toHaveBeenCalledTimes(1);
    expect(mockQueryCollaborationBots).toHaveBeenCalledWith(
      { bot_ids: ['human_447147', 'human_448148', 'bot-b:100'] },
      expect.any(AbortSignal),
    );
    expect(result.human).toEqual(
      expect.objectContaining({
        ok: true,
        data: expect.objectContaining({
          items: [
            expect.objectContaining({ actorId: '447147', queryId: 'human_447147', displayName: '风太' }),
            expect.objectContaining({ actorId: '448148', queryId: 'human_448148', displayName: '雨太' }),
          ],
        }),
      }),
    );
    expect(result.bot).toEqual(
      expect.objectContaining({
        ok: true,
        data: expect.objectContaining({
          items: [
            expect.objectContaining({
              actorId: 'bot-b:100',
              displayName: '皮皮虾',
              disabledReason: '暂不支持查看 Bot 好友对话',
            }),
          ],
        }),
      }),
    );
  });

  it('keeps relationship IDs usable when details are missing', async () => {
    mockListFriendConnections.mockImplementation(async (params: ListFriendConnectionsParams) =>
      params.target_type === 'human' ? relationEnvelope(['447147']) : relationEnvelope([]),
    );
    mockQueryCollaborationBots.mockResolvedValue({
      code: 20000,
      message: 'OK',
      data: { items: [], total: 0 },
      request_id: 'details',
    });

    const result = await botFriendConversationService.loadDirectory('bot-a:327325');

    expect(result.human).toEqual(
      expect.objectContaining({
        ok: true,
        data: expect.objectContaining({
          items: [expect.objectContaining({ actorId: '447147', displayName: '447147', detailsResolved: false })],
        }),
      }),
    );
  });

  it('returns independent section errors when Human relations fail', async () => {
    mockListFriendConnections.mockImplementation(async (params: ListFriendConnectionsParams) => {
      if (params.target_type === 'human') throw new Error('human failed');
      return relationEnvelope(['bot-b:100']);
    });
    mockQueryCollaborationBots.mockResolvedValue({
      code: 20000,
      message: 'OK',
      data: { items: [{ bot_id: 'bot-b:100', kind: 'bot', name: '皮皮虾', status: 'online' }], total: 1 },
      request_id: 'details',
    });

    const result = await botFriendConversationService.loadDirectory('bot-a:327325');

    expect(result.human).toEqual(expect.objectContaining({ ok: false }));
    expect(result.bot).toEqual(expect.objectContaining({ ok: true }));
  });
});

describe('botFriendConversationService read-only sessions', () => {
  it('addresses the current Bot and sends the selected Human as f_user_id', async () => {
    mockListBotSessions.mockResolvedValue({
      code: 20000,
      message: 'OK',
      data: {
        items: [
          {
            session_id: 'session-1',
            title: '历史会话_session-1',
            agent_id: 'bot-a',
            model: 'model',
            message_count: 3,
            gmt_create: '2026-09-15T10:00:00Z',
            gmt_modified: '2026-09-16T10:00:00Z',
          },
        ],
        total: 1,
      },
      request_id: 'sessions',
    });

    const result = await botFriendConversationService.listSessionsPage('bot-a:327325', 'human_447147', 1, 10);

    expect(mockListBotSessions).toHaveBeenCalledWith('bot-a', {
      user_id: '327325',
      owner_id: '327325',
      f_user_id: '447147',
      page: 1,
      page_size: 10,
    });
    expect(result).toEqual({
      ok: true,
      data: {
        items: [
          expect.objectContaining({
            sessionId: 'session-1',
            friendUserId: '447147',
            title: '历史会话',
          }),
        ],
        total: 1,
        page: 1,
        pageSize: 10,
        hasMore: false,
      },
    });
  });

  it('maps message history and keeps raw pagination counts', async () => {
    mockListBotSessionMessages.mockResolvedValue({
      code: 20000,
      message: 'OK',
      data: {
        items: [
          {
            message_id: 'm1',
            session_id: 'session-1',
            role: 'user',
            content: '你好',
            gmt_create: '2026-09-16T10:00:00Z',
          },
        ],
        total: 3,
      },
      request_id: 'messages',
    });

    const result = await botFriendConversationService.listMessagesPage('bot-a:327325', '447147', 'session-1', 1, 50);

    expect(mockListBotSessionMessages).toHaveBeenCalledWith('bot-a', 'session-1', {
      user_id: '327325',
      owner_id: '327325',
      f_user_id: '447147',
      page: 1,
      page_size: 50,
    });
    expect(result).toEqual({
      ok: true,
      data: expect.objectContaining({
        messages: [expect.objectContaining({ id: 'm1', role: 'user', content: '你好' })],
        total: 3,
        rawCount: 1,
      }),
    });
  });
});
