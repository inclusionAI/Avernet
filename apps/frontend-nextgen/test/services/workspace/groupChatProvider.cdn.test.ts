import { createSessionToken, listSessionMessages } from '@/services/backendApi/collaboration/sessionController';
import { listBotRenderScreens, type BotRenderScreen } from '@/services/bcs/bcsManifestController';
import { clearCdnConfig, getLibraryCdn } from '@/services/bcs/libraryCdnInjector';
import { createGroupChatProvider } from '@/services/workspace/groupChatProvider';
import type { ChatMessage } from '@tc-chat/core';

jest.mock('@/services/backendApi/collaboration/sessionController');
jest.mock('@/services/bcs/bcsManifestController');

const panel = '<AixUI type="panel" component="lib.View" />';
const screens: BotRenderScreen[] = [{ id: 1, bot_id: 'bot-a', name: 'lib', cdn_url: '/lib.js' }];
function message(content: string, botUuid = 'bot-a:1', id = 'm1'): ChatMessage {
  return { id, role: 'assistant', content, status: 'streaming', extra: { botUuid } };
}
function page(items: Array<{ id: string; timestamp: number; sender: string; content: string }>) {
  return { code: 20000, message: '', request_id: 'r', data: items.map((item) => ({ ...item, role: 'assistant', message_type: 'bot' })) } as Awaited<ReturnType<typeof listSessionMessages>>;
}
function deferred() {
  let resolve!: (value: BotRenderScreen[]) => void;
  const promise = new Promise<BotRenderScreen[]>((done) => { resolve = done; });
  return { promise, resolve };
}
async function flush() {
  await new Promise((resolve) => { setTimeout(resolve, 0); });
}
function createProvider() {
  const inner = {
    isConnected: true,
    connect: jest.fn(), disconnect: jest.fn(),
    transport: { send: jest.fn(), onMessage: jest.fn() },
    subscribeToConnectionStatus: () => () => {},
    onMessage: undefined as ((message: ChatMessage) => void) | undefined,
    onComplete: undefined as ((messages: ChatMessage[]) => void) | undefined,
  };
  const provider = createGroupChatProvider({ groupId: 'g', sessionId: 's', identityId: 'human_1', wsOrigin: 'ws://localhost', createSdkProvider: () => inner as never });
  return { provider, inner };
}

beforeEach(() => {
  jest.resetAllMocks();
  clearCdnConfig();
  jest.mocked(createSessionToken).mockResolvedValue({ code: 20000, message: '', request_id: 'r', data: { token: 'test', expires_at: 999 } });
});

it('空会话和普通历史不查配置；首次向上翻页出现副屏才查，刷新复用结果', async () => {
  const { provider } = createProvider();
  jest.mocked(listSessionMessages).mockResolvedValueOnce(page([]));
  await provider.loadHistory();
  expect(listBotRenderScreens).not.toHaveBeenCalled();
  jest.mocked(listSessionMessages).mockResolvedValueOnce(page(Array.from({ length: 50 }, (_, i) => ({ id: `m${i}`, timestamp: 2000-i, sender: 'plain:2', content: 'hello' }))));
  await provider.loadHistory();
  expect(listBotRenderScreens).not.toHaveBeenCalled();
  const older = page([{ id: 'older', timestamp: 1000, sender: 'bot-a:1', content: panel }]);
  jest.mocked(listSessionMessages).mockResolvedValue(older);
  const pending = deferred();
  jest.mocked(listBotRenderScreens).mockReturnValue(pending.promise);
  const loaded = jest.fn();
  const loading = provider.loadMoreHistory().then(loaded);
  await flush();
  expect(listBotRenderScreens).toHaveBeenCalledWith('bot-a', '1');
  expect(loaded).not.toHaveBeenCalled();
  pending.resolve(screens);
  await loading;
  expect(loaded).toHaveBeenCalledWith(expect.arrayContaining([expect.objectContaining({ content: panel })]));
  expect(getLibraryCdn('lib')).toBe('/lib.js');
  await provider.loadHistory();
  expect(listBotRenderScreens).toHaveBeenCalledTimes(1);
});

it.each([false, true])('首次实时副屏先补配置，完成帧不抢跑（hydration=%s）', async (buffered) => {
  const { provider, inner } = createProvider();
  const delivered: string[] = [];
  provider.onMessage = (msg) => delivered.push(`${msg.id}:${msg.content}`);
  provider.onComplete = () => delivered.push('complete');
  await provider.connect();
  if (!buffered) provider.enterLiveMode();
  inner.onMessage?.(message('普通文本'));
  expect(listBotRenderScreens).not.toHaveBeenCalled();
  const pending = deferred();
  jest.mocked(listBotRenderScreens).mockReturnValue(pending.promise);
  inner.onMessage?.(message(panel));
  inner.onComplete?.([message(panel)]);
  if (buffered) provider.enterLiveMode();
  await flush();
  expect(listBotRenderScreens).toHaveBeenCalledTimes(1);
  expect(delivered).toEqual(['m1:普通文本']);
  pending.resolve(screens);
  await flush();
  expect(delivered).toEqual(['m1:普通文本', `m1:${panel}`, 'complete']);
  expect(getLibraryCdn('lib')).toBe('/lib.js');
  inner.onMessage?.(message(panel, 'bot-a:1', 'm2'));
  expect(delivered.at(-1)).toBe(`m2:${panel}`);
  expect(listBotRenderScreens).toHaveBeenCalledTimes(1);
  provider.disconnect();
});

it('切换会话后在途副屏不能将旧回调交付到新视图', async () => {
  const { provider, inner } = createProvider();
  provider.onMessage = jest.fn();
  await provider.connect();
  provider.enterLiveMode();
  const pending = deferred();
  jest.mocked(listBotRenderScreens).mockReturnValue(pending.promise);
  inner.onMessage?.(message(panel));
  await flush();
  provider.disconnect();
  pending.resolve(screens);
  await flush();
  expect(provider.onMessage).not.toHaveBeenCalled();
});


it('历史首屏只查询实际组件发送方，多条消息与失败空结果均复用', async () => {
  const { provider } = createProvider();
  jest.mocked(listSessionMessages).mockResolvedValue(page([
    { id: 'm4', timestamp: 4000, sender: 'bot-b:2', content: panel },
    { id: 'm3', timestamp: 3000, sender: 'bot-a:1', content: panel },
    { id: 'm2', timestamp: 2000, sender: 'bot-a:1', content: panel },
    { id: 'm1', timestamp: 1000, sender: 'plain:3', content: '普通文本' },
  ]));
  const error = jest.spyOn(console, 'error').mockImplementation(() => {});
  jest.mocked(listBotRenderScreens).mockImplementation(async (botId) => {
    if (botId === 'bot-b') throw new Error('not available');
    return screens;
  });
  try {
    expect(await provider.loadHistory()).toHaveLength(4);
    expect(listBotRenderScreens).toHaveBeenCalledTimes(2);
    expect(listBotRenderScreens).toHaveBeenCalledWith('bot-a', '1');
    expect(listBotRenderScreens).toHaveBeenCalledWith('bot-b', '2');
    expect(getLibraryCdn('lib')).toBe('/lib.js');
    await provider.loadHistory();
    expect(listBotRenderScreens).toHaveBeenCalledTimes(2);
  } finally {
    error.mockRestore();
  }
});
