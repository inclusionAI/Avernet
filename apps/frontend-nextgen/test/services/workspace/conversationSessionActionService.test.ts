import { firstPageOf, managedViewOf, sessionRequestPage } from '@/pages/Workspace/Chat/hooks/conversationSessionCache';
import type { ChatBotView } from '@/services/workspace/botSessionService';
import { botSessionService } from '@/services/workspace/botSessionService';
import { conversationSessionActionService as service } from '@/services/workspace/conversationSessionActionService';
import { useConversationStore as store } from '@/stores/conversationStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';

jest.mock('@/services/workspace/botSessionService', () => ({
  BOT_SESSION_PAGE_SIZE: 10,
  botSessionService: { updateSessionTitle: jest.fn(), clearContext: jest.fn(), deleteSession: jest.fn() },
}));
const bot: ChatBotView = {
  botId: 'b:owner',
  realBotId: 'b',
  ownerId: 'owner',
  displayName: 'Bot',
  chatable: true,
  online: true,
};
const session = {
  botId: bot.botId,
  sessionId: 's1',
  title: '原标题',
  favorite: true,
  messageCount: 15,
  gmtCreate: '',
  gmtModified: '',
};
const request = { bot, section: 'managed' as const, userId: 'user', sessionId: 's1' };
const cached = () => store.getState().sessionsByBotId[bot.botId].sessions;
const seed = (scope: 'all' | 'favorite' = 'all') => {
  store.getState().setManagedBotScope(bot.botId, scope);
  store
    .getState()
    .setManagedBotCache(
      bot.botId,
      managedViewOf(bot.botId, scope, undefined, firstPageOf([session, { ...session, sessionId: 's2' }], 2)),
    );
  store.getState().selectConversation({
    botId: bot.botId,
    section: 'managed',
    origin: 'mine',
    scope,
    sessionId: 's1',
    friendUserId: null,
  });
};
beforeEach(() => {
  jest.clearAllMocks();
  store.getState().reset();
  useWorkspaceStore.getState().reset();
  seed();
  jest
    .mocked(botSessionService.updateSessionTitle)
    .mockResolvedValue({ ok: true, data: { ...session, title: '服务端标题' } });
  jest.mocked(botSessionService.clearContext).mockResolvedValue({ ok: true, data: null });
  jest.mocked(botSessionService.deleteSession).mockResolvedValue({ ok: true, data: null });
});
it.each(['rename', 'clear', 'delete'] as const)(
  'rejects %s from others even if a write caller supplies a session id',
  async (type) => {
    store.getState().setManagedBotOrigin(bot.botId, 'others');
    expect(await service.execute({ ...request, action: { type, title: '新标题' } })).toMatchObject({ ok: false });
    expect(botSessionService.updateSessionTitle).not.toHaveBeenCalled();
    expect(botSessionService.clearContext).not.toHaveBeenCalled();
    expect(botSessionService.deleteSession).not.toHaveBeenCalled();
  },
);
it('renames with trimmed input and applies only returned title to latest cache', async () => {
  const result = await service.execute({ ...request, action: { type: 'rename', title: ' 新标题 ' } });
  expect(botSessionService.updateSessionTitle).toHaveBeenCalledWith(bot, 'user', 's1', '新标题');
  store.getState().setManagedBotCache(bot.botId, {
    ...store.getState().sessionsByBotId[bot.botId],
    sessions: { ...cached(), items: cached().items.map((s) => ({ ...s, messageCount: 30 })) },
  });
  if (!result.ok) throw new Error('expected success');
  service.apply(request, result.data);
  expect(cached().items[0]).toMatchObject({ title: '服务端标题', messageCount: 30, favorite: true });
});
it('clears message count and refreshes history only for the matching interactive selection', async () => {
  const result = await service.execute({ ...request, action: { type: 'clear' } });
  if (!result.ok) throw new Error('expected success');
  service.apply(request, result.data);
  expect(cached().items[0].messageCount).toBe(0);
  expect(useWorkspaceStore.getState().historyRefreshNonce).toBe(1);
  store.getState().selectConversation({
    botId: 'other',
    section: 'friend',
    origin: 'mine',
    scope: 'all',
    sessionId: 's1',
    friendUserId: null,
  });
  service.apply(request, result.data);
  expect(useWorkspaceStore.getState().historyRefreshNonce).toBe(1);
});
it.each(['all', 'favorite'] as const)(
  'deletes in %s, updates totals, selection and does not skip offset-shifted sessions',
  async (scope) => {
    seed(scope);
    const items = Array.from({ length: 10 }, (_, i) => ({ ...session, sessionId: `s${i + 1}` }));
    store.getState().setManagedBotCache(bot.botId, managedViewOf(bot.botId, scope, undefined, firstPageOf(items, 21)));
    const result = await service.execute({ ...request, action: { type: 'delete' } });
    if (!result.ok) throw new Error('expected success');
    service.apply(request, result.data);
    expect(cached()).toMatchObject({ total: 20, hasMore: true });
    expect(cached().items).toHaveLength(9);
    expect(sessionRequestPage(cached(), scope, true)).toBe(1);
    expect(store.getState().selectedSessionId).toBe('s2');
  },
);
it('supports TEClaw and friend Bot using original bot identity (no favorites dependency)', async () => {
  const friend = { ...bot, engine: 'TEClaw', isFriendBot: true };
  store.getState().setFriendBotSessions(bot.botId, firstPageOf([session], 1));
  const r = { ...request, bot: friend, section: 'friend' as const };
  const result = await service.execute({ ...r, action: { type: 'delete' } });
  if (!result.ok) throw new Error('expected success');
  service.apply(r, result.data);
  expect(botSessionService.deleteSession).toHaveBeenCalledWith(friend, 'user', 's1');
  expect(store.getState().friendBotSessionsByBotId[bot.botId]).toMatchObject({ items: [], total: 0, hasMore: false });
  expect(cached().items).toHaveLength(2);
});
it('missing session, blank title, non-chatable Bot and loading list cannot write', async () => {
  expect(await service.execute({ ...request, action: { type: 'rename', title: '  ' } })).toMatchObject({ ok: false });
  expect(await service.execute({ ...request, sessionId: 'absent', action: { type: 'delete' } })).toMatchObject({
    ok: false,
  });
  expect(
    await service.execute({ ...request, bot: { ...bot, chatable: false }, action: { type: 'clear' } }),
  ).toMatchObject({ ok: false });
  store.getState().setManagedBotCache(bot.botId, {
    ...store.getState().sessionsByBotId[bot.botId],
    sessions: { ...cached(), isLoadingMore: true },
  });
  expect(await service.execute({ ...request, action: { type: 'delete' } })).toMatchObject({ ok: false });
  expect(botSessionService.deleteSession).not.toHaveBeenCalled();
});
it('failure is propagated and does not change the cache', async () => {
  jest
    .mocked(botSessionService.deleteSession)
    .mockResolvedValue({ ok: false, error: { code: 'FAIL', friendlyMessage: '删除失败', canRetry: true } });
  expect(await service.execute({ ...request, action: { type: 'delete' } })).toMatchObject({ ok: false });
  expect(cached().items).toHaveLength(2);
});
it('response does not overwrite an others cache after switching origin', () => {
  store.getState().setManagedBotOrigin(bot.botId, 'others');
  service.apply(request, { type: 'delete' });
  expect(cached().items).toHaveLength(2);
  expect(store.getState().selectedOrigin).toBe('others');
});

