/** @jest-environment jsdom */
import { backendRequest } from '@/services/backendApi/httpClient';
import {
  botSessionService,
  resolveChatRuntimeStage,
  withFriendBotRequestParams,
  type ChatBotView,
} from '@/services/workspace/botSessionService';

jest.mock('@/services/backendApi/httpClient', () => ({ backendRequest: jest.fn() }));
const request = jest.mocked(backendRequest);
const base: ChatBotView = {
  botId: 'service:entity',
  realBotId: 'service',
  ownerId: 'entity',
  displayName: 'Service',
  online: true,
  chatable: true,
  botType: 'service',
  runtimeStage: 'draft',
};
const cases: [string, Partial<ChatBotView>, 'draft' | 'verify' | 'online'][] = [
  ['team service', { isTeamBot: true }, 'online'],
  ['friend service', { isFriendBot: true }, 'online'],
  ['team personal', { isTeamBot: true, botType: 'personal' }, 'draft'],
  ['friend personal', { isFriendBot: true, botType: 'personal' }, 'draft'],
  ['friend without type', { isFriendBot: true, botType: undefined }, 'draft'],
  ['my service online', { runtimeStage: 'online' }, 'online'],
  ['my personal', { botType: 'personal', runtimeStage: 'online' }, 'draft'],
  ['my service draft', {}, 'draft'],
  ['my service verify', { runtimeStage: 'verify' }, 'verify'],
];

beforeEach(() => {
  request.mockReset();
  request.mockResolvedValue({
    code: 200000,
    data: { items: [], total: 0, session_id: 's1', title: 'Session', message_count: 0 },
  });
});

test.each(cases)('%s resolves the correct runtime', (_name, override, stage) => {
  const bot = { ...base, ...override };
  expect(resolveChatRuntimeStage(bot)).toBe(stage);
  const params = withFriendBotRequestParams(bot, 'human_viewer', { user_id: 'viewer' });
  expect(params.stage).toBe(bot.botType === 'service' ? stage : undefined);
  expect(Object.hasOwn(params, 'stage')).toBe(bot.botType === 'service');
});

test.each(cases)('%s uses the same stage on all conversation requests', async (_name, override, stage) => {
  const bot = { ...base, ...override };
  await botSessionService.listSessionsPage(bot, 'human_viewer');
  await botSessionService.listSessions(bot, 'human_viewer');
  await botSessionService.createSession(bot, 'human_viewer');
  await botSessionService.getSessionDetail(bot, 'human_viewer', 's1');
  await botSessionService.deleteSession(bot, 'human_viewer', 's1');
  await botSessionService.updateSessionTitle(bot, 'human_viewer', 's1', 'Renamed');
  await botSessionService.clearContext(bot, 'human_viewer', 's1');
  await botSessionService.listMessagesPage(bot, 'human_viewer', 's1');
  await botSessionService.listModels(bot, 'human_viewer');
  await botSessionService.updateSessionModel(bot, 'human_viewer', 's1', 'model');
  await botSessionService.listFavoriteSessionsPage(bot, 'human_viewer');
  await botSessionService.toggleFavorite(bot, 'human_viewer', 's1', true);
  await botSessionService.toggleFavorite(bot, 'human_viewer', 's1', false);

  expect(request).toHaveBeenCalledTimes(13);
  for (const [url, options] of request.mock.calls) {
    expect(url).toMatch(/^\/openapi\/v1\/bots\/service\/(sessions|models)/);
    expect(options?.params).toMatchObject({ user_id: 'viewer' });
    expect(options?.params?.stage).toBe(bot.botType === 'service' ? stage : undefined);
    expect(Object.hasOwn(options?.params ?? {}, 'stage')).toBe(bot.botType === 'service');
    const ownerParams = bot.isTeamBot || bot.isFriendBot ? { owner_id: 'entity' } : {};
    expect(options?.params).toMatchObject(ownerParams);
    expect(options?.params?.f_user_id).toBe(bot.isFriendBot ? 'viewer' : undefined);
  }
});

test.each(['isTeamBot', 'isFriendBot'] as const)(
  '%s cannot downgrade service requests through a draft instance range',
  async (role) => {
    const bot = { ...base, [role]: true };
    const range = { deviceUuid: 'device', stage: 'draft' as const };
    await botSessionService.listSessionsPage(bot, 'viewer', 1, 20, '', range);
    await botSessionService.listFavoriteSessionsPage(bot, 'viewer', 1, 20, range);
    for (const [, options] of request.mock.calls) expect(options?.params).toMatchObject({ stage: 'online' });
  },
);

test('my Bot preserves an explicitly selected request stage', () => {
  expect(withFriendBotRequestParams(base, 'viewer', { user_id: 'viewer', stage: 'verify' })).toMatchObject({
    stage: 'verify',
  });
});

test('non-service team Bots omit even stale explicit instance-range stages', () => {
  const bot = { ...base, isTeamBot: true, botType: undefined };
  expect(withFriendBotRequestParams(bot, 'viewer', { user_id: 'viewer', stage: 'verify' })).not.toHaveProperty('stage');
});

test.each(['service', 'personal'])(
  'owned %s catalog selects online and its sessions use the mapped runtime',
  async (botType) => {
    request.mockResolvedValueOnce({
      code: 200000,
      data: {
        items: [
          { bot_id: 'service', owner_entity_id: 'entity', bot_name: 'My Bot', bot_type: botType, status: 'ACTIVE' },
        ],
        total: 1,
      },
    });
    const result = await botSessionService.listOwnedBots('human_viewer');
    expect(request).toHaveBeenCalledWith(
      '/openapi/v1/bots',
      expect.objectContaining({
        method: 'GET',
        params: expect.objectContaining({ user_id: 'viewer', stage: 'online' }),
      }),
    );
    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error(result.error.friendlyMessage);
    const bot = result.data[0];
    const stage = botType === 'service' ? 'online' : 'draft';
    expect(bot).toMatchObject({ botType, runtimeStage: stage });
    await botSessionService.createSession(bot, 'human_viewer');
    await botSessionService.listSessionsPage(bot, 'human_viewer');
    await botSessionService.listFavoriteSessionsPage(bot, 'human_viewer');
    await botSessionService.listModels(bot, 'human_viewer');
    for (const [, options] of request.mock.calls.slice(1)) {
      expect(options?.params?.stage).toBe(botType === 'service' ? 'online' : undefined);
      expect(Object.hasOwn(options?.params ?? {}, 'stage')).toBe(botType === 'service');
    }
  },
);

test('owned online service cannot be downgraded by stale draft instance ranges', async () => {
  const bot = { ...base, runtimeStage: 'online' as const };
  const range = { deviceUuid: 'device', stage: 'draft' as const };
  await botSessionService.listSessionsPage(bot, 'viewer', 1, 20, '', range);
  await botSessionService.listFavoriteSessionsPage(bot, 'viewer', 1, 20, range);
  for (const [, options] of request.mock.calls) expect(options?.params).toMatchObject({ stage: 'online' });
});

test.each(['personal', 'desktop', undefined])(
  'non-service type=%p omits stage even when an instance range supplies draft',
  async (botType) => {
    const bot = { ...base, botType };
    const range = { deviceUuid: 'device', stage: 'draft' as const };
    await botSessionService.listSessionsPage(bot, 'viewer', 1, 20, '', range);
    await botSessionService.listFavoriteSessionsPage(bot, 'viewer', 1, 20, range);
    for (const [, options] of request.mock.calls) expect(options?.params).not.toHaveProperty('stage');
  },
);
