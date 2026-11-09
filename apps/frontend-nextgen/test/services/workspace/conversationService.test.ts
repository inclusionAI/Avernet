import { defaultCapabilities, extendCapabilities } from '@/capabilities';
import type { ChatBotView } from '@/services/workspace/botSessionService';
import { conversationService } from '@/services/workspace/conversationService';
import { afterEach, beforeEach, describe, expect, it, jest } from '@jest/globals';

jest.mock('@/services/backendApi/bots/botController');
jest.mock('@/services/backendApi/bots/privateBotSessionController');
jest.mock('@/services/backendApi/collaboration/collaborationBotController');
jest.mock('@/services/backendApi/collaboration/collaborationFriendConnectionController');

import * as botCtrl from '@/services/backendApi/bots/botController';
import * as ctrl from '@/services/backendApi/bots/privateBotSessionController';
import { listFriendConnections } from '@/services/backendApi/collaboration/collaborationFriendConnectionController';

const botMocked = botCtrl as unknown as Record<string, jest.Mock<any>>;
const mocked = ctrl as unknown as Record<string, jest.Mock<any>>;
const relationsMocked = listFriendConnections as unknown as jest.Mock<any>;

const managedBot: ChatBotView = {
  botId: 'bot-a:2088',
  realBotId: 'bot-a',
  ownerId: '2088',
  displayName: 'Bot A',
  online: true,
  chatable: true,
};

const friendBot: ChatBotView = {
  botId: 'bot-b:327325',
  realBotId: 'bot-b',
  ownerId: '327325',
  displayName: 'Bot B',
  online: true,
  chatable: true,
};

const sessionItem = {
  session_id: 'session-1',
  title: '历史会话_session-1',
  agent_id: 'bot-a',
  model: '',
  message_count: 3,
  gmt_create: '2026-09-15T10:00:00Z',
  gmt_modified: '2026-09-16T10:00:00Z',
};

const sessionsEnvelope = (items: unknown[], total = items.length) => ({
  code: 20000,
  message: 'OK',
  data: { items, total },
  request_id: 'sessions',
});

beforeEach(() => {
  jest.clearAllMocks();
  extendCapabilities(defaultCapabilities);
  mocked.listFavoriteSessions.mockResolvedValue(sessionsEnvelope([]));
});

afterEach(() => {
  extendCapabilities(defaultCapabilities);
});

describe('conversationService.listDirectory', () => {
  it('returns managed and AgentCoding Bots in the managed section plus friend Bots', async () => {
    botMocked.listBots.mockResolvedValue({
      code: 200000,
      data: {
        items: [
          {
            bot_id: 'bot-a',
            bot_name: 'Bot A',
            engine_type: 'claude_code',
            owner_entity_id: '2088',
            status: 'ACTIVE',
          },
          {
            bot_id: 'application:1',
            bot_name: '应用实例',
            engine_type: 'claude_code',
            template_type: 'applicationCoding',
            owner_entity_id: '1',
            status: 'ACTIVE',
          },
        ],
      },
      message: 'OK',
      request_id: 'r',
    });
    relationsMocked.mockResolvedValue({
      code: 20000,
      message: 'OK',
      data: {
        items: [{ actor: { type: 'bot', id: 'bot-b:327325' }, name: 'Bot B' }],
        total: 1,
      },
      request_id: 'r',
    });
    botMocked.listBotMetadata.mockResolvedValue({
      code: 200000,
      data: {
        items: [
          {
            bot_id: 'bot-b',
            owner_id: '327325',
            bot_name: 'Bot B',
            engine: 'claude_code',
            template_type: 'personalCoding',
            template_name: '个人 Coding Bot',
            status: 'ACTIVE',
          },
        ],
      },
      message: 'OK',
      request_id: 'r',
    });

    const result = await conversationService.listDirectory('human_900003');

    expect(botMocked.listBots).toHaveBeenCalledWith(expect.objectContaining({ user_id: '900003' }));
    expect(relationsMocked).toHaveBeenCalledWith(expect.objectContaining({ actor_type: 'human', actor_id: '900003' }));
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.data.managedBots).toEqual([
      expect.objectContaining({
        botId: 'bot-a:2088',
        realBotId: 'bot-a',
        ownerId: '2088',
        displayName: 'Bot A',
      }),
      expect.objectContaining({
        botId: 'application:1',
        displayName: '应用实例',
        isAgentCodingBot: true,
        templateType: 'applicationCoding',
      }),
    ]);
    expect(result.data.friendBots).toEqual([
      expect.objectContaining({
        botId: 'bot-b:327325',
        realBotId: 'bot-b',
        ownerId: '327325',
        displayName: 'Bot B',
        online: true,
        chatable: true,
        isFriendBot: true,
        isAgentCodingBot: true,
      }),
    ]);
  });

  it.each([
    ['service', false, false],
    ['service', true, true],
    ['service', null, true],
    ['service', undefined, true],
    ['personal', false, true],
    [undefined, false, true],
  ] as const)(
    'maps friend bot_type=%p online_available=%p to chatable=%p',
    async (botType, onlineAvailable, chatable) => {
      botMocked.listBots.mockResolvedValue({ code: 200000, data: { items: [], total: 0 } });
      relationsMocked.mockResolvedValue({
        code: 20000,
        data: { items: [{ actor: { type: 'bot', id: 'friend:327325' } }], total: 1 },
      });
      botMocked.listBotMetadata.mockResolvedValue({
        code: 200000,
        data: {
          items: [
            {
              bot_id: 'friend',
              owner_id: '327325',
              bot_name: '好友 Bot',
              bot_type: botType,
              online_available: onlineAvailable,
              status: 'ACTIVE',
            },
          ],
          total: 1,
        },
      });

      const result = await conversationService.listDirectory('human_900003');

      expect(result).toMatchObject({
        ok: true,
        data: { friendBots: [{ botId: 'friend:327325', botType, onlineAvailable, chatable, isFriendBot: true }] },
      });
    },
  );

  it('keeps the directory usable with empty friend Bots when friend relations fail', async () => {
    botMocked.listBots.mockResolvedValue({
      code: 200000,
      data: {
        items: [
          {
            bot_id: 'bot-a',
            bot_name: 'Bot A',
            engine_type: 'claude_code',
            owner_entity_id: '2088',
            status: 'ACTIVE',
          },
        ],
      },
      message: 'OK',
      request_id: 'r',
    });
    relationsMocked.mockRejectedValue(new Error('friend boom'));

    const result = await conversationService.listDirectory('human_900003');

    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.data.friendBots).toEqual([]);
    expect(result.data.managedBots).toHaveLength(1);
  });

  it('propagates a user-facing error when managed Bots fail', async () => {
    botMocked.listBots.mockRejectedValue(new Error('managed boom'));
    relationsMocked.mockResolvedValue({
      code: 20000,
      message: 'OK',
      data: { items: [], total: 0 },
      request_id: 'r',
    });

    const result = await conversationService.listDirectory('human_900003');

    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(result.error.friendlyMessage.length).toBeGreaterThan(0);
  });
});

