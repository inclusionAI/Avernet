/** @jest-environment jsdom */
import { useBotChat } from '@/pages/Workspace/hooks/useBotChat';
import { createBotChatProvider } from '@/services/workspace/botChatProvider';
import type { BotChatSessionView, ChatBotView } from '@/services/workspace/botSessionService';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { beforeEach, expect, it, jest } from '@jest/globals';
import { act, renderHook, waitFor } from '@testing-library/react';

const bot: ChatBotView = { botId: 'b:1', realBotId: 'b', ownerId: '1', displayName: 'B', online: true, chatable: true };
const session: BotChatSessionView = {
  sessionId: 's1',
  botId: 'b:1',
  title: 't',
  messageCount: 0,
  gmtModified: '',
  gmtCreate: '',
};

const mockUseChatCalls: any[] = [];
const mockUseChatBridgeCalls: any[] = [];
let mockChat: any;
let mockProvider: any;

jest.mock('@tc-chat/adapters', () => ({
  useChat: (opts: unknown) => {
    mockUseChatCalls.push(opts);
    return mockChat;
  },
  useChatBridge: (config: unknown) => {
    mockUseChatBridgeCalls.push(config);
  },
}));
jest.mock('@/services/workspace/botChatProvider');
// 副屏方式② CDN 桥：bare auto-mock（factory 不引用 jest，规避 TDZ）；运行时用 require 取出 jest.fn 断言。
jest.mock('@/services/bcs/libraryCdnInjector');

const mockedFactory = createBotChatProvider as unknown as jest.Mock;
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const cdnModule: any = require('@/services/bcs/libraryCdnInjector');

beforeEach(() => {
  jest.clearAllMocks();
  mockUseChatCalls.length = 0;
  mockUseChatBridgeCalls.length = 0;
  cdnModule.queryAndRegisterBotLibraryCdn.mockResolvedValue(1);
  useWorkspaceStore.getState().resetWorkspace();
  useWorkspaceStore.setState({ activeIdentityId: 'human-1' });
  const fn = () => jest.fn<any>();
  mockProvider = {
    loadHistory: fn().mockResolvedValue([]),
    connect: fn().mockResolvedValue(undefined),
    disconnect: fn().mockResolvedValue(undefined),
    reconnect: fn().mockResolvedValue(undefined),
    request: fn().mockResolvedValue(undefined),
    stop: fn(),
    abort: fn(),
    subscribeToSupportState: jest.fn<any>(() => jest.fn<any>()),
    subscribeToConnectionStatus: jest.fn<any>(() => jest.fn<any>()),
  };
  mockedFactory.mockReturnValue(mockProvider);
  mockChat = {
    messages: [],
    setMessages: jest.fn(),
    onRequest: jest.fn(),
    abort: jest.fn(),
    isRequesting: false,
    isDefaultMessagesRequesting: false,
    connectionStatus: 'disconnected',
  };
});

it('仅当 bot 与 session 齐全时创建 provider', () => {
  renderHook(() => useBotChat(bot, session));
  expect(mockedFactory).toHaveBeenCalledWith({ bot, userId: 'human-1', sessionId: 's1' });
});

it('切换 session 触发 connect + loadHistory', async () => {
  mockProvider.loadHistory.mockResolvedValue([{ id: 'm1' }]);
  renderHook(() => useBotChat(bot, session));
  await waitFor(() => expect(mockProvider.connect).toHaveBeenCalled());
  await waitFor(() => expect(mockProvider.loadHistory).toHaveBeenCalled());
});

it('切换到新会话(sessionId 变化)先清空副屏再 connect,避免旧会话副屏内容叠加(对齐 useGroupChat connect effect)', async () => {
  const closePanelForce = jest.fn();
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const panelRef: any = { current: { closePanelForce } };
  const sessionA: BotChatSessionView = { ...session, sessionId: 's-a' };
  const sessionB: BotChatSessionView = { ...session, sessionId: 's-b' };
  const { rerender } = renderHook(({ s }: { s: BotChatSessionView }) => useBotChat(bot, s, panelRef), {
    initialProps: { s: sessionA },
  });
  await waitFor(() => expect(mockProvider.connect).toHaveBeenCalledTimes(1));
  const callsAfterMount = closePanelForce.mock.calls.length;
  rerender({ s: sessionB });
  await waitFor(() => expect(mockProvider.connect).toHaveBeenCalledTimes(2));
  // 切换会话必须清空副屏 tab:旧会话副屏不会残留叠加到新会话
  expect(closePanelForce.mock.calls.length).toBeGreaterThan(callsAfterMount);
});

it('重复点击同一会话(historyRefreshNonce 递增)不清空副屏,保留当前会话副屏内容', async () => {
  const closePanelForce = jest.fn();
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const panelRef: any = { current: { closePanelForce } };
  renderHook(() => useBotChat(bot, session, panelRef));
  await waitFor(() => expect(mockProvider.connect).toHaveBeenCalledTimes(1));
  const callsAfterMount = closePanelForce.mock.calls.length;
  act(() => useWorkspaceStore.getState().bumpHistoryRefresh());
  // 同一会话仅重载历史(connect effect deps 不含 nonce)→ 不清副屏
  expect(closePanelForce.mock.calls.length).toBe(callsAfterMount);
});