it('deleting the last selected session clears only session selection and preserves Bot/scope', async () => {
  store.getState().setManagedBotCache(bot.botId, managedViewOf(bot.botId, 'all', undefined, firstPageOf([session], 1)));
  const result = await service.execute({ ...request, action: { type: 'delete' } });
  if (!result.ok) throw new Error('expected success');
  service.apply(request, result.data);
  expect(store.getState()).toMatchObject({
    selectedBotId: bot.botId,
    selectedSection: 'managed',
    selectedOrigin: 'mine',
    selectedScope: 'all',
    selectedSessionId: null,
  });
  expect(cached()).toMatchObject({ items: [], total: 0, hasMore: false });
});
it('deleting a non-selected session keeps the other selection', async () => {
  store.getState().selectConversation({
    botId: bot.botId,
    section: 'managed',
    origin: 'mine',
    scope: 'all',
    sessionId: 's2',
    friendUserId: null,
  });
  const result = await service.execute({ ...request, action: { type: 'delete' } });
  if (!result.ok) throw new Error('expected success');
  service.apply(request, result.data);
  expect(store.getState().selectedSessionId).toBe('s2');
});
it('unchanged trimmed title is a successful no-op without a write request', async () => {
  expect(await service.execute({ ...request, action: { type: 'rename', title: ' 原标题 ' } })).toEqual({
    ok: true,
    data: { type: 'rename', title: '原标题' },
  });
  expect(botSessionService.updateSessionTitle).not.toHaveBeenCalled();
});

it('team 删除回写管理类缓存并保留 team 选择，others 拒绝写操作', async () => {
  const target = { ...request, section: 'team' as const, bot: { ...bot, isTeamBot: true } };
  store.getState().selectConversation({
    botId: bot.botId,
    section: 'team',
    origin: 'mine',
    scope: 'all',
    friendUserId: null,
    sessionId: 's1',
  });
  const outcome = await service.execute({ ...target, action: { type: 'delete' } });
  expect(outcome.ok).toBe(true);
  service.apply(target, { type: 'delete' });
  expect(cached().items.map((item) => item.sessionId)).toEqual(['s2']);
  expect(store.getState()).toMatchObject({ selectedSection: 'team', selectedSessionId: 's2' });
  expect(store.getState().friendBotSessionsByBotId).toEqual({});
  store.getState().setManagedBotOrigin(bot.botId, 'others');
  jest.mocked(botSessionService.deleteSession).mockClear();
  expect(await service.execute({ ...target, sessionId: 's2', action: { type: 'delete' } })).toMatchObject({
    ok: false,
  });
  expect(botSessionService.deleteSession).not.toHaveBeenCalled();
});