describe('conversationService.listManagedSessions', () => {
  it('queries owned sessions with the normalized user id for scope=all', async () => {
    mocked.listBotSessions.mockResolvedValue(sessionsEnvelope([sessionItem]));

    const result = await conversationService.listManagedSessions(managedBot, 'human_900003', 'all', 2, 20);

    expect(mocked.listBotSessions).toHaveBeenCalledWith(
      'bot-a',
      expect.objectContaining({ user_id: '900003', owner_id: '2088', page: 2, page_size: 20 }),
    );
    expect(mocked.listFavoriteSessions).not.toHaveBeenCalled();
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.data.items[0]).toMatchObject({
      sessionId: 'session-1',
      botId: 'bot-a:2088',
      title: '历史会话',
    });
  });

  it('routes scope=favorite to the favorite sessions endpoint', async () => {
    mocked.listFavoriteSessions.mockResolvedValue(sessionsEnvelope([sessionItem]));

    const result = await conversationService.listManagedSessions(managedBot, 'human_900003', 'favorite', 1, 10);

    expect(mocked.listBotSessions).not.toHaveBeenCalled();
    expect(mocked.listFavoriteSessions).toHaveBeenCalledWith(
      'bot-a',
      expect.objectContaining({ user_id: '900003', owner_id: '2088', page: 1, page_size: 10 }),
    );
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.data.items[0]).toMatchObject({ sessionId: 'session-1', favorite: true });
  });

  describe('Coding Bot session ownership', () => {
    const codingBot: ChatBotView = {
      ...managedBot,
      engine: 'claude_code',
      isAgentCodingBot: true,
    };
    const sessionDto = (sessionId: string) => ({
      ...sessionItem,
      session_id: sessionId,
      title: `历史会话_${sessionId}`,
    });

    it('continues scanning raw pages when the first page only contains sessions from another user', async () => {
      const otherSessions = Array.from({ length: 100 }, (_, index) =>
        sessionDto(`user:200:session:other-${index}:agent:bot-a`),
      );
      const mineSession = sessionDto('user:100:session:mine:agent:bot-a');
      mocked.listBotSessions.mockImplementation((_botId: string, params: Parameters<typeof ctrl.listBotSessions>[1]) =>
        Promise.resolve(
          params.page === 1 ? sessionsEnvelope(otherSessions, 101) : sessionsEnvelope([mineSession], 101),
        ),
      );

      const result = await conversationService.listManagedSessions(codingBot, '100', 'all', 1, 10);

      expect(mocked.listBotSessions).toHaveBeenNthCalledWith(
        1,
        'bot-a',
        expect.objectContaining({ page: 1, page_size: 100 }),
      );
      expect(mocked.listBotSessions).toHaveBeenNthCalledWith(
        2,
        'bot-a',
        expect.objectContaining({ page: 2, page_size: 100 }),
      );
      expect(result).toMatchObject({
        ok: true,
        data: { items: [{ sessionId: 'user:100:session:mine:agent:bot-a' }], total: 1 },
      });
    });

    it('returns only sessions created by other users for origin=others', async () => {
      mocked.listBotSessions.mockResolvedValue(
        sessionsEnvelope([
          sessionDto('user:100:session:mine:agent:bot-a'),
          sessionDto('user:200:session:other:agent:bot-a'),
        ]),
      );

      const result = await conversationService.listManagedSessions(
        codingBot,
        '100',
        'all',
        1,
        10,
        '',
        undefined,
        'others',
      );

      expect(result).toMatchObject({
        ok: true,
        data: { items: [{ sessionId: 'user:200:session:other:agent:bot-a' }], total: 1 },
      });
    });

    it('treats numeric user ids with leading zeroes as the same creator', async () => {
      mocked.listBotSessions.mockResolvedValue(sessionsEnvelope([sessionDto('user:000100:session:mine:agent:bot-a')]));

      const result = await conversationService.listManagedSessions(codingBot, '100', 'all');

      expect(result).toMatchObject({
        ok: true,
        data: { items: [{ sessionId: 'user:000100:session:mine:agent:bot-a' }] },
      });
    });

    it('filters internal sessions injected through the Coding Bot visibility policy', async () => {
      extendCapabilities({
        getCodingBotConversationHost: () => ({
          status: 'available',
          value: {
            Stage: () => null,
            isSessionHidden: (session: { title?: unknown }) => session.title === 'cron: MemoryOS任务',
          },
        }),
      });
      const normal = sessionDto('user:100:session:normal:agent:bot-a');
      const hidden = {
        ...sessionDto('user:100:session:hidden:agent:bot-a'),
        title: 'cron: MemoryOS任务_user:100:session:hidden:agent:bot-a',
      };
      mocked.listBotSessions.mockResolvedValue(sessionsEnvelope([hidden, normal], 2));

      const result = await conversationService.listManagedSessions(codingBot, '100', 'all', 1, 10);

      expect(result).toMatchObject({
        ok: true,
        data: { items: [{ sessionId: 'user:100:session:normal:agent:bot-a' }] },
      });
    });

    it('keeps ordinary Bot sessions unaffected by the Coding Bot visibility policy', async () => {
      extendCapabilities({
        getCodingBotConversationHost: () => ({
          status: 'available',
          value: {
            Stage: () => null,
            isSessionHidden: () => true,
          },
        }),
      });
      mocked.listBotSessions.mockResolvedValue(sessionsEnvelope([sessionItem]));

      const result = await conversationService.listManagedSessions(managedBot, 'human_900003', 'all');

      expect(result).toMatchObject({
        ok: true,
        data: { items: [{ sessionId: 'session-1', title: '历史会话' }] },
      });
    });

    it('keeps unrecognized legacy session ids in mine for backward compatibility', async () => {
      mocked.listBotSessions.mockResolvedValue(sessionsEnvelope([sessionDto('legacy-session-id')]));

      const result = await conversationService.listManagedSessions(codingBot, '100', 'all');

      expect(result).toMatchObject({
        ok: true,
        data: { items: [{ sessionId: 'legacy-session-id' }] },
      });
    });
  });
});

