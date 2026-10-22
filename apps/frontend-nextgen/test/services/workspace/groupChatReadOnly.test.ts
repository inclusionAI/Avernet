import * as api from '@/services/backendApi/collaboration/sessionController';
import { createGroupChatProvider } from '@/services/workspace/groupChatProvider';
import { beforeEach, expect, it, jest } from '@jest/globals';

jest.mock('@/services/backendApi/collaboration/sessionController');
jest.mock('@tc-chat/adapters', () => ({ GroupChatProvider: class {} }));
const sc = api as unknown as Record<string, jest.Mock<any>>;

function setup(liveEnabled = false) {
  const inner: any = {
    isConnected: false,
    transport: { send: jest.fn<any>().mockResolvedValue(undefined), onMessage: jest.fn() },
    request: jest.fn<any>().mockResolvedValue(undefined),
    connect: jest.fn<any>(),
    disconnect: jest.fn(() => { inner.isConnected = false; }),
    subscribeToConnectionStatus: jest.fn(() => () => {}),
  };
  inner.connect.mockImplementation(async () => { inner.isConnected = true; });
  const factory = jest.fn(() => inner);
  const provider = createGroupChatProvider({
    sessionId: 's1', groupId: 'g1', identityId: 'human_1', liveEnabled,
    wsOrigin: 'wss://example.test', createSdkProvider: factory,
  });
  return { provider, inner, factory };
}

beforeEach(() => {
  jest.clearAllMocks();
  sc.createSessionToken.mockResolvedValue({ data: { token: 'token' } });
  sc.listSessionMessages.mockResolvedValue({ data: [] });
});

it('loads, paginates and refreshes HTTP history without a token or SDK', async () => {
  const { provider, factory } = setup();
  const rows = Array.from({ length: 50 }, (_, i) => ({
    id: `m${i}`, sender: 'human_1', message_type: 'human', role: 'user',
    content: `history ${i}`, timestamp: 1_700_000_000_100 - i,
  }));
  sc.listSessionMessages.mockResolvedValueOnce({ data: rows });
  expect(await provider.loadHistory()).toHaveLength(50);
  expect(provider.hasMoreHistory).toBe(true);
  await provider.loadMoreHistory();
  await provider.loadHistory();
  expect(sc.listSessionMessages).toHaveBeenCalledTimes(3);
  expect(sc.listSessionMessages.mock.calls[1][1]).toMatchObject({ before: '1700000000051' });
  expect(provider.supportState.phase).toBe('ready');
  expect(sc.createSessionToken).not.toHaveBeenCalled();
  expect(factory).not.toHaveBeenCalled();
});

it('blocks all live entry points while absent and allows joining later', async () => {
  const { provider, factory } = setup();
  await provider.connect();
  await provider.reconnect();
  await expect(provider.request({ content: 'hi', sessionId: 's1' })).rejects.toThrow('未加入');
  await expect(provider.abortBot('bot_1')).rejects.toThrow('未加入');
  expect(sc.createSessionToken).not.toHaveBeenCalled();
  expect(factory).not.toHaveBeenCalled();
  provider.setLiveEnabled(true);
  await provider.connect();
  expect(provider.isConnected).toBe(true);
  provider.setLiveEnabled(false);
  expect(provider.isConnected).toBe(false);
  await provider.reconnect();
  expect(factory).toHaveBeenCalledTimes(1);
});

it('discards a token that completes after leaving', async () => {
  let finish!: (value: unknown) => void;
  sc.createSessionToken.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
  const { provider, factory } = setup(true);
  const connecting = provider.connect();
  provider.setLiveEnabled(false);
  finish({ data: { token: 'late' } });
  await connecting;
  expect(factory).not.toHaveBeenCalled();
  expect(provider.supportState.phase).not.toBe('error');
});

it('does not send after leaving during an implicit connection', async () => {
  const { provider, inner } = setup(true);
  let finish!: () => void;
  inner.connect.mockImplementationOnce(() => new Promise<void>((resolve) => { finish = resolve; }));
  const sending = provider.request({ content: 'hi', sessionId: 's1' });
  for (let i = 0; i < 8; i++) await Promise.resolve();
  expect(inner.connect).toHaveBeenCalled();
  provider.setLiveEnabled(false);
  finish();
  await expect(sending).rejects.toThrow();
  expect(inner.request).not.toHaveBeenCalled();
});

it('ignores stale incoming scope events after leaving', async () => {
  const { provider, inner, factory } = setup(true);
  await provider.connect();
  provider.setLiveEnabled(false);
  inner.transport.onMessage({ type: 'event', event: 'view_scope_changed', payload: {} });
  await Promise.resolve();
  expect(factory).toHaveBeenCalledTimes(1);
  expect(provider.isConnected).toBe(false);
});

it('keeps a newly joined connection when an older token finishes later', async () => {
  let finish!: (value: unknown) => void;
  sc.createSessionToken.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
  const { provider, factory } = setup(true);
  const oldConnect = provider.connect();
  provider.setLiveEnabled(false);
  provider.setLiveEnabled(true);
  await provider.connect();
  finish({ data: { token: 'stale' } });
  await oldConnect;
  expect(factory).toHaveBeenCalledTimes(1);
  expect(provider.isConnected).toBe(true);
  await provider.request({ content: 'joined', sessionId: 's1' });
  expect(sc.createSessionToken).toHaveBeenCalledTimes(2);
});

it('surfaces HTTP history errors and permits HTTP-only retry', async () => {
  const { provider, factory } = setup();
  sc.listSessionMessages.mockRejectedValueOnce(new Error('403'));
  await expect(provider.loadHistory()).rejects.toThrow('403');
  expect(provider.supportState).toEqual({ phase: 'error', error: '403' });
  await expect(provider.loadHistory()).resolves.toEqual([]);
  expect(provider.supportState.phase).toBe('ready');
  expect(sc.createSessionToken).not.toHaveBeenCalled();
  expect(factory).not.toHaveBeenCalled();
});
