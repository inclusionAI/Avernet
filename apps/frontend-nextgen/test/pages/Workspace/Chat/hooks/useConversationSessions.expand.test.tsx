/** @jest-environment jsdom */
// 展开/切台解耦（2026-11-02 反馈治理）：展开 Bot 行只控制列表显隐与懒加载，
// 不再自动抢占主舞台（旧「自动首选第一条会话」行为已随 begin/complete 移除）；
// 主舞台仅在用户点具体会话 / 深链 / 新建会话时切换。
import type { ConversationBotView } from '@/domain/conversation';
import { firstPageOf, managedViewOf } from '@/pages/Workspace/Chat/hooks/conversationSessionCache';
import { useConversationSessions } from '@/pages/Workspace/Chat/hooks/useConversationSessions';
import { botSessionService } from '@/services/workspace/botSessionService';
import { conversationService } from '@/services/workspace/conversationService';
import { useConversationStore as store } from '@/stores/conversationStore';
import { act, renderHook, waitFor } from '@testing-library/react';

jest.mock('@/services/workspace/conversationService', () => ({
  conversationService: { listManagedSessions: jest.fn(), listFriendBotSessions: jest.fn() },
}));
jest.mock('@/services/workspace/botSessionService', () => ({
  BOT_SESSION_PAGE_SIZE: 10,
  botSessionService: { createSession: jest.fn() },
}));
const a: ConversationBotView = {
  section: 'managed',
  bot: { botId: 'a', realBotId: 'a', displayName: 'A', chatable: true, online: true },
};
const b: ConversationBotView = { section: 'managed', bot: { ...a.bot, botId: 'b', realBotId: 'b', displayName: 'B' } };
const f: ConversationBotView = {
  section: 'friend',
  bot: { ...a.bot, botId: 'f', realBotId: 'f', displayName: 'F', isFriendBot: true },
};
const options = { userId: 'user' as string | null, managedBots: [a, b], friendBots: [f] };
const session = (id: string, botId = 'a') => ({
  sessionId: id,
  botId,
  title: id,
  messageCount: 1,
  gmtCreate: '',
  gmtModified: '',
});
const page = (ids: string[], botId = 'a') => ({
  ok: true as const,
  data: { items: ids.map((id) => session(id, botId)), total: ids.length },
});
function deferred() {
  let resolve!: (data: ReturnType<typeof page>) => void;
  const promise = new Promise<ReturnType<typeof page>>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}
