import { botSessionService, type ChatBotView } from '@/services/workspace/botSessionService';
import { conversationFavoriteService } from '@/services/workspace/conversationFavoriteService';
import { clearConversationFavoriteCache } from '@/services/workspace/conversationFavoriteCache';

jest.mock('@/services/workspace/botSessionService', () => ({
  botSessionService: { listFavoriteSessionsPage: jest.fn(), toggleFavorite: jest.fn() },
}));
const list = botSessionService.listFavoriteSessionsPage as jest.Mock;
const toggle = botSessionService.toggleFavorite as jest.Mock;
const bot: ChatBotView = {
  botId: 'b:owner',
  realBotId: 'b',
  ownerId: 'owner',
  displayName: 'Bot',
  chatable: true,
  online: true,
};
const session = (sessionId: string) => ({
  sessionId,
  botId: bot.botId,
  title: sessionId,
  messageCount: 0,
  gmtCreate: '',
  gmtModified: '',
});
beforeEach(() => { jest.resetAllMocks(); clearConversationFavoriteCache(); });

it('hydrates favorites beyond the first favorite page', async () => {
  list
    .mockResolvedValueOnce({ ok: true, data: { items: [session('other')], total: 101 } })
    .mockResolvedValueOnce({ ok: true, data: { items: [session('s2')], total: 101 } });
  const result = await conversationFavoriteService.hydrate(bot, 'user', {
    items: [session('s1'), session('s2')],
    total: 20,
  });
  expect(result.items.map((s) => s.favorite)).toEqual([false, true]);
  expect(result.total).toBe(20);
  expect(list).toHaveBeenLastCalledWith(bot, 'user', 2, 100);
});
it('stops early when all displayed sessions are known favorites', async () => {
  list.mockResolvedValue({ ok: true, data: { items: [session('s1')], total: 300 } });
  expect(
    (await conversationFavoriteService.hydrate(bot, 'user', { items: [session('s1')], total: 1 })).items[0].favorite,
  ).toBe(true);
  expect(list).toHaveBeenCalledTimes(1);
});
it('preserves ordinary sessions but leaves favorite status unknown on failure', async () => {
  list.mockResolvedValue({ ok: false, error: { friendlyMessage: '收藏加载失败' } });
  const result = await conversationFavoriteService.hydrate(bot, 'user', { items: [session('s1')], total: 1 });
  expect(result.items[0]).toEqual(session('s1'));
});
it.each(['teclaw', ' TEClaw '])('never reads or writes favorites for %s', async (engine) => {
  const unsupported = { ...bot, engine };
  await conversationFavoriteService.hydrate(unsupported, 'user', { items: [session('s1')], total: 1 });
  expect((await conversationFavoriteService.toggle(unsupported, 'user', 's1', true)).ok).toBe(false);
  expect(list).not.toHaveBeenCalled();
  expect(toggle).not.toHaveBeenCalled();
});
it('delegates supported favorite mutations with the original bot context', async () => {
  toggle.mockResolvedValue({ ok: true, data: true });
  expect(await conversationFavoriteService.toggle({ ...bot, isFriendBot: true }, 'user', 's1', true)).toEqual({
    ok: true,
    data: true,
  });
  expect(toggle).toHaveBeenCalledWith({ ...bot, isFriendBot: true }, 'user', 's1', true);
});

it('merges in-flight favorite pages and reuses them for different ordinary pages', async () => {
  let finish!: (value: unknown) => void;
  list.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
  const first = conversationFavoriteService.hydrate(bot, 'user', { items: [session('s1')], total: 1 });
  const second = conversationFavoriteService.hydrate(bot, 'user', { items: [session('s2')], total: 1 });
  expect(list).toHaveBeenCalledTimes(1);
  finish({ ok: true, data: { items: [session('s1')], total: 1 } });
  expect((await first).items[0].favorite).toBe(true);
  expect((await second).items[0].favorite).toBe(false);
  await conversationFavoriteService.hydrate(bot, 'user', { items: [session('s3')], total: 1 });
  expect(list).toHaveBeenCalledTimes(1);
});

it('isolates owner, stage, viewer and instance ranges', async () => {
  list.mockResolvedValue({ ok: true, data: { items: [], total: 0 } });
  const input = { items: [session('s1')], total: 1 };
  await conversationFavoriteService.hydrate(bot, 'user', input);
  await conversationFavoriteService.hydrate({ ...bot, ownerId: 'other' }, 'user', input);
  await conversationFavoriteService.hydrate({ ...bot, runtimeStage: 'online' }, 'user', input);
  await conversationFavoriteService.hydrate(bot, 'user', input, { deviceUuid: 'device-1', stage: 'online' });
  await conversationFavoriteService.hydrate(bot, 'user', input, { deviceUuid: 'device-2', stage: 'online' });
  await conversationFavoriteService.hydrate(bot, 'other-user', input);
  expect(list).toHaveBeenCalledTimes(6);
  expect(list).toHaveBeenNthCalledWith(4, bot, 'user', 1, 100, { deviceUuid: 'device-1', stage: 'online' });
});

it('expires cached pages after 30 seconds and retries failed reads', async () => {
  const now = jest.spyOn(Date, 'now').mockReturnValue(1000);
  const input = { items: [session('s1')], total: 1 };
  try {
    list.mockResolvedValue({ ok: true, data: { items: [], total: 0 } });
    await conversationFavoriteService.hydrate(bot, 'user', input);
    now.mockReturnValue(31_001);
    list.mockResolvedValueOnce({ ok: false, error: { friendlyMessage: '失败' } });
    expect((await conversationFavoriteService.hydrate(bot, 'user', input)).items[0].favorite).toBeUndefined();
    expect((await conversationFavoriteService.hydrate(bot, 'user', input)).items[0].favorite).toBe(false);
    expect(list).toHaveBeenCalledTimes(3);
  } finally { now.mockRestore(); }
});

it('invalidates reads around mutations; late old reads cannot mark stale favorite state', async () => {
  let finish!: (value: unknown) => void;
  list.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
  const input = { items: [session('s1')], total: 1 };
  const pending = conversationFavoriteService.hydrate(bot, 'user', input);
  toggle.mockResolvedValue({ ok: true, data: true });
  await conversationFavoriteService.toggle(bot, 'user', 's1', true);
  finish({ ok: true, data: { items: [], total: 0 } });
  expect((await pending).items[0].favorite).toBeUndefined();
  list.mockResolvedValue({ ok: true, data: { items: [session('s1')], total: 1 } });
  expect((await conversationFavoriteService.hydrate(bot, 'user', input)).items[0].favorite).toBe(true);
  expect(list).toHaveBeenCalledTimes(2);
});
