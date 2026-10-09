import { botSessionService, type ChatBotView } from '@/services/workspace/botSessionService';
import { conversationFavoriteService } from '@/services/workspace/conversationFavoriteService';

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
beforeEach(() => jest.resetAllMocks());

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