describe('conversationService.listFriendBotSessions', () => {
  it('sends the authenticated Human as user_id and f_user_id for Human→Bot sessions', async () => {
    mocked.listBotSessions.mockResolvedValue(sessionsEnvelope([sessionItem]));

    const result = await conversationService.listFriendBotSessions(friendBot, 'human_900003', 1, 10);

    expect(mocked.listBotSessions).toHaveBeenCalledWith(
      'bot-b',
      expect.objectContaining({
        user_id: '900003',
        owner_id: '327325',
        f_user_id: '900003',
        page: 1,
        page_size: 10,
      }),
    );
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.data.items[0]).toMatchObject({
      sessionId: 'session-1',
      botId: 'bot-b:327325',
      title: '历史会话',
    });
  });

  it('maps load failures into user-facing DomainResult errors', async () => {
    mocked.listBotSessions.mockRejectedValue(new Error('boom'));

    const result = await conversationService.listFriendBotSessions(friendBot, 'human_900003');

    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(result.error.friendlyMessage.length).toBeGreaterThan(0);
  });
});

it('normalizes a stale TEClaw favorite scope to ordinary sessions', async () => {
  mocked.listBotSessions.mockResolvedValue(sessionsEnvelope([sessionItem]));
  const result = await conversationService.listManagedSessions(
    { ...managedBot, engine: 'TEClaw' },
    'user',
    'favorite',
    2,
    10,
  );
  expect(result.ok).toBe(true);
  expect(mocked.listBotSessions).toHaveBeenCalledWith('bot-a', expect.objectContaining({ page: 2, page_size: 10 }));
  expect(mocked.listFavoriteSessions).not.toHaveBeenCalled();
});
