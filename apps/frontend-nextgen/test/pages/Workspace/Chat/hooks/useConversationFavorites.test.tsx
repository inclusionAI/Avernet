/** @jest-environment jsdom */
import type { ConversationBotView } from '@/domain/conversation';
import { firstPageOf, managedViewOf } from '@/pages/Workspace/Chat/hooks/conversationSessionCache';
import { useConversationFavorites } from '@/pages/Workspace/Chat/hooks/useConversationFavorites';
import { conversationFavoriteService } from '@/services/workspace/conversationFavoriteService';
import { useConversationStore } from '@/stores/conversationStore';
import { act, renderHook } from '@testing-library/react';
import { toast } from 'sonner';

jest.mock('@/services/workspace/conversationFavoriteService', () => ({
  conversationFavoriteService: { toggle: jest.fn() },
}));
jest.mock('sonner', () => ({ toast: { error: jest.fn() } }));
const toggle = conversationFavoriteService.toggle as jest.Mock;
const view: ConversationBotView = {
  section: 'managed',
  bot: { botId: 'b:owner', realBotId: 'b', ownerId: 'owner', displayName: 'Bot', online: true, chatable: true },
};
const session = {
  sessionId: 's1',
  botId: view.bot.botId,
  title: '会话',
  messageCount: 1,
  favorite: false,
  gmtCreate: '',
  gmtModified: '',
};
function seed(scope: 'all' | 'favorite' = 'all', favorite = false) {
  const s = useConversationStore.getState();
  s.setManagedBotScope(view.bot.botId, scope);
  s.setManagedBotCache(
    view.bot.botId,
    managedViewOf(view.bot.botId, scope, undefined, firstPageOf([{ ...session, favorite }], 1)),
  );
}
function renderFavorites(managed = view, userId: string | null = 'user') {
  return renderHook((props) => useConversationFavorites(props), {
    initialProps: { userId, managedBots: [managed], friendBots: [] as ConversationBotView[] },
  });
}
function deferred() {
  let resolve!: (value: any) => void;
  const promise = new Promise<any>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}
beforeEach(() => {
  jest.resetAllMocks();
  useConversationStore.getState().reset();
  seed();
  toggle.mockResolvedValue({ ok: true, data: true });
});

