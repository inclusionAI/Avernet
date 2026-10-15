// 会话 sessionKey 搜索的服务层查询契约:mock 实际 controller,校验 session_key 原始串、
// owner/f_user_id 身份链路、空条件省略与收藏接口不带搜索参数。
// 设计依据:specs/2026-10-14-conversation-session-key-search/spec.md §数据流与实现职责。
import type { ChatBotView } from '@/services/workspace/botSessionService';
import { botSessionService } from '@/services/workspace/botSessionService';
import { conversationService } from '@/services/workspace/conversationService';
import { managedBotConversationService } from '@/services/workspace/managedBotConversationService';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';

jest.mock('@/services/backendApi/bots/privateBotSessionController');

import * as ctrl from '@/services/backendApi/bots/privateBotSessionController';

const mocked = ctrl as unknown as Record<string, jest.Mock<any>>;

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

/** 普通会话 hydrate 收藏元数据时也会访问收藏分页,统一给空成功应答。 */
beforeEach(() => {
  jest.clearAllMocks();
  mocked.listFavoriteSessions.mockResolvedValue(sessionsEnvelope([]));
});

describe('botSessionService.listSessionsPage with session key', () => {
  it('appends the raw session_key to the ordinary session query', async () => {
    mocked.listBotSessions.mockResolvedValue(sessionsEnvelope([sessionItem]));

    const result = await botSessionService.listSessionsPage(managedBot, 'human_900003', 1, 10, 'agent:main:sn-1');

    expect(mocked.listBotSessions).toHaveBeenCalledWith(
      'bot-a',
      expect.objectContaining({
        user_id: '900003',
        owner_id: '2088',
        page: 1,
        page_size: 10,
        session_key: 'agent:main:sn-1',
      }),
    );
    // 原始字符串直接透传:冒号分隔符保留,不做 Base64/编码。
    expect(mocked.listBotSessions.mock.calls[0][1].session_key).toBe('agent:main:sn-1');
    expect(result.ok).toBe(true);
  });

  it('omits session_key entirely when the search is empty', async () => {
    mocked.listBotSessions.mockResolvedValue(sessionsEnvelope([]));

    await botSessionService.listSessionsPage(managedBot, 'human_900003', 2, 20, '');

    expect(mocked.listBotSessions).toHaveBeenCalledTimes(1);
    expect(mocked.listBotSessions.mock.calls[0][1]).not.toHaveProperty('session_key');
  });

  it('keeps friend f_user_id and team owner_id checks alongside session_key', async () => {
    mocked.listBotSessions.mockResolvedValue(sessionsEnvelope([]));

    await botSessionService.listSessionsPage({ ...friendBot, isFriendBot: true }, 'human_900003', 1, 10, 'k-1');
    await botSessionService.listSessionsPage({ ...managedBot, isTeamBot: true }, 'human_900003', 1, 10, 'k-2');

    const friendParams = mocked.listBotSessions.mock.calls[0][1];
    expect(friendParams).toMatchObject({ user_id: '900003', owner_id: '327325', f_user_id: '900003', session_key: 'k-1' });

    const teamParams = mocked.listBotSessions.mock.calls[1][1];
    expect(teamParams).toMatchObject({ user_id: '900003', owner_id: '2088', session_key: 'k-2' });
    expect(teamParams).not.toHaveProperty('f_user_id');
  });
});

describe('conversationService.listManagedSessions with session key', () => {
  it('passes the raw session_key through for scope=all and still hydrates favorites', async () => {
    mocked.listBotSessions.mockResolvedValue(sessionsEnvelope([sessionItem]));

    const result = await conversationService.listManagedSessions(managedBot, 'human_900003', 'all', 2, 20, 'agent:main:x');

    expect(mocked.listBotSessions).toHaveBeenCalledWith(
      'bot-a',
      expect.objectContaining({ user_id: '900003', owner_id: '2088', page: 2, page_size: 20, session_key: 'agent:main:x' }),
    );
    // 普通列表继续补全收藏状态。
    expect(mocked.listFavoriteSessions).toHaveBeenCalled();
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.data.items[0]).toMatchObject({ sessionId: 'session-1', botId: 'bot-a:2088', title: '历史会话' });
  });

  it('never sends session_key to the favorites endpoint', async () => {
    mocked.listFavoriteSessions.mockResolvedValue(sessionsEnvelope([sessionItem]));

    const result = await conversationService.listManagedSessions(managedBot, 'human_900003', 'favorite', 1, 10, 'agent:main:x');

    expect(mocked.listBotSessions).not.toHaveBeenCalled();
    expect(mocked.listFavoriteSessions).toHaveBeenCalledWith(
      'bot-a',
      expect.objectContaining({ user_id: '900003', owner_id: '2088', page: 1, page_size: 10 }),
    );
    expect(mocked.listFavoriteSessions.mock.calls[0][1]).not.toHaveProperty('session_key');
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.data.items[0]).toMatchObject({ sessionId: 'session-1', favorite: true });
  });
});

