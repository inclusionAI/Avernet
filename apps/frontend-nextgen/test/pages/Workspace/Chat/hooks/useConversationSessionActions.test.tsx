/** @jest-environment jsdom */
import type { ConversationBotView } from '@/domain/conversation';
import { firstPageOf, managedViewOf } from '@/pages/Workspace/Chat/hooks/conversationSessionCache';
import { useConversationSessionActions } from '@/pages/Workspace/Chat/hooks/useConversationSessionActions';
import { botSessionService } from '@/services/workspace/botSessionService';
import type { ConversationSessionAction } from '@/services/workspace/conversationSessionActionService';
import { useConversationStore as store } from '@/stores/conversationStore';
import { act, renderHook } from '@testing-library/react';
import { toast } from 'sonner';
jest.mock('@/services/workspace/botSessionService', () => ({
  BOT_SESSION_PAGE_SIZE: 10,
  botSessionService: { deleteSession: jest.fn(), clearContext: jest.fn(), updateSessionTitle: jest.fn() },
}));
jest.mock('sonner', () => ({ toast: { success: jest.fn(), error: jest.fn() } }));
const view: ConversationBotView = {
  section: 'managed',
  bot: { botId: 'b:owner', realBotId: 'b', ownerId: 'owner', displayName: 'Bot', online: true, chatable: true },
};
const session = {
  sessionId: 's1',
  botId: view.bot.botId,
  title: '会话',
  messageCount: 10,
  favorite: true,
  gmtCreate: '',
  gmtModified: '',
};
const options = { userId: 'user' as string | null, managedBots: [view], friendBots: [] as ConversationBotView[] };
const renderActions = () => renderHook((props) => useConversationSessionActions(props), { initialProps: options });
const list = () => store.getState().sessionsByBotId[view.bot.botId].sessions;
function deferred() {
  let resolve!: (result: { ok: true; data: null }) => void;
  const promise = new Promise<{ ok: true; data: null }>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}
beforeEach(() => {
  jest.clearAllMocks();
  store.getState().reset();
  store
    .getState()
    .setManagedBotCache(view.bot.botId, managedViewOf(view.bot.botId, 'all', undefined, firstPageOf([session], 1)));
  jest.mocked(botSessionService.deleteSession).mockResolvedValue({ ok: true, data: null });
  jest.mocked(botSessionService.clearContext).mockResolvedValue({ ok: true, data: null });
});
it('executes and shows success after cache removal', async () => {
  const { result } = renderActions();
  await act(async () => {
    expect(await result.current.run(view.bot.botId, 'managed', 's1', { type: 'delete' })).toBe(true);
  });
  expect(list().items).toEqual([]);
  expect(toast.success).toHaveBeenCalledWith('会话已删除');
});
it('blocks reentry for all writes on a Bot while pending and releases after completion', async () => {
  const req = deferred();
  jest.mocked(botSessionService.deleteSession).mockReturnValue(req.promise);
  const { result } = renderActions();
  let pending!: Promise<boolean>;
  act(() => {
    pending = result.current.run(view.bot.botId, 'managed', 's1', { type: 'delete' });
  });
  expect(result.current.isPending(view.bot.botId, 'managed')).toBe(true);
  await act(async () => {
    expect(await result.current.run(view.bot.botId, 'managed', 's1', { type: 'clear' })).toBe(false);
  });
  expect(botSessionService.clearContext).not.toHaveBeenCalled();
  await act(async () => {
    req.resolve({ ok: true, data: null });
    await pending;
  });
  expect(result.current.isPending(view.bot.botId, 'managed')).toBe(false);
});
it.each(['rename', 'clear', 'delete'] as const)('failed %s keeps data and reports error', async (type) => {
  const failure = { ok: false as const, error: { code: 'FAIL', friendlyMessage: '操作失败', canRetry: true } };
  jest.mocked(botSessionService.deleteSession).mockResolvedValue(failure);
  jest.mocked(botSessionService.clearContext).mockResolvedValue(failure);
  jest.mocked(botSessionService.updateSessionTitle).mockResolvedValue(failure);
  const { result } = renderActions();
  await act(async () => {
    expect(
      await result.current.run(view.bot.botId, 'managed', 's1', { type, title: 'new' } as ConversationSessionAction),
    ).toBe(false);
  });
  expect(list().items[0]).toEqual(session);
  expect(toast.error).toHaveBeenCalledWith('操作失败');
  expect(result.current.isPending(view.bot.botId, 'managed')).toBe(false);
});
it('ignores in-flight response and stale callbacks after a user change', async () => {
  const req = deferred();
  jest.mocked(botSessionService.deleteSession).mockReturnValue(req.promise);
  const { result, rerender } = renderActions();
  const oldRun = result.current.run;
  let pending!: Promise<boolean>;
  act(() => {
    pending = oldRun(view.bot.botId, 'managed', 's1', { type: 'delete' });
  });
  rerender({ ...options, userId: 'new-user' });
  await act(async () => {
    req.resolve({ ok: true, data: null });
    expect(await pending).toBe(false);
  });
  await act(async () => {
    expect(await oldRun(view.bot.botId, 'managed', 's1', { type: 'delete' })).toBe(false);
  });
  expect(list().items).toHaveLength(1);
  expect(botSessionService.deleteSession).toHaveBeenCalledTimes(1);
  expect(toast.success).not.toHaveBeenCalled();
});
it('does not write after unmount', async () => {
  const req = deferred();
  jest.mocked(botSessionService.deleteSession).mockReturnValue(req.promise);
  const { result, unmount } = renderActions();
  let pending!: Promise<boolean>;
  act(() => {
    pending = result.current.run(view.bot.botId, 'managed', 's1', { type: 'delete' });
  });
  unmount();
  await act(async () => {
    req.resolve({ ok: true, data: null });
    expect(await pending).toBe(false);
  });
  expect(list().items).toHaveLength(1);
});
it('refuses missing user/Bot, read-only origin and list pagination', async () => {
  const { result, rerender } = renderActions();
  rerender({ ...options, userId: null });
  await act(async () => {
    expect(await result.current.run(view.bot.botId, 'managed', 's1', { type: 'delete' })).toBe(false);
  });
  rerender(options);
  await act(async () => {
    expect(await result.current.run('unknown', 'managed', 's1', { type: 'delete' })).toBe(false);
  });
  store.getState().setManagedBotOrigin(view.bot.botId, 'others');
  await act(async () => {
    expect(await result.current.run(view.bot.botId, 'managed', 's1', { type: 'delete' })).toBe(false);
  });
  store.getState().setManagedBotOrigin(view.bot.botId, 'mine');
  store.getState().setManagedBotCache(view.bot.botId, {
    ...store.getState().sessionsByBotId[view.bot.botId],
    sessions: { ...list(), isLoadingMore: true },
  });
  await act(async () => {
    expect(await result.current.run(view.bot.botId, 'managed', 's1', { type: 'delete' })).toBe(false);
  });
  expect(botSessionService.deleteSession).not.toHaveBeenCalled();
});
it('handles unexpected rejection and allows retry', async () => {
  jest.mocked(botSessionService.deleteSession).mockRejectedValueOnce(new Error('network'));
  const { result } = renderActions();
  await act(async () => {
    expect(await result.current.run(view.bot.botId, 'managed', 's1', { type: 'delete' })).toBe(false);
  });
  expect(toast.error).toHaveBeenCalled();
  expect(list().items).toHaveLength(1);
  await act(async () => {
    expect(await result.current.run(view.bot.botId, 'managed', 's1', { type: 'delete' })).toBe(true);
  });
});
