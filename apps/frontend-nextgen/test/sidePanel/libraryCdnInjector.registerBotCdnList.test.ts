/** @jest-environment jsdom */
import { getManifest, listBotRenderScreens, type BotRenderScreen } from '@/services/bcs/bcsManifestController';
import {
  clearCdnConfig,
  getLibraryCdn,
  hasBotCdnBucket,
  registerBotCdnList,
} from '@/services/bcs/libraryCdnInjector';

jest.mock('@/services/bcs/bcsManifestController', () => ({
  getManifest: jest.fn(),
  listBotRenderScreens: jest.fn(),
}));

const mockListBotRenderScreens = jest.mocked(listBotRenderScreens);
const mockGetManifest = jest.mocked(getManifest);

// listBotRenderScreens 返回项（BotRenderScreen 兼容 LibraryCdnConfig）。
function screen(name: string, cdnUrl: string): BotRenderScreen {
  return { id: Math.random(), bot_id: 'unused', name, cdn_url: cdnUrl } as BotRenderScreen;
}

beforeEach(() => {
  clearCdnConfig();
  mockListBotRenderScreens.mockReset();
  mockGetManifest.mockReset();
});

test('并行注册多个 Bot 的 CDN 配置并按复合键去重', async () => {
  mockListBotRenderScreens.mockImplementation(async (botId: string) => {
    if (botId === 'bot-a') return [screen('libA', 'https://cdn/a.js')];
    if (botId === 'bot-b') return [screen('libB', 'https://cdn/b.js')];
    return [];
  });
  await registerBotCdnList([
    { botId: 'bot-a', ownerId: '100001' },
    { botId: 'bot-a', ownerId: '100001' }, // 复合键重复
    { botId: 'bot-b' },
  ]);
  expect(getLibraryCdn('libA')).toBe('https://cdn/a.js');
  expect(getLibraryCdn('libB')).toBe('https://cdn/b.js');
  expect(mockListBotRenderScreens).toHaveBeenCalledTimes(2);
  expect(mockListBotRenderScreens).toHaveBeenCalledWith('bot-a', '100001');
  expect(mockListBotRenderScreens).toHaveBeenCalledWith('bot-b', undefined);
});

test('单个 Bot 请求失败不阻塞其余注册，且写空桶负缓存', async () => {
  const consoleSpy = jest.spyOn(console, 'error').mockImplementation(() => {});
  mockListBotRenderScreens.mockImplementation(async (botId: string) => {
    if (botId === 'bad-bot') throw new Error('network unavailable');
    return [screen('libOk', 'https://cdn/ok.js')];
  });
  await registerBotCdnList([
    { botId: 'bad-bot', ownerId: '100001' },
    { botId: 'ok-bot' },
  ]);
  expect(getLibraryCdn('libOk')).toBe('https://cdn/ok.js');
  expect(hasBotCdnBucket('bad-bot', '100001')).toBe(true); // 负缓存已写
  expect(consoleSpy).toHaveBeenCalledWith(expect.stringContaining('[CollabCdn]'), expect.any(Error));
  consoleSpy.mockRestore();
});

test('已入桶（含负缓存）的 Bot 会话内不重拉', async () => {
  mockListBotRenderScreens.mockResolvedValue([screen('libA', 'https://cdn/a.js')]);
  await registerBotCdnList([{ botId: 'bot-a', ownerId: '100001' }]);
  expect(mockListBotRenderScreens).toHaveBeenCalledTimes(1);
  mockListBotRenderScreens.mockClear();
  // 二次调用（live 增量 diff 重复触发）不产生新请求。
  await registerBotCdnList([{ botId: 'bot-a', ownerId: '100001' }]);
  expect(mockListBotRenderScreens).not.toHaveBeenCalled();
  // 清桶后可重新拉取。
  mockListBotRenderScreens.mockResolvedValue([]);
  // eslint-disable-next-line @typescript-eslint/no-unsafe-argument
  await registerBotCdnList([{ botId: 'bot-c' }]);
  expect(mockListBotRenderScreens).toHaveBeenCalledWith('bot-c', undefined);
});

test('全部失败时写满空桶不抛错，返回当前映射数', async () => {
  const consoleSpy = jest.spyOn(console, 'error').mockImplementation(() => {});
  mockListBotRenderScreens.mockRejectedValue(new Error('boom'));
  await expect(
    registerBotCdnList([
      { botId: 'bot-a', ownerId: '1' },
      { botId: 'bot-b', ownerId: '2' },
    ]),
  ).resolves.toBe(0);
  expect(hasBotCdnBucket('bot-a', '1')).toBe(true);
  expect(hasBotCdnBucket('bot-b', '2')).toBe(true);
  consoleSpy.mockRestore();
});

test('多 owner 同名 botId 请求互不串桶，后注册不覆盖先注册', async () => {
  mockListBotRenderScreens.mockImplementation(async (_botId: string, ownerId?: string) =>
    ownerId === '100001' ? [screen('libA', 'https://cdn/a.js')] : [screen('libB', 'https://cdn/b.js')],
  );
  await registerBotCdnList([{ botId: 'default', ownerId: '100001' }, { botId: 'default', ownerId: '100002' }]);
  expect(getLibraryCdn('libA')).toBe('https://cdn/a.js');
  expect(getLibraryCdn('libB')).toBe('https://cdn/b.js');
  expect(hasBotCdnBucket('default', '100001')).toBe(true);
  expect(hasBotCdnBucket('default', '100002')).toBe(true);
});
