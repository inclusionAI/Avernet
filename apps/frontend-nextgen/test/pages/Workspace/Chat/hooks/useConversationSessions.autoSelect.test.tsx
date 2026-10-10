/** @jest-environment jsdom */
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
it('switches to cached Bot and selects its first session, not the previous selection', () => {
  seed('b', ['b-first', 'b-second']);
  const { result } = renderSessions();
  act(() => result.current.toggleBot('b', 'managed'));
  expect(store.getState()).toMatchObject({
    selectedBotId: 'b',
    selectedSection: 'managed',
    selectedSessionId: 'b-first',
    expandedBotIds: { b: true },
  });
  expect(conversationService.listManagedSessions).not.toHaveBeenCalled();
  act(() => result.current.toggleBot('a', 'managed'));
  expect(store.getState().selectedSessionId).toBe('a1');
});
it('clears old conversation immediately, then selects first once the new list loads', async () => {
  const request = deferred();
  jest.mocked(conversationService.listManagedSessions).mockReturnValue(request.promise);
  const { result } = renderSessions();
  act(() => result.current.toggleBot('b', 'managed'));
  expect(store.getState()).toMatchObject({ selectedBotId: 'b', selectedSessionId: null });
  await act(async () => request.resolve(page(['b1', 'b2'], 'b')));
  expect(store.getState().selectedSessionId).toBe('b1');
});
it('friend Bot selection follows its first loaded session', async () => {
  jest.mocked(conversationService.listFriendBotSessions).mockResolvedValue(page(['f1', 'f2'], 'f'));
  const { result } = renderSessions();
  act(() => result.current.toggleBot('f', 'friend'));
  await waitFor(() => expect(store.getState().selectedSessionId).toBe('f1'));
  expect(store.getState().selectedSection).toBe('friend');
});
it('empty list keeps target Bot selected but removes old session', async () => {
  const { result } = renderSessions();
  act(() => result.current.toggleBot('b', 'managed'));
  await waitFor(() => expect(store.getState().sessionsByBotId.b.sessions.loading).toBe(false));
  expect(store.getState()).toMatchObject({ selectedBotId: 'b', selectedSessionId: null });
});
it('late response from previously expanded Bot cannot replace the new selection', async () => {
  const request = deferred();
  jest.mocked(conversationService.listManagedSessions).mockReturnValue(request.promise);
  jest.mocked(conversationService.listFriendBotSessions).mockResolvedValue(page(['f1'], 'f'));
  const { result } = renderSessions();
  act(() => result.current.toggleBot('b', 'managed'));
  act(() => result.current.toggleBot('f', 'friend'));
  await waitFor(() => expect(store.getState().selectedSessionId).toBe('f1'));
  await act(async () => request.resolve(page(['b1'], 'b')));
  expect(store.getState()).toMatchObject({ selectedBotId: 'f', selectedSessionId: 'f1', expandedBotIds: { f: true } });
});
it('a manual or deep-link session chosen while loading is not overwritten by default-first', async () => {
  const request = deferred();
  jest.mocked(conversationService.listManagedSessions).mockReturnValue(request.promise);
  const { result } = renderSessions();
  act(() => result.current.toggleBot('b', 'managed'));
  act(() => result.current.selectMineSession('b', 'b2'));
  await act(async () => request.resolve(page(['b1', 'b2'], 'b')));
  expect(store.getState().selectedSessionId).toBe('b2');
});
it('collapsing a pending Bot cancels automatic selection', async () => {
  const request = deferred();
  jest.mocked(conversationService.listManagedSessions).mockReturnValue(request.promise);
  const { result } = renderSessions();
  act(() => result.current.toggleBot('b', 'managed'));
  act(() => result.current.toggleBot('b', 'managed'));
  await act(async () => request.resolve(page(['b1'], 'b')));
  expect(store.getState()).toMatchObject({ expandedBotIds: {}, selectedSessionId: null });
});
it('selects within remembered favorite scope rather than stale all-sessions cache', async () => {
  seed('b', ['stale']);
  store.getState().setManagedBotScope('b', 'favorite');
  const request = deferred();
  jest.mocked(conversationService.listManagedSessions).mockReturnValue(request.promise);
  const { result } = renderSessions();
  act(() => result.current.toggleBot('b', 'managed'));
  expect(store.getState()).toMatchObject({ selectedBotId: 'b', selectedSessionId: null, selectedScope: 'favorite' });
  await act(async () => request.resolve(page(['favorite-first'], 'b')));
  expect(store.getState().selectedSessionId).toBe('favorite-first');
});
it('readonly origin drops old interactive selection and picks the first visible friend session', () => {
  store.getState().setManagedBotOrigin('b', 'others');
  store.getState().setExpandedFriend('b', 'friend-user', true);
  store.getState().setManagedBotCache('b', {
    ...managedViewOf('b', 'all', undefined, firstPageOf([], 0)),
    origin: 'others',
    friendDirectory: { items: [{ userId: 'friend-user', displayName: 'User' }], loading: false, error: null },
    friendGroups: {
      'friend-user': {
        friend: { userId: 'friend-user', displayName: 'User' },
        state: 'loaded',
        expanded: true,
        sessions: firstPageOf([session('other1', 'b'), session('other2', 'b')], 2),
      },
    },
  });
  const { result } = renderSessions();
  act(() => result.current.toggleBot('b', 'managed'));
  expect(store.getState()).toMatchObject({
    selectedBotId: 'b',
    selectedOrigin: 'others',
    selectedFriendUserId: 'friend-user',
    selectedSessionId: 'other1',
  });
  expect(conversationService.listManagedSessions).not.toHaveBeenCalled();
});
it('failed list load can be retried and selects the first successful result', async () => {
  jest
    .mocked(conversationService.listManagedSessions)
    .mockResolvedValueOnce({ ok: false, error: { code: 'FAIL', friendlyMessage: '列表失败', canRetry: true } })
    .mockResolvedValueOnce(page(['b1'], 'b'));
  const { result } = renderSessions();
  act(() => result.current.toggleBot('b', 'managed'));
  await waitFor(() => expect(store.getState().sessionsByBotId.b.sessions.error).toBe('列表失败'));
  expect(store.getState().selectedSessionId).toBeNull();
  act(() => result.current.retrySessions('b', 'managed'));
  await waitFor(() => expect(store.getState().selectedSessionId).toBe('b1'));
});