describe('conversationService.listFriendBotSessions with scope and session key', () => {
  it('sends the friend identity chain with the raw session_key under scope=all', async () => {
    mocked.listBotSessions.mockResolvedValue(sessionsEnvelope([sessionItem]));

    const result = await conversationService.listFriendBotSessions(friendBot, 'human_900003', 1, 10, 'all', 'agent:main:f');

    expect(mocked.listBotSessions).toHaveBeenCalledWith(
      'bot-b',
      expect.objectContaining({
        user_id: '900003',
        owner_id: '327325',
        f_user_id: '900003',
        page: 1,
        page_size: 10,
        session_key: 'agent:main:f',
      }),
    );
    // 好友普通列表继续 hydrate 收藏元数据,收藏分页也走好友身份。
    expect(mocked.listFavoriteSessions).toHaveBeenCalledWith(
      'bot-b',
      expect.objectContaining({ user_id: '900003', owner_id: '327325', f_user_id: '900003' }),
    );
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.data.items[0]).toMatchObject({ sessionId: 'session-1', botId: 'bot-b:327325' });
  });

  it('routes favorite scope to the favorites endpoint with the friend identity and without session_key', async () => {
    mocked.listFavoriteSessions.mockResolvedValue(sessionsEnvelope([sessionItem]));

    const result = await conversationService.listFriendBotSessions(
      friendBot,
      'human_900003',
      1,
      10,
      'favorite',
      'agent:main:f',
    );

    expect(mocked.listBotSessions).not.toHaveBeenCalled();
    expect(mocked.listFavoriteSessions).toHaveBeenCalledWith(
      'bot-b',
      expect.objectContaining({ user_id: '900003', owner_id: '327325', f_user_id: '900003', page: 1, page_size: 10 }),
    );
    expect(mocked.listFavoriteSessions.mock.calls[0][1]).not.toHaveProperty('session_key');
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.data.items[0]).toMatchObject({ sessionId: 'session-1', favorite: true, botId: 'bot-b:327325' });
  });

  it('normalizes a TEClaw favorite scope back to ordinary friend sessions carrying the search', async () => {
    mocked.listBotSessions.mockResolvedValue(sessionsEnvelope([sessionItem]));

    const result = await conversationService.listFriendBotSessions(
      { ...friendBot, engine: 'TEClaw' },
      'human_900003',
      1,
      10,
      'favorite',
      'agent:main:f',
    );

    expect(mocked.listFavoriteSessions).not.toHaveBeenCalled();
    expect(mocked.listBotSessions).toHaveBeenCalledWith(
      'bot-b',
      expect.objectContaining({ f_user_id: '900003', session_key: 'agent:main:f' }),
    );
    expect(result.ok).toBe(true);
  });
});

describe('managedBotConversationService.listOtherSessions with session key', () => {
  it('queries others sessions with owner identity, friend user and the raw key', async () => {
    mocked.listBotSessions.mockResolvedValue(sessionsEnvelope([sessionItem]));

    const result = await managedBotConversationService.listOtherSessions(managedBot, '447147', 1, 10, 'agent:main:o');

    expect(mocked.listBotSessions).toHaveBeenCalledWith(
      'bot-a',
      expect.objectContaining({
        user_id: '2088',
        owner_id: '2088',
        f_user_id: '447147',
        page: 1,
        page_size: 10,
        session_key: 'agent:main:o',
      }),
    );
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.data.items[0]).toMatchObject({ sessionId: 'session-1', botId: 'bot-a:2088' });
  });

  it('omits session_key for an empty others search while keeping the identity chain', async () => {
    mocked.listBotSessions.mockResolvedValue(sessionsEnvelope([]));

    await managedBotConversationService.listOtherSessions(managedBot, '447147', 2, 10, '');

    expect(mocked.listBotSessions.mock.calls[0][1]).not.toHaveProperty('session_key');
    expect(mocked.listBotSessions.mock.calls[0][1]).toMatchObject({
      user_id: '2088',
      owner_id: '2088',
      f_user_id: '447147',
      page: 2,
      page_size: 10,
    });
  });

  it('still rejects team Bots before any session query when a key is supplied', async () => {
    const result = await managedBotConversationService.listOtherSessions(
      { ...managedBot, isTeamBot: true },
      '447147',
      1,
      10,
      'agent:main:o',
    );

    expect(mocked.listBotSessions).not.toHaveBeenCalled();
    expect(result.ok).toBe(false);
  });
});
