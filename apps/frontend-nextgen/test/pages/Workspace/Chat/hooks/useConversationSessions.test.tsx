/** @jest-environment jsdom */
import type { ConversationBotView } from '@/domain/conversation';
import { useConversationSessions } from '@/pages/Workspace/Chat/hooks/useConversationSessions';
import { botSessionService } from '@/services/workspace/botSessionService';
import { conversationService } from '@/services/workspace/conversationService';
import { useConversationStore } from '@/stores/conversationStore';
import { act, renderHook, waitFor } from '@testing-library/react';
import { toast } from 'sonner';

jest.mock('@/services/workspace/conversationService', () => ({
  conversationService: {
    listManagedSessions: require('jest-mock').fn(),
    listFriendBotSessions: require('jest-mock').fn(),
  },
}));

jest.mock('@/services/workspace/botSessionService', () => ({
  botSessionService: { createSession: require('jest-mock').fn(), deleteSession: require('jest-mock').fn() },
  splitBotId: (botId: string) => {
    const idx = botId.indexOf(':');
    return idx < 0
      ? { realBotId: botId, ownerId: undefined }
      : { realBotId: botId.slice(0, idx), ownerId: botId.slice(idx + 1) };
  },
  BOT_SESSION_PAGE_SIZE: 10,
}));

jest.mock('sonner', () => ({ toast: { success: require('jest-mock').fn(), error: require('jest-mock').fn() } }));

const listManagedSessions = conversationService.listManagedSessions as unknown as jest.Mock;
const listFriendBotSessions = conversationService.listFriendBotSessions as unknown as jest.Mock;
const createSession = botSessionService.createSession as unknown as jest.Mock;
const toastError = toast.error as unknown as jest.Mock;