it('historyRefreshNonce 递增时(重复点击同一会话)重新拉取历史消息', async () => {
  renderHook(() => useBotChat(bot, session));
  await waitFor(() => expect(mockProvider.loadHistory).toHaveBeenCalledTimes(1));
  act(() => useWorkspaceStore.getState().bumpHistoryRefresh());
  await waitFor(() => expect(mockProvider.loadHistory).toHaveBeenCalledTimes(2));
  // 同一会话重载历史不应触发重连
  expect(mockProvider.connect).toHaveBeenCalledTimes(1);
});

it('send 调用 chat.onRequest 并透传 content/sessionId', () => {
  const { result } = renderHook(() => useBotChat(bot, session));
  result.current.send('hi');
  expect(mockChat.onRequest).toHaveBeenCalledWith(expect.objectContaining({ content: 'hi', sessionId: 's1' }));
});

it('send 用 fileRefDisplay 富化本地乐观消息,但发送 content 保持 canonical', () => {
  const { result } = renderHook(() => useBotChat(bot, session));
  const content = '<file-ref insert_id="ins_1"></file-ref>看下这个';
  result.current.send(content, {
    resourceReferences: [{ type: 'file', resource_id: 'sr_1', insert_id: 'ins_1' }],
    promptFileRefs: [{ resource_id: 'sr_1', insert_id: 'ins_1' }],
    fileRefDisplay: [{ insert_id: 'ins_1', name: '报告.pdf' }],
  });
  expect(mockChat.onRequest).toHaveBeenCalledWith(
    expect.objectContaining({
      content,
      userMessage: expect.objectContaining({
        content: '<file-ref insert_id="ins_1" name="报告.pdf"></file-ref>看下这个',
      }),
      resourceReferences: [{ type: 'file', resource_id: 'sr_1', insert_id: 'ins_1' }],
      promptFileRefs: [{ resource_id: 'sr_1', insert_id: 'ins_1' }],
    }),
  );
});

// ============ F1(a) 单聊 Bot CDN 数据桥 ============

const botB: ChatBotView = {
  botId: 'b:2',
  realBotId: 'b2',
  ownerId: '1',
  displayName: 'B2',
  online: true,
  chatable: true,
};

it('进入会话按 botId 拉取并注册 Bot 副屏 CDN（方式②数据桥）', async () => {
  renderHook(() => useBotChat(bot, session));
  await waitFor(() => expect(cdnModule.queryAndRegisterBotLibraryCdn).toHaveBeenCalledWith('b:1'));
});

it('允许业务页面注入 Bot 副屏 CDN 加载器', async () => {
  const loadBotLibraryCdn = jest.fn<(botId: string) => Promise<number>>().mockResolvedValue(1);
  renderHook(() => useBotChat(bot, session, undefined, loadBotLibraryCdn));
  await waitFor(() => expect(loadBotLibraryCdn).toHaveBeenCalledWith('b:1'));
  expect(cdnModule.queryAndRegisterBotLibraryCdn).not.toHaveBeenCalled();
});

it('切换 Bot 先清旧 botId CDN scope 再查新 botId', async () => {
  const { rerender } = renderHook(({ b }: { b: ChatBotView }) => useBotChat(b, session), {
    initialProps: { b: bot },
  });
  await waitFor(() => expect(cdnModule.queryAndRegisterBotLibraryCdn).toHaveBeenCalledWith('b:1'));
  rerender({ b: botB });
  // effect cleanup 先于下一 effect 运行：先 clearBotCdnConfig('b:1')，再 queryAndRegisterBotLibraryCdn('b:2')
  expect(cdnModule.clearBotCdnConfig).toHaveBeenCalledWith('b:1');
  await waitFor(() => expect(cdnModule.queryAndRegisterBotLibraryCdn).toHaveBeenCalledWith('b:2'));
});

it('CDN 拉取失败时静默降级，不阻塞会话、不抛错', async () => {
  cdnModule.queryAndRegisterBotLibraryCdn.mockRejectedValue(new Error('boom'));
  expect(() => renderHook(() => useBotChat(bot, session))).not.toThrow();
  // 让微任务落地，确认被 .catch 吞掉、不冒泡为 unhandled rejection
  await waitFor(() => expect(cdnModule.queryAndRegisterBotLibraryCdn).toHaveBeenCalled());
});

// ============ F1(b) panelRef 透传 SDK useChat ============

it('panelRef 透传给 SDK useChat（供自动 flushContext→panelContext 注入 bot 请求）', () => {
  const panelRef = { current: null };
  renderHook(() => useBotChat(bot, session, panelRef));
  expect(mockUseChatCalls[0].panelRef).toBe(panelRef);
});

it('未传 panelRef 时 useChat 收到 undefined panelRef（保持可选，不强行造 ref）', () => {
  renderHook(() => useBotChat(bot, session));
  expect(mockUseChatCalls[0].panelRef).toBeUndefined();
});

// ============ D2 集中注册:useBotChat 不自行接 useChatBridge ============
// （防回归:若在 useBotChat 内接 useChatBridge,会与 useWorkspace support 注册在同一全局桥上 last-wins
//   覆盖,导致 aixcore 卡片 bridge.sendMessage 错路由到 support。集中注册只在编排层 useWorkspace。) */

it('useBotChat 不自行调用 useChatBridge（集中注册由编排层 useWorkspace 承担）', () => {
  renderHook(() => useBotChat(bot, session, { current: null }));
  expect(mockUseChatBridgeCalls.length).toBe(0);
});