it('favorites through the service and patches the latest session without changing selection', async () => {
  const { result } = renderFavorites();
  await act(async () => {
    await result.current.toggleFavorite(view.bot.botId, 'managed', 's1');
  });
  expect(toggle).toHaveBeenCalledWith(view.bot, 'user', 's1', true);
  expect(useConversationStore.getState().sessionsByBotId[view.bot.botId].sessions.items[0].favorite).toBe(true);
  expect(useConversationStore.getState().selectedSessionId).toBeNull();
});
it('supports friend Bot sessions with their friend identity', async () => {
  const friend: ConversationBotView = { ...view, section: 'friend', bot: { ...view.bot, isFriendBot: true } };
  useConversationStore.getState().setFriendBotSessions(view.bot.botId, firstPageOf([session], 1));
  const { result } = renderHook(() =>
    useConversationFavorites({ userId: 'user', managedBots: [], friendBots: [friend] }),
  );
  await act(async () => {
    await result.current.toggleFavorite(view.bot.botId, 'friend', 's1');
  });
  expect(toggle).toHaveBeenCalledWith(friend.bot, 'user', 's1', true);
  expect(useConversationStore.getState().friendBotSessionsByBotId[view.bot.botId].items[0].favorite).toBe(true);
});
it('removes an unfavorited item from favorite scope and clears its selected session only', async () => {
  seed('favorite', true);
  toggle.mockResolvedValue({ ok: true, data: false });
  useConversationStore.getState().selectConversation({
    botId: view.bot.botId,
    section: 'managed',
    origin: 'mine',
    scope: 'favorite',
    sessionId: 's1',
    friendUserId: null,
  });
  const { result } = renderFavorites();
  await act(async () => {
    await result.current.toggleFavorite(view.bot.botId, 'managed', 's1');
  });
  const s = useConversationStore.getState();
  expect(toggle).toHaveBeenCalledWith(view.bot, 'user', 's1', false);
  expect(s.sessionsByBotId[view.bot.botId].sessions).toMatchObject({ items: [], total: 0 });
  expect(s.selectedSessionId).toBeNull();
  expect(s.selectedBotId).toBe(view.bot.botId);
});
it('prevents duplicate requests and exposes row pending state', async () => {
  const request = deferred();
  toggle.mockReturnValue(request.promise);
  const { result } = renderFavorites();
  let pending!: Promise<boolean>;
  act(() => {
    pending = result.current.toggleFavorite(view.bot.botId, 'managed', 's1');
  });
  expect(result.current.isPending(view.bot.botId, 'managed', 's1')).toBe(true);
  await act(async () => {
    await result.current.toggleFavorite(view.bot.botId, 'managed', 's1');
  });
  expect(toggle).toHaveBeenCalledTimes(1);
  await act(async () => {
    request.resolve({ ok: true, data: true });
    await pending;
  });
  expect(result.current.isPending(view.bot.botId, 'managed', 's1')).toBe(false);
});
it('preserves the old value and shows an error on failed mutations', async () => {
  toggle.mockResolvedValue({ ok: false, error: { friendlyMessage: '收藏失败' } });
  const { result } = renderFavorites();
  await act(async () => {
    await result.current.toggleFavorite(view.bot.botId, 'managed', 's1');
  });
  expect(toast.error).toHaveBeenCalledWith('收藏失败');
  expect(useConversationStore.getState().sessionsByBotId[view.bot.botId].sessions.items[0].favorite).toBe(false);
  expect(result.current.isPending(view.bot.botId, 'managed', 's1')).toBe(false);
});
it.each(['others', 'teclaw', 'unknown', 'anonymous'] as const)(
  'rejects %s mutations before requesting',
  async (mode) => {
    if (mode === 'others') useConversationStore.getState().setManagedBotOrigin(view.bot.botId, 'others');
    if (mode === 'unknown') {
      const store = useConversationStore.getState();
      const cache = store.sessionsByBotId[view.bot.botId];
      store.setManagedBotCache(view.bot.botId, {
        ...cache,
        sessions: { ...cache.sessions, items: [{ ...session, favorite: undefined }] },
      });
    }
    const bot = mode === 'teclaw' ? { ...view, bot: { ...view.bot, engine: 'TEClaw' } } : view;
    const { result } = renderFavorites(bot, mode === 'anonymous' ? null : 'user');
    await act(async () => {
      await result.current.toggleFavorite(view.bot.botId, 'managed', 's1');
    });
    expect(toggle).not.toHaveBeenCalled();
  },
);
it.each(['user', 'unmount', 'others'] as const)('ignores late writes after %s change', async (change) => {
  const request = deferred();
  toggle.mockReturnValue(request.promise);
  const { result, rerender, unmount } = renderFavorites();
  let pending!: Promise<boolean>;
  act(() => {
    pending = result.current.toggleFavorite(view.bot.botId, 'managed', 's1');
  });
  if (change === 'user') rerender({ userId: 'new-user', managedBots: [view], friendBots: [] });
  if (change === 'unmount') unmount();
  if (change === 'others') act(() => useConversationStore.getState().setManagedBotOrigin(view.bot.botId, 'others'));
  await act(async () => {
    request.resolve({ ok: true, data: true });
    await pending;
  });
  expect(useConversationStore.getState().sessionsByBotId[view.bot.botId].sessions.items[0].favorite).toBe(false);
  expect(toast.error).not.toHaveBeenCalled();
});

it('blocks favorite-list writes while that list is paginating', async () => {
  seed('favorite', true);
  const s = useConversationStore.getState();
  const cache = s.sessionsByBotId[view.bot.botId];
  s.setManagedBotCache(view.bot.botId, { ...cache, sessions: { ...cache.sessions, isLoadingMore: true } });
  const { result } = renderFavorites();
  expect(result.current.isPending(view.bot.botId, 'managed', 's1')).toBe(true);
  await act(async () => {
    await result.current.toggleFavorite(view.bot.botId, 'managed', 's1');
  });
  expect(toggle).not.toHaveBeenCalled();
});

it('team 收藏写回管理类缓存，不写好友缓存；他人来源禁止收藏', async () => {
  const team: ConversationBotView = { ...view, section: 'team', bot: { ...view.bot, isTeamBot: true } };
  const { result } = renderFavorites(team);
  await act(async () => {
    await result.current.toggleFavorite(team.bot.botId, 'team', 's1');
  });
  expect(toggle).toHaveBeenCalledWith(team.bot, 'user', 's1', true);
  expect(useConversationStore.getState().sessionsByBotId[team.bot.botId].sessions.items[0].favorite).toBe(true);
  expect(useConversationStore.getState().friendBotSessionsByBotId).toEqual({});
  act(() => useConversationStore.getState().setManagedBotOrigin(team.bot.botId, 'others'));
  toggle.mockClear();
  await act(async () => {
    await result.current.toggleFavorite(team.bot.botId, 'team', 's1');
  });
  expect(toggle).not.toHaveBeenCalled();
});
