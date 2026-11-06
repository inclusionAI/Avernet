/** @jest-environment jsdom */
import { defaultCapabilities, extendCapabilities } from '@/capabilities';
import type { ConversationBotView } from '@/domain/conversation';
import { createSessionRequestContext, loadSessionPage } from '@/pages/Workspace/Chat/hooks/conversationSessionPageRequest';
import { clearConversationFavoriteCache } from '@/services/workspace/conversationFavoriteCache';
import { botSessionService, type BotSessionPageView } from '@/services/workspace/botSessionService';
import { conversationService } from '@/services/workspace/conversationService';
import type { DomainResult } from '@/services/workspace/identityService';
import { useConversationStore } from '@/stores/conversationStore';
import { act, waitFor } from '@testing-library/react';

jest.mock('@/services/workspace/conversationService', () => ({
  conversationService: { listManagedSessions: jest.fn(), listFriendBotSessions: jest.fn() },
}));
jest.mock('@/services/workspace/botSessionService', () => ({
  BOT_SESSION_PAGE_SIZE: 10,
  botSessionService: { listFavoriteSessionsPage: jest.fn() },
}));
const managed = jest.mocked(conversationService.listManagedSessions);
const friends = jest.mocked(conversationService.listFriendBotSessions);
const favorites = jest.mocked(botSessionService.listFavoriteSessionsPage);
const view: ConversationBotView = { section: 'managed', bot: {
  botId: 'b:owner', realBotId: 'b', ownerId: 'owner', displayName: 'Bot',
  online: true, chatable: true, botType: 'service', runtimeStage: 'online',
} };
function page(ids: string[], total = ids.length): BotSessionPageView {
  return { total, items: ids.map((sessionId) => ({ sessionId, botId: view.bot.botId,
    title: sessionId, messageCount: 1, gmtCreate: '', gmtModified: '' })) };
}
function deferred() {
  let resolve!: (result: DomainResult<BotSessionPageView>) => void;
  const promise = new Promise<DomainResult<BotSessionPageView>>((done) => { resolve = done; });
  return { promise, resolve };
}
const current = () => useConversationStore.getState().sessionsByBotId[view.bot.botId]?.sessions;
beforeEach(() => {
  jest.resetAllMocks();
  clearConversationFavoriteCache();
  extendCapabilities(defaultCapabilities);
  useConversationStore.getState().reset();
  managed.mockResolvedValue({ ok: true, data: page(['s1']) });
  friends.mockResolvedValue({ ok: true, data: page(['s1']) });
});

it('收藏慢时普通列表先展示，后台返回后才补齐星标，不改变 total 或分页状态', async () => {
  const response = deferred();
  favorites.mockReturnValue(response.promise);
  loadSessionPage(createSessionRequestContext('viewer'), () => false, view, false);
  await waitFor(() => expect(current()?.loading).toBe(false));
  expect(current()?.items[0].sessionId).toBe('s1');
  expect(current()?.items[0].favorite).toBeUndefined();
  await act(async () => response.resolve({ ok: true, data: page(['s1']) }));
  expect(current()?.items[0].favorite).toBe(true);
  expect(current()).toMatchObject({ total: 1, page: 1, loading: false, error: null });
});

it.each(['user', 'generation', 'filter'] as const)('%s 改变后忽略晚到收藏响应', async (change) => {
  const response = deferred();
  favorites.mockReturnValue(response.promise);
  const context = createSessionRequestContext('viewer');
  loadSessionPage(context, () => false, view, false);
  await waitFor(() => expect(current()?.loading).toBe(false));
  if (change === 'user') context.userId.current = 'another';
  if (change === 'generation') context.generation.current += 1;
  if (change === 'filter') useConversationStore.getState().setManagedBotScope(view.bot.botId, 'favorite');
  await act(async () => response.resolve({ ok: true, data: page(['s1']) }));
  expect(current()?.items[0].favorite).toBeUndefined();
});

it('追加页期间合并收藏请求，补全不覆盖之前页、不改变页码', async () => {
  const response = deferred();
  favorites.mockReturnValue(response.promise);
  managed.mockResolvedValueOnce({ ok: true, data: page(['s1'], 20) })
    .mockResolvedValueOnce({ ok: true, data: page(['s2'], 20) });
  const context = createSessionRequestContext('viewer');
  loadSessionPage(context, () => false, view, false);
  await waitFor(() => expect(current()?.loading).toBe(false));
  loadSessionPage(context, () => false, view, true);
  await waitFor(() => expect(current()?.page).toBe(2));
  expect(favorites).toHaveBeenCalledTimes(1);
  await act(async () => response.resolve({ ok: true, data: page(['s1', 's2']) }));
  expect(current()?.items.map((item) => [item.sessionId, item.favorite])).toEqual([['s1', true], ['s2', true]]);
  expect(current()).toMatchObject({ page: 2, total: 20, isLoadingMore: false });
});

it('收藏失败不变成会话列表错误，未知状态不冒充未收藏', async () => {
  favorites.mockResolvedValue({ ok: false, error: { code: 'FAILED', friendlyMessage: '失败', canRetry: true } });
  loadSessionPage(createSessionRequestContext('viewer'), () => false, view, false);
  await waitFor(() => expect(current()?.loading).toBe(false));
  expect(current()).toMatchObject({ error: null, total: 1 });
  expect(current()?.items[0].favorite).toBeUndefined();
});

it('好友 Bot 列表同样先展示，再补齐当前好友上下文的星标', async () => {
  const response = deferred();
  favorites.mockReturnValue(response.promise);
  const friendView: ConversationBotView = { ...view, section: 'friend', bot: { ...view.bot, isFriendBot: true } };
  loadSessionPage(createSessionRequestContext('viewer'), () => false, friendView, false);
  await waitFor(() => expect(useConversationStore.getState().friendBotSessionsByBotId[view.bot.botId]?.loading).toBe(false));
  await act(async () => response.resolve({ ok: true, data: page(['s1']) }));
  expect(useConversationStore.getState().friendBotSessionsByBotId[view.bot.botId].items[0].favorite).toBe(true);
  expect(favorites).toHaveBeenCalledWith(friendView.bot, 'viewer', 1, 100);
});

it('后台补全不覆盖期间用户已经确认的星标', async () => {
  const response = deferred();
  favorites.mockReturnValue(response.promise);
  loadSessionPage(createSessionRequestContext('viewer'), () => false, view, false);
  await waitFor(() => expect(current()?.loading).toBe(false));
  const store = useConversationStore.getState();
  const cache = store.sessionsByBotId[view.bot.botId];
  store.setManagedBotCache(view.bot.botId, { ...cache, sessions: { ...cache.sessions,
    items: cache.sessions.items.map((item) => ({ ...item, favorite: true })) } });
  await act(async () => response.resolve({ ok: true, data: page([]) }));
  expect(current()?.items[0].favorite).toBe(true);
});