const managedBot: ConversationBotView = {
  bot: {
    botId: 'bot-a:2088',
    realBotId: 'bot-a',
    ownerId: '2088',
    displayName: '管理 Bot',
    online: true,
    chatable: true,
  },
  section: 'managed',
};
const friendBot: ConversationBotView = {
  bot: {
    botId: 'fb-1:327325',
    realBotId: 'fb-1',
    ownerId: '327325',
    displayName: '好友 Bot',
    online: true,
    chatable: true,
    isFriendBot: true,
  },
  section: 'friend',
};
const sessionOf = (sessionId: string) => ({
  sessionId,
  botId: 'bot-a:2088',
  title: '会话',
  messageCount: 0,
  gmtModified: '',
  gmtCreate: '',
});
const okPage = (sessionIds: string[], total = sessionIds.length) => ({
  ok: true as const,
  data: { items: sessionIds.map(sessionOf), total },
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

function renderSessions(options?: Partial<Parameters<typeof useConversationSessions>[0]>) {
  return renderHook((props) => useConversationSessions(props), {
    initialProps: {
      userId: '327325',
      managedBots: [managedBot],
      friendBots: [friendBot],
      ...options,
    },
  });
}

beforeEach(() => {
  // require('jest-mock').fn() 的 mock 不归全局 clearAllMocks 管,逐个 reset。
  listManagedSessions.mockReset();
  listFriendBotSessions.mockReset();
  createSession.mockReset();
  toastError.mockReset();
  // 未在用例内单独 stub 时给兜底实现,避免空 mock 返回 undefined。
  listManagedSessions.mockResolvedValue(okPage([]));
  listFriendBotSessions.mockResolvedValue(okPage([]));
  createSession.mockResolvedValue({ ok: true, data: { ...sessionOf('s-new'), title: '新会话' } });
  useConversationStore.getState().reset();
});

describe('useConversationSessions', () => {
  it('loads sessions only when a Bot expands', async () => {
    listManagedSessions.mockResolvedValue(okPage(['s1', 's2']));
    const { result } = renderSessions();

    expect(listManagedSessions).not.toHaveBeenCalled();

    act(() => result.current.toggleBot('bot-a:2088', 'managed'));

    await waitFor(() => expect(listManagedSessions).toHaveBeenCalledWith(managedBot.bot, '327325', 'all', 1));
    const view = await waitFor(() => {
      const current = useConversationStore.getState().sessionsByBotId['bot-a:2088'];
      expect(current.sessions.items).toHaveLength(2);
      return current;
    });
    expect(view.origin).toBe('mine');
    expect(view.scope).toBe('all');
    expect(result.current.openBotIds['bot-a:2088']).toBe(true);
  });

  it('keeps the loaded list when a Bot collapses and re-expands', async () => {
    listManagedSessions.mockResolvedValue(okPage(['s1']));
    const { result } = renderSessions();

    act(() => result.current.toggleBot('bot-a:2088', 'managed'));
    await waitFor(() => expect(listManagedSessions).toHaveBeenCalledTimes(1));

    act(() => result.current.toggleBot('bot-a:2088', 'managed'));
    act(() => result.current.toggleBot('bot-a:2088', 'managed'));
    await act(async () => {
      await Promise.resolve();
    });

    expect(listManagedSessions).toHaveBeenCalledTimes(1);
    expect(
      useConversationStore.getState().sessionsByBotId['bot-a:2088'].sessions.items.map((item) => item.sessionId),
    ).toEqual(['s1']);
  });

  it('reloads with the remembered scope and drops the stale in-flight list', async () => {
    const all = deferred<Awaited<ReturnType<typeof listManagedSessions>>>();
    listManagedSessions.mockReturnValueOnce(all.promise).mockResolvedValue(okPage(['fav-1'], 1));
    const { result } = renderSessions();

    act(() => result.current.toggleBot('bot-a:2088', 'managed'));
    act(() => useConversationStore.getState().setManagedBotScope('bot-a:2088', 'favorite'));

    await waitFor(() => expect(listManagedSessions).toHaveBeenCalledWith(managedBot.bot, '327325', 'favorite', 1));
    const favoriteView = await waitFor(() => {
      const current = useConversationStore.getState().sessionsByBotId['bot-a:2088'];
      expect(current.sessions.items[0]?.sessionId).toBe('fav-1');
      return current;
    });
    expect(favoriteView.scope).toBe('favorite');

    await act(async () => {
      all.resolve(okPage(['all-1']));
      await all.promise;
    });
    expect(useConversationStore.getState().sessionsByBotId['bot-a:2088'].sessions.items[0]?.sessionId).toBe('fav-1');
  });

  it('does not load managed sessions while the origin is others', async () => {
    listManagedSessions.mockResolvedValue(okPage(['s1']));
    act(() => useConversationStore.getState().setManagedBotOrigin('bot-a:2088', 'others'));
    const { result } = renderSessions();

    act(() => result.current.toggleBot('bot-a:2088', 'managed'));
    await act(async () => {
      await Promise.resolve();
    });

    expect(listManagedSessions).not.toHaveBeenCalled();
  });

  it('loads friend Bot sessions through the friend path', async () => {
    listFriendBotSessions.mockResolvedValue(okPage(['fs1']));
    const { result } = renderSessions();

    act(() => result.current.toggleBot('fb-1:327325', 'friend'));

    await waitFor(() => expect(listFriendBotSessions).toHaveBeenCalledWith(friendBot.bot, '327325', 1));
    await waitFor(() => {
      expect(useConversationStore.getState().friendBotSessionsByBotId['fb-1:327325'].items).toHaveLength(1);
    });
  });

  it('selectMineSession expands the Bot and writes the managed selection', async () => {
    listManagedSessions.mockResolvedValue(okPage(['s1']));
    const { result } = renderSessions();

    act(() => useConversationStore.getState().setExpandedBot('fb-1:327325', true));
    act(() => result.current.selectMineSession('bot-a:2088', 's1'));

    const store = useConversationStore.getState();
    expect(store.expandedBotIds).toEqual({ 'bot-a:2088': true });
    expect(store.selectedBotId).toBe('bot-a:2088');
    expect(store.selectedSection).toBe('managed');
    expect(store.selectedOrigin).toBe('mine');
    expect(store.selectedScope).toBe('all');
    expect(store.selectedSessionId).toBe('s1');
    await waitFor(() => expect(listManagedSessions).toHaveBeenCalledTimes(1));
  });

  it('selectFriendBotSession expands the Bot and writes the friend selection', () => {
    const { result } = renderSessions();

    act(() => {
      useConversationStore.getState().setExpandedBot('bot-a:2088', true);
      result.current.selectFriendBotSession('fb-1:327325', 'fs1');
    });

    const store = useConversationStore.getState();
    expect(store.expandedBotIds).toEqual({ 'fb-1:327325': true });
    expect(store.selectedBotId).toBe('fb-1:327325');
    expect(store.selectedSection).toBe('friend');
    expect(store.selectedSessionId).toBe('fs1');
    expect(listManagedSessions).not.toHaveBeenCalled();
  });

  it('createSession delegates to botSessionService, prepends and selects', async () => {
    listManagedSessions.mockResolvedValue(okPage(['s1']));
    createSession.mockResolvedValue({ ok: true, data: { ...sessionOf('s-new'), title: '新会话' } });
    const { result } = renderSessions();
    act(() => result.current.toggleBot('bot-a:2088', 'managed'));
    await waitFor(() => expect(useConversationStore.getState().sessionsByBotId['bot-a:2088']).toBeDefined());

    act(() => useConversationStore.getState().setExpandedBot('fb-1:327325', true));
    await act(async () => result.current.createSession('bot-a:2088'));
    expect(useConversationStore.getState().expandedBotIds).toEqual({ 'bot-a:2088': true });

    expect(createSession).toHaveBeenCalledWith(managedBot.bot, '327325');
    const view = useConversationStore.getState().sessionsByBotId['bot-a:2088'];
    expect(view.sessions.items[0]?.sessionId).toBe('s-new');
    expect(useConversationStore.getState().selectedSessionId).toBe('s-new');
    expect(toast.success).toHaveBeenCalledWith('会话已创建');
  });

  it('surfaces createSession failures without touching the store selection', async () => {
    createSession.mockResolvedValue({
      ok: false,
      error: { code: 'X', friendlyMessage: '创建会话失败', canRetry: true },
    });
    const { result } = renderSessions();

    await act(async () => result.current.createSession('bot-a:2088'));

    expect(toastError).toHaveBeenCalledWith('创建会话失败');
    expect(useConversationStore.getState().selectedSessionId).toBeNull();
  });

  it('loadMoreSessions appends the next managed page and stops when exhausted', async () => {
    listManagedSessions.mockResolvedValueOnce(okPage(['s1'], 25)).mockResolvedValueOnce(okPage(['s2'], 25));
    const { result } = renderSessions();
    act(() => result.current.toggleBot('bot-a:2088', 'managed'));
    await waitFor(() =>
      expect(useConversationStore.getState().sessionsByBotId['bot-a:2088'].sessions.items).toHaveLength(1),
    );

    await act(async () => result.current.loadMoreSessions('bot-a:2088', 'managed', 'all'));

    expect(listManagedSessions).toHaveBeenLastCalledWith(managedBot.bot, '327325', 'all', 2);
    let view = useConversationStore.getState().sessionsByBotId['bot-a:2088'];
    expect(view.sessions.items.map((item) => item.sessionId)).toEqual(['s1', 's2']);
    expect(view.sessions.page).toBe(2);
    expect(view.sessions.hasMore).toBe(true);

    listManagedSessions.mockResolvedValueOnce(okPage(['s3'], 25));
    await act(async () => result.current.loadMoreSessions('bot-a:2088', 'managed', 'all'));
    expect(listManagedSessions).toHaveBeenLastCalledWith(managedBot.bot, '327325', 'all', 3);
    view = useConversationStore.getState().sessionsByBotId['bot-a:2088'];
    expect(view.sessions.items.map((item) => item.sessionId)).toEqual(['s1', 's2', 's3']);

    listManagedSessions.mockResolvedValueOnce(okPage(['s4'], 30));
    await act(async () => result.current.loadMoreSessions('bot-a:2088', 'managed', 'all'));
    expect(listManagedSessions).toHaveBeenLastCalledWith(managedBot.bot, '327325', 'all', 3);
  });

  it('loadMoreSessions paginates friend Bot sessions', async () => {
    listFriendBotSessions.mockResolvedValueOnce(okPage(['fs1'], 11)).mockResolvedValueOnce(okPage(['fs2'], 11));
    const { result } = renderSessions();
    act(() => result.current.toggleBot('fb-1:327325', 'friend'));
    await waitFor(() => expect(useConversationStore.getState().friendBotSessionsByBotId['fb-1:327325']).toBeDefined());

    await act(async () => result.current.loadMoreSessions('fb-1:327325', 'friend', 'all'));

    expect(listFriendBotSessions).toHaveBeenLastCalledWith(friendBot.bot, '327325', 2);
    const state = useConversationStore.getState().friendBotSessionsByBotId['fb-1:327325'];
    expect(state.items.map((item) => item.sessionId)).toEqual(['fs1', 'fs2']);
    expect(state.hasMore).toBe(false);
  });
});

it('normalizes remembered and selected TEClaw favorite scope before loading sessions', async () => {
  const teclaw = { ...managedBot, bot: { ...managedBot.bot, engine: ' TEClaw ' } };
  const s = useConversationStore.getState();
  s.setManagedBotScope(teclaw.bot.botId, 'favorite');
  s.setExpandedBot(teclaw.bot.botId, true);
  s.selectConversation({
    botId: teclaw.bot.botId,
    section: 'managed',
    origin: 'mine',
    scope: 'favorite',
    friendUserId: null,
    sessionId: 's1',
  });
  const { result } = renderSessions({ managedBots: [teclaw] });
  await waitFor(() => expect(listManagedSessions).toHaveBeenCalledWith(teclaw.bot, '327325', 'all', 1));
  expect(listManagedSessions.mock.calls.every((call) => call[2] === 'all')).toBe(true);
  expect(useConversationStore.getState().scopeByManagedBotId[teclaw.bot.botId]).toBe('all');
  expect(useConversationStore.getState().selectedScope).toBe('all');
  expect(useConversationStore.getState().selectedSessionId).toBe('s1');
  // 后续浏览器导航再次带入旧 favorite 范围时也应修正。
  act(() => useConversationStore.getState().setManagedBotScope(teclaw.bot.botId, 'favorite'));
  await waitFor(() => expect(useConversationStore.getState().selectedScope).toBe('all'));
  expect(result.current.favorites).toBeDefined();
});

it('retains a favorite update when an older pagination response appends', async () => {
  const nextPage = deferred<any>();
  listManagedSessions.mockResolvedValueOnce(okPage(['s1'], 20)).mockReturnValueOnce(nextPage.promise);
  const { result } = renderSessions();
  act(() => result.current.toggleBot(managedBot.bot.botId, 'managed'));
  await waitFor(() =>
    expect(useConversationStore.getState().sessionsByBotId[managedBot.bot.botId].sessions.items).toHaveLength(1),
  );
  await act(async () => {
    await result.current.loadMoreSessions(managedBot.bot.botId, 'managed', 'all');
  });
  act(() => {
    const s = useConversationStore.getState();
    const cache = s.sessionsByBotId[managedBot.bot.botId];
    s.setManagedBotCache(managedBot.bot.botId, {
      ...cache,
      sessions: { ...cache.sessions, items: [{ ...cache.sessions.items[0], favorite: true }] },
    });
  });
  await act(async () => {
    nextPage.resolve(okPage(['s2'], 20));
    await nextPage.promise;
  });
  expect(useConversationStore.getState().sessionsByBotId[managedBot.bot.botId].sessions.items[0].favorite).toBe(true);
});

it('refills a shifted favorite page after unfavoriting without skipping or duplicating sessions', async () => {
  const items = Array.from({ length: 9 }, (_, i) => ({ ...sessionOf(`s${i + 1}`), favorite: true }));
  const s = useConversationStore.getState();
  s.setManagedBotScope(managedBot.bot.botId, 'favorite');
  s.setManagedBotCache(managedBot.bot.botId, {
    botId: managedBot.bot.botId,
    origin: 'mine',
    scope: 'favorite',
    sessions: {
      items,
      page: 1,
      total: 19,
      hasMore: true,
      loading: false,
      error: null,
      isLoadingMore: false,
      loadMoreError: null,
    },
    friendDirectory: { items: [], loading: false, error: null },
    friendGroups: {},
  });
  listManagedSessions.mockResolvedValue(okPage([...items.map((item) => item.sessionId), 's10'], 19));
  const { result } = renderSessions();
  await act(async () => {
    await result.current.loadMoreSessions(managedBot.bot.botId, 'managed', 'favorite');
  });
  expect(listManagedSessions).toHaveBeenCalledWith(managedBot.bot, '327325', 'favorite', 1);
  const list = useConversationStore.getState().sessionsByBotId[managedBot.bot.botId].sessions;
  expect(list.items.map((item) => item.sessionId)).toEqual([...items.map((item) => item.sessionId), 's10']);
  expect(list.items[0].favorite).toBe(true);
});

it('exposes session actions and blocks pagination during deletion, then refills the shifted page', async () => {
  const ids = Array.from({ length: 10 }, (_, i) => `s${i + 1}`);
  listManagedSessions
    .mockResolvedValueOnce(okPage(ids, 21))
    .mockResolvedValueOnce(okPage([...ids.slice(1), 's11'], 20));
  const deletion = deferred<{ ok: true; data: null }>();
  (botSessionService.deleteSession as jest.Mock).mockReturnValue(deletion.promise);
  const { result } = renderSessions();
  act(() => result.current.toggleBot(managedBot.bot.botId, 'managed'));
  await waitFor(() =>
    expect(useConversationStore.getState().sessionsByBotId[managedBot.bot.botId]?.sessions.items).toHaveLength(10),
  );
  let pending!: Promise<boolean>;
  act(() => {
    pending = result.current.actions.run(managedBot.bot.botId, 'managed', 's1', { type: 'delete' });
  });
  await act(async () => result.current.loadMoreSessions(managedBot.bot.botId, 'managed', 'all'));
  expect(listManagedSessions).toHaveBeenCalledTimes(1);
  await act(async () => {
    deletion.resolve({ ok: true, data: null });
    await pending;
  });
  await act(async () => result.current.loadMoreSessions(managedBot.bot.botId, 'managed', 'all'));
  expect(listManagedSessions).toHaveBeenLastCalledWith(managedBot.bot, '327325', 'all', 1);
  const list = useConversationStore.getState().sessionsByBotId[managedBot.bot.botId].sessions;
  expect(list.items.map((s) => s.sessionId)).toEqual([...ids.slice(1), 's11']);
  expect(list.total).toBe(20);
});

it('resumes a deferred scope load when the pending session action finishes', async () => {
  listManagedSessions.mockResolvedValueOnce(okPage(['s1'], 1)).mockResolvedValueOnce(okPage(['s2'], 1));
  const deletion = deferred<{ ok: true; data: null }>();
  (botSessionService.deleteSession as jest.Mock).mockReturnValue(deletion.promise);
  const { result } = renderSessions();
  act(() => result.current.toggleBot(managedBot.bot.botId, 'managed'));
  await waitFor(() =>
    expect(useConversationStore.getState().sessionsByBotId[managedBot.bot.botId]?.sessions.items).toHaveLength(1),
  );
  let pending!: Promise<boolean>;
  act(() => {
    pending = result.current.actions.run(managedBot.bot.botId, 'managed', 's1', { type: 'delete' });
  });
  act(() => useConversationStore.getState().setManagedBotScope(managedBot.bot.botId, 'favorite'));
  expect(listManagedSessions).toHaveBeenCalledTimes(1);
  await act(async () => {
    deletion.resolve({ ok: true, data: null });
    await pending;
  });
  await waitFor(() => expect(listManagedSessions).toHaveBeenLastCalledWith(managedBot.bot, '327325', 'favorite', 1));
});

it('expands managed and friend Bots exclusively, selects their first session and reuses cached sessions', async () => {
  listManagedSessions.mockResolvedValue(okPage(['s1']));
  listFriendBotSessions.mockResolvedValue(okPage(['fs1']));
  const { result } = renderSessions();
  act(() => result.current.selectMineSession(managedBot.bot.botId, 's1'));
  await waitFor(() =>
    expect(useConversationStore.getState().sessionsByBotId[managedBot.bot.botId]?.sessions.items).toHaveLength(1),
  );
  act(() => result.current.toggleBot(friendBot.bot.botId, 'friend'));
  await waitFor(() =>
    expect(useConversationStore.getState().friendBotSessionsByBotId[friendBot.bot.botId]?.items).toHaveLength(1),
  );
  expect(result.current.openBotIds).toEqual({ [friendBot.bot.botId]: true });
  expect(useConversationStore.getState()).toMatchObject({
    selectedBotId: friendBot.bot.botId,
    selectedSessionId: 'fs1',
  });
  act(() => result.current.toggleBot(managedBot.bot.botId, 'managed'));
  expect(result.current.openBotIds).toEqual({ [managedBot.bot.botId]: true });
  expect(listManagedSessions).toHaveBeenCalledTimes(1);
  expect(listFriendBotSessions).toHaveBeenCalledTimes(1);
  act(() => result.current.toggleBot(managedBot.bot.botId, 'managed'));
  expect(result.current.openBotIds).toEqual({});
});

it('a late response only caches the collapsed Bot without re-expanding it', async () => {
  const request = deferred<ReturnType<typeof okPage>>();
  listManagedSessions.mockReturnValue(request.promise);
  const { result } = renderSessions();
  act(() => result.current.toggleBot(managedBot.bot.botId, 'managed'));
  act(() => result.current.toggleBot(friendBot.bot.botId, 'friend'));
  await act(async () => {
    request.resolve(okPage(['s1']));
    await request.promise;
  });
  expect(result.current.openBotIds).toEqual({ [friendBot.bot.botId]: true });
  expect(useConversationStore.getState().sessionsByBotId[managedBot.bot.botId].sessions.items).toHaveLength(1);
});

describe('团队 Bot 复用管理会话', () => {
  const teamBot: ConversationBotView = { ...managedBot, section: 'team' };
  it('展开走管理列表、自动首选、切换 mine 选中保留 team；创建/动作使用 team 缓存', async () => {
    listManagedSessions.mockResolvedValue(okPage(['first']));
    const { result } = renderSessions({ managedBots: [teamBot] });
    act(() => result.current.toggleBot(teamBot.bot.botId, 'team'));
    await waitFor(() => expect(useConversationStore.getState().selectedSessionId).toBe('first'));
    expect(listManagedSessions).toHaveBeenCalledWith(teamBot.bot, '327325', 'all', 1);
    expect(listFriendBotSessions).not.toHaveBeenCalled();
    expect(useConversationStore.getState().selectedSection).toBe('team');
    act(() => result.current.selectMineSession(teamBot.bot.botId, 'first'));
    expect(useConversationStore.getState().selectedSection).toBe('team');
    createSession.mockResolvedValue({ ok: true, data: sessionOf('new') });
    await act(async () => {
      await result.current.createSession(teamBot.bot.botId);
    });
    expect(useConversationStore.getState()).toMatchObject({ selectedSection: 'team', selectedSessionId: 'new' });
    expect(useConversationStore.getState().sessionsByBotId[teamBot.bot.botId].sessions.items[0].sessionId).toBe('new');
  });
  it('他人来源不加载 mine，不允许创建', async () => {
    useConversationStore.getState().setManagedBotOrigin(teamBot.bot.botId, 'others');
    const { result } = renderSessions({ managedBots: [teamBot] });
    act(() => result.current.toggleBot(teamBot.bot.botId, 'team'));
    await act(async () => {
      await result.current.createSession(teamBot.bot.botId);
    });
    expect(listManagedSessions).not.toHaveBeenCalled();
    expect(listFriendBotSessions).not.toHaveBeenCalled();
    expect(createSession).not.toHaveBeenCalled();
    expect(useConversationStore.getState().selectedOrigin).toBe('others');
  });
});

it('团队缺 entity 的不可聊天 Bot 即使被深链展开也不查询/创建会话', async () => {
  const invalid: ConversationBotView = {
    ...managedBot,
    section: 'team',
    bot: { ...managedBot.bot, ownerId: undefined, chatable: false },
  };
  useConversationStore.getState().setExpandedBot(invalid.bot.botId, true);
  const { result } = renderSessions({ managedBots: [invalid] });
  await act(async () => {
    await result.current.createSession(invalid.bot.botId);
  });
  expect(listManagedSessions).not.toHaveBeenCalled();
  expect(createSession).not.toHaveBeenCalled();
});