it('reloads a cached empty partial page instead of getting stuck without selecting a real first session', async () => {
  store.getState().setManagedBotCache('b', managedViewOf('b', 'all', undefined, firstPageOf([], 12)));
  jest.mocked(conversationService.listManagedSessions).mockResolvedValue(page(['b1'], 'b'));
  const { result } = renderSessions();
  act(() => result.current.toggleBot('b', 'managed'));
  await waitFor(() => expect(store.getState().selectedSessionId).toBe('b1'));
});

it('a delayed creation caches its new session but does not steal selection after switching Bot', async () => {
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
  act(() => result.current.toggleBot('a', 'managed'));
  await act(async () => {
    resolve({ ok: true, data: session('b-new', 'b') });
    await creation;
  });
  expect(store.getState()).toMatchObject({ selectedBotId: 'a', selectedSessionId: 'a1', expandedBotIds: { a: true } });
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

it('new-session selection wins over a pending first-page auto-selection', async () => {
  const request = deferred();
  jest.mocked(conversationService.listManagedSessions).mockReturnValue(request.promise);
  jest.mocked(botSessionService.createSession).mockResolvedValue({ ok: true, data: session('new-created', 'b') });
  const { result } = renderSessions();
  act(() => result.current.toggleBot('b', 'managed'));
  await act(async () => result.current.createSession('b'));
  await act(async () => request.resolve(page(['older-first', 'new-created'], 'b')));
  expect(store.getState().selectedSessionId).toBe('new-created');
});

it('user change cancels a pending automatic selection', async () => {
  const request = deferred();
  jest.mocked(conversationService.listManagedSessions).mockReturnValue(request.promise);
  const { result, rerender } = renderSessions();
  act(() => result.current.toggleBot('b', 'managed'));
  act(() => store.getState().reset());
  rerender({ ...options, userId: 'another-user' });
  await act(async () => request.resolve(page(['b1'], 'b')));
  expect(store.getState().selectedSessionId).toBeNull();
  expect(store.getState().sessionsByBotId.b).toBeUndefined();
});

it('readonly Bot without visible friend sessions clears old chat and never creates a writable selection', () => {
  store.getState().setManagedBotOrigin('b', 'others');
  const { result } = renderSessions();
  act(() => result.current.toggleBot('b', 'managed'));
  expect(store.getState()).toMatchObject({
    selectedBotId: 'b',
    selectedOrigin: 'others',
    selectedSessionId: null,
    selectedFriendUserId: null,
  });
  expect(conversationService.listManagedSessions).not.toHaveBeenCalled();
});