function seed(id: string, ids: string[]) {
  store.getState().setManagedBotCache(
    id,
    managedViewOf(
      id,
      'all',
      undefined,
      firstPageOf(
        ids.map((sid) => session(sid, id)),
        ids.length,
      ),
    ),
  );
}
const renderSessions = () => renderHook((props) => useConversationSessions(props), { initialProps: options });
beforeEach(() => {
  jest.clearAllMocks();
  store.getState().reset();
  jest.mocked(conversationService.listManagedSessions).mockResolvedValue(page([]));
  jest.mocked(conversationService.listFriendBotSessions).mockResolvedValue(page([], 'f'));
  seed('a', ['a1', 'a2']);
  store.getState().selectConversation({
    botId: 'a',
    section: 'managed',
    origin: 'mine',
    scope: 'all',
    friendUserId: null,
    sessionId: 'a2',
  });
  store.getState().setExpandedBot('a', true);
});
it('expanding another Bot keeps the current session as the main chat, accordion still applies', async () => {
  const request = deferred();
  jest.mocked(conversationService.listManagedSessions).mockReturnValue(request.promise);
  const { result } = renderSessions();
  act(() => result.current.toggleBot('b', 'managed'));
  // 展开即时：手风琴收起旧列表，主舞台选中保持不变（旧实现会立即清空选中）。
  expect(store.getState()).toMatchObject({
    expandedBotIds: { b: true },
    selectedBotId: 'a',
    selectedSection: 'managed',
    selectedSessionId: 'a2',
  });
  await act(async () => request.resolve(page(['b-first', 'b-second'], 'b')));
  // 列表加载完仅供浏览：不再自动首选第一条会话。
  expect(store.getState()).toMatchObject({ selectedBotId: 'a', selectedSessionId: 'a2' });
  expect(store.getState().sessionsByBotId.b.sessions.items.map((item) => item.sessionId)).toEqual([
    'b-first',
    'b-second',
  ]);
});
it('empty list keeps the current chat instead of clearing the stage', async () => {
  jest.mocked(conversationService.listManagedSessions).mockResolvedValue(page([], 'b'));
  const { result } = renderSessions();
  act(() => result.current.toggleBot('b', 'managed'));
  await waitFor(() => expect(store.getState().sessionsByBotId.b.sessions.loading).toBe(false));
  expect(store.getState()).toMatchObject({
    expandedBotIds: { b: true },
    selectedBotId: 'a',
    selectedSessionId: 'a2',
  });
});
it('friend Bot expansion only browses the list; the managed session stays on stage', async () => {
  jest.mocked(conversationService.listFriendBotSessions).mockResolvedValue(page(['f1', 'f2'], 'f'));
  const { result } = renderSessions();
  act(() => result.current.toggleBot('f', 'friend'));
  await waitFor(() => expect(store.getState().friendBotSessionsByBotId.f.items).toHaveLength(2));
  expect(store.getState()).toMatchObject({
    expandedBotIds: { f: true },
    selectedBotId: 'a',
    selectedSection: 'managed',
    selectedSessionId: 'a2',
  });
});
it('collapsing the expanded Bot keeps the selection and the cached list', () => {
  seed('b', ['b1']);
  const { result } = renderSessions();
  act(() => result.current.toggleBot('b', 'managed'));
  act(() => result.current.toggleBot('b', 'managed'));
  expect(store.getState()).toMatchObject({ expandedBotIds: {}, selectedBotId: 'a', selectedSessionId: 'a2' });
  expect(store.getState().sessionsByBotId.b.sessions.items[0].sessionId).toBe('b1');
});
it('readonly Bot expansion keeps the selection and never requests the mine list', () => {
  store.getState().setManagedBotOrigin('b', 'others');
  const { result } = renderSessions();
  act(() => result.current.toggleBot('b', 'managed'));
  expect(conversationService.listManagedSessions).not.toHaveBeenCalled();
  expect(store.getState()).toMatchObject({
    expandedBotIds: { b: true },
    selectedBotId: 'a',
    selectedOrigin: 'mine',
    selectedSessionId: 'a2',
  });
  expect(store.getState().originByManagedBotId.b).toBe('others');
});
it('failed list can be retried for browsing without touching the selection', async () => {
  jest
    .mocked(conversationService.listManagedSessions)
    .mockResolvedValueOnce({ ok: false, error: { code: 'FAIL', friendlyMessage: '列表失败', canRetry: true } })
    .mockResolvedValueOnce(page(['b1'], 'b'));
  const { result } = renderSessions();
  act(() => result.current.toggleBot('b', 'managed'));
  await waitFor(() => expect(store.getState().sessionsByBotId.b.sessions.error).toBe('列表失败'));
  expect(store.getState()).toMatchObject({ selectedBotId: 'a', selectedSessionId: 'a2' });
  act(() => result.current.retrySessions('b', 'managed'));
  await waitFor(() => expect(store.getState().sessionsByBotId.b.sessions.items[0].sessionId).toBe('b1'));
  expect(store.getState()).toMatchObject({ selectedBotId: 'a', selectedSessionId: 'a2' });
});
it('reloads a cached empty partial page instead of leaving a phantom empty list', async () => {
  store.getState().setManagedBotCache('b', managedViewOf('b', 'all', undefined, firstPageOf([], 12)));
  jest.mocked(conversationService.listManagedSessions).mockResolvedValue(page(['b1'], 'b'));
  const { result } = renderSessions();
  act(() => result.current.toggleBot('b', 'managed'));
  await waitFor(() => expect(store.getState().sessionsByBotId.b.sessions.items[0].sessionId).toBe('b1'));
  expect(store.getState()).toMatchObject({ selectedBotId: 'a', selectedSessionId: 'a2' });
});
it('a late response only caches the previously expanded Bot without touching the current chat', async () => {
  const request = deferred();
  jest.mocked(conversationService.listManagedSessions).mockReturnValue(request.promise);
  jest.mocked(conversationService.listFriendBotSessions).mockResolvedValue(page(['f1'], 'f'));
  const { result } = renderSessions();
  act(() => result.current.toggleBot('b', 'managed'));
  act(() => result.current.toggleBot('f', 'friend'));
  await waitFor(() => expect(store.getState().friendBotSessionsByBotId.f.items).toHaveLength(1));
  await act(async () => request.resolve(page(['b1'], 'b')));
  expect(store.getState()).toMatchObject({
    expandedBotIds: { f: true },
    selectedBotId: 'a',
    selectedSessionId: 'a2',
  });
  expect(store.getState().sessionsByBotId.b.sessions.items[0].sessionId).toBe('b1');
});
it('a delayed creation still becomes the main chat when the user has not selected elsewhere', async () => {
  seed('b', []);
  let resolve!: (value: Awaited<ReturnType<typeof botSessionService.createSession>>) => void;
  jest.mocked(botSessionService.createSession).mockReturnValue(
    new Promise((r) => {
      resolve = r;
    }),
  );
  const { result } = renderSessions();
  act(() => result.current.toggleBot('b', 'managed'));
  let creation!: Promise<void>;
  act(() => {
    creation = result.current.createSession('b');
  });
  await act(async () => {
    resolve({ ok: true, data: session('b-new', 'b') });
    await creation;
  });
  expect(store.getState()).toMatchObject({ selectedBotId: 'b', selectedSessionId: 'b-new', expandedBotIds: { b: true } });
});
it('a creation made while the user selected another session must not steal the stage', async () => {
  seed('b', []);
  let resolve!: (value: Awaited<ReturnType<typeof botSessionService.createSession>>) => void;
  jest.mocked(botSessionService.createSession).mockReturnValue(
    new Promise((r) => {
      resolve = r;
    }),
  );
  const { result } = renderSessions();
  act(() => result.current.toggleBot('b', 'managed'));
  let creation!: Promise<void>;
  act(() => {
    creation = result.current.createSession('b');
  });
  act(() => result.current.selectMineSession('a', 'a1'));
  await act(async () => {
    resolve({ ok: true, data: session('b-new', 'b') });
    await creation;
  });
  expect(store.getState().selectedSessionId).toBe('a1');
  expect(store.getState().sessionsByBotId.b.sessions.items[0].sessionId).toBe('b-new');
});
it('a creation response cannot mutate the workspace after unmount', async () => {
  seed('b', []);
  let resolve!: (value: Awaited<ReturnType<typeof botSessionService.createSession>>) => void;
  jest.mocked(botSessionService.createSession).mockReturnValue(
    new Promise((r) => {
      resolve = r;
    }),
  );
  const { result, unmount } = renderSessions();
  let creation!: Promise<void>;
  act(() => {
    creation = result.current.createSession('b');
  });
  unmount();
  const state = store.getState();
  await act(async () => {
    resolve({ ok: true, data: session('b-new', 'b') });
    await creation;
  });
  expect(store.getState()).toBe(state);
});
it('user change cancels a pending list request', async () => {
  const request = deferred();
  jest.mocked(conversationService.listManagedSessions).mockReturnValue(request.promise);
  const { result, rerender } = renderSessions();
  act(() => result.current.toggleBot('b', 'managed'));
  act(() => store.getState().reset());
  rerender({ ...options, userId: 'another-user' });
  await act(async () => request.resolve(page(['b1'], 'b')));
  expect(store.getState().sessionsByBotId.b).toBeUndefined();
  expect(store.getState().selectedSessionId).toBeNull();
});
