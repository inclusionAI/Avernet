/** @jest-environment jsdom */
import type { SessionView } from '@/domain/collaboration';
import { useGroupChat } from '@/pages/Workspace/hooks/useGroupChat';
import { createGroupChatProvider } from '@/services/workspace/groupChatProvider';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { beforeEach, expect, it, jest } from '@jest/globals';
import { act, renderHook, waitFor } from '@testing-library/react';

// Provider 需要 groupId（BCS connect 帧的 group_id）；Hook 入参为 SessionView。
const session: SessionView = {
  sessionId: 's1',
  groupId: 'g1',
  title: 't',
  kind: 'chat',
  status: 'running',
  participants: [],
  lastMessageAt: 0,
  createdAt: 0,
  favorite: false,
};

// `@tc-chat/adapters` 位于 node_modules 且为 ESM，jest 自动 mock 无法读取它；
// 而带 `jest.requireActual`/`jest.fn()` 的 factory 又会在 @jest/globals + babel 模块
// hoisting 下触发 TDZ（与 useGroupSessions.test 中的限制一致）。
// 解决：factory 不引用 jest，仅闭包捕获顶层 `mock` 前缀变量——jest 允许在 factory
// 中引用以 `mock` 开头的变量（约定其会在 require 前懒初始化）。factory 在被 require
// 时才执行（晚于模块顶层求值），闭包变量此时已就绪。
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const mockUseChatCalls: any[] = [];
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let mockChat: any;
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let mockProvider: any;
// 捕获 useChatBridge 调用 config（断言集中注册）。懒初始化避开 TDZ。
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let mockUseChatBridgeCalls: any[] = [];

jest.mock('@tc-chat/adapters', () => ({
  useChat: (opts: unknown) => {
    mockUseChatCalls.push(opts);
    return mockChat;
  },
  useChatBridge: (config: unknown) => {
    mockUseChatBridgeCalls.push(config);
  },
}));

// src 下 TS 模块，bare auto-mock 安全（无 jest 引用），createGroupChatProvider
// 被替换为 jest.fn()，我们在 beforeEach 里 mockReturnValue(mockProvider)。
jest.mock('@/services/workspace/groupChatProvider');

// @tc-chat/core 为 node_modules ESM，jest 无法 auto-mock。factory 提供轻量 ChatBridge：复刻
// installGlobal:true→globalThis.aixBridge 副作用（断言全局单例可达沙箱）。
// 注意:chatBridge 单例模块 top-level `new ChatBridge` 早于 mockBridgeConfigs 初始化→TDZ;
// 构造体不 push(改由 @/services/workspace/chatBridge mock 登记),仅做 installGlobal 副作用。chatBridgeHelper 桩。
jest.mock('@tc-chat/core', () => {
  const bridges = new Map();
  const chatBridgeHelper = {
    get: (k: string) => bridges.get(k),
    set: (k: string, b: unknown) => bridges.set(k, b),
  };
  class MockChatBridge {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    constructor(config: any) {
      if (config?.installGlobal) {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        (globalThis as any).aixBridge = this;
      }
    }
    emitPanelEvent() {}
    openPanelTab() {}
    closePanelForce() {}
    closePanel() {}
    openPanel() {}
    submit() {}
    abort() {}
    getMessages() {
      return [];
    }
    getIsRequesting() {
      return false;
    }
    getInputRef() {
      return null;
    }
  }
  return { ChatBridge: MockChatBridge, chatBridgeHelper, PanelHandle: {}, PanelAction: {}, ChatMessage: {} };
});

// 全局单例 chatBridge 模块 mock:避免 real 模块 top-level `new ChatBridge` TDZ;懒创建稳定单例(跨 test 缓存),
// 每次 getter ensure globalThis.aixBridge→单例(抗 beforeEach delete)。
jest.mock('@/services/workspace/chatBridge', () => {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  let singleton: any = null;
  return {
    get chatBridge() {
      if (!singleton) {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const { ChatBridge } = require('@tc-chat/core') as any;
        singleton = new ChatBridge({ installGlobal: true });
      }
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      (globalThis as any).aixBridge = singleton;
      return singleton;
    },
  };
});

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const mockedProviderFactory = createGroupChatProvider as unknown as jest.Mock<any>;

beforeEach(() => {
  jest.clearAllMocks();
  mockUseChatCalls.length = 0;
  mockUseChatBridgeCalls.length = 0;
  // aixBridge 由 @/services/workspace/chatBridge mock 的 getter 在 useGroupChat 渲染时 ensure 指回单例,
  // 不在此 delete(单例跨 test 缓存,delete 后下一测试构造副作用不重跑);保留 resetWorkspace。
  useWorkspaceStore.getState().resetWorkspace();
  useWorkspaceStore.setState({ activeIdentityId: 'me' });

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const fn = () => jest.fn<any>();
  mockProvider = {
    loadHistory: fn().mockResolvedValue([]),
    loadMoreHistory: fn().mockResolvedValue([]),
    connect: fn().mockResolvedValue(undefined),
    disconnect: fn().mockResolvedValue(undefined),
    reconnect: fn().mockResolvedValue(undefined),
    request: fn().mockResolvedValue(undefined),
    stop: fn(),
    abort: fn(),
    beginHistoryHydration: fn(),
    enterLiveMode: fn(),
    hasMoreHistory: false,
    isLoadingMoreHistory: false,
    subscribeToSupportState: jest.fn<any>(() => jest.fn<any>()),
    subscribeToConnectionStatus: jest.fn<any>(() => jest.fn<any>()),
  };
  mockedProviderFactory.mockReturnValue(mockProvider);

  mockChat = {
    messages: [],
    setMessages: jest.fn(),
    setMessage: jest.fn(),
    removeMessage: jest.fn(),
    onRequest: jest.fn(),
    onReload: jest.fn(),
    abort: jest.fn(),
    isRequesting: false,
    isDefaultMessagesRequesting: false,
    connectionStatus: 'disconnected',
    retryCount: 0,
    reconnect: jest.fn(),
    isConnected: false,
    isConnecting: false,
  };
});

it('mount connects to the provider with conversationKey=sessionId, unmount disconnects', async () => {
  const { unmount } = renderHook(() => useGroupChat(session));
  await waitFor(() => {
    expect(mockUseChatCalls.length).toBeGreaterThan(0);
    const opts = mockUseChatCalls[0];
    expect(opts.conversationKey).toBe('s1');
    // 不再使用 SDK defaultMessages；改为显式 loadHistory + setMessages
    expect(opts.defaultMessages).toBeUndefined();
  });
  expect(mockedProviderFactory).toHaveBeenCalledWith({ sessionId: 's1', groupId: 'g1', identityId: 'me' });
  expect(mockProvider.connect).toHaveBeenCalled();

  unmount();
  expect(mockProvider.disconnect).toHaveBeenCalled();
});

it('mount calls provider.loadHistory and sets messages into chat', async () => {
  const fakeMessages = [{ id: 'm1', role: 'user', content: 'hi', status: 'history' }];
  mockProvider.loadHistory.mockResolvedValue(fakeMessages);
  renderHook(() => useGroupChat(session));
  await waitFor(() => expect(mockProvider.loadHistory).toHaveBeenCalled());
  await waitFor(() => expect(mockChat.setMessages).toHaveBeenCalledWith(fakeMessages));
  expect(mockProvider.beginHistoryHydration.mock.invocationCallOrder[0]).toBeLessThan(
    mockProvider.connect.mock.invocationCallOrder[0],
  );
  expect(mockProvider.enterLiveMode).toHaveBeenCalled();
});

it('leaves hydration buffering when initialization fails so retry can start cleanly', async () => {
  mockProvider.loadHistory.mockRejectedValue(new Error('history failed'));

  renderHook(() => useGroupChat(session));

  await waitFor(() => expect(mockProvider.loadHistory).toHaveBeenCalled());
  await waitFor(() => expect(mockProvider.enterLiveMode).toHaveBeenCalled());
});

it('exposes group bootstrap processing until the matching run message appears', async () => {
  useWorkspaceStore.getState().setPendingGroupBootstrap({
    groupId: 'g1',
    sessionId: 's1',
    run: {
      runId: 'run-manager',
      botUuid: 'manager-bot',
      activityKind: 'group_bootstrap',
      state: 'running',
      startedAt: '2026-08-31T00:00:00Z',
    },
  });
  const { result, rerender } = renderHook(() => useGroupChat(session));
  expect(result.current.groupBootstrapProcessing).toBe(true);

  mockChat.messages = [
    {
      id: 'bcs-run:run-manager:manager-bot',
      role: 'assistant',
      content: '开始处理',
      status: 'streaming',
      extra: { runId: 'run-manager', botUuid: 'manager-bot' },
    },
  ];
  rerender();
  await waitFor(() => expect(useWorkspaceStore.getState().pendingGroupBootstrap).toBeNull());
});

it('historyRefreshNonce 递增时（重复点击同一会话）重新拉取历史消息', async () => {
  renderHook(() => useGroupChat(session));
  await waitFor(() => expect(mockProvider.loadHistory).toHaveBeenCalledTimes(1));
  act(() => useWorkspaceStore.getState().bumpHistoryRefresh());
  await waitFor(() => expect(mockProvider.loadHistory).toHaveBeenCalledTimes(2));
  // 重载也使用同一可取消初始化流程，确保 history 与 WS 事件有确定顺序。
  expect(mockProvider.connect).toHaveBeenCalledTimes(2);
});

it('send calls chat.onRequest with sessionId and trimmed text', () => {
  const { result } = renderHook(() => useGroupChat(session));
  result.current.send(' hello ');
  expect(mockChat.onRequest).toHaveBeenCalledWith(
    expect.objectContaining({
      content: 'hello',
      sessionId: 's1',
      userMessage: expect.objectContaining({ content: 'hello' }),
    }),
  );
});

it('send forwards mention bot ids into chat.onRequest', () => {
  const { result } = renderHook(() => useGroupChat(session));
  result.current.send(' @ALL 你们呢 ', ['bot-a', 'bot-b']);
  expect(mockChat.onRequest).toHaveBeenCalledWith(
    expect.objectContaining({
      content: '@ALL 你们呢',
      sessionId: 's1',
      mentions: ['bot-a', 'bot-b'],
      userMessage: expect.objectContaining({ content: '@ALL 你们呢' }),
    }),
  );
});

it('send forwards image attachments into chat.onRequest and userMessage extra for local echo', () => {
  const { result } = renderHook(() => useGroupChat(session));
  const attachments = [
    {
      attachment_id: 'f1',
      type: 'image' as const,
      file_name: 'a.png',
      mime_type: 'image/png',
      size: 10,
      url: 'https://share.example/f1',
      expires_at: 1700003600000,
    },
  ];
  result.current.send('看图', undefined, attachments);
  expect(mockChat.onRequest).toHaveBeenCalledWith(
    expect.objectContaining({
      content: '看图',
      sessionId: 's1',
      attachments,
      userMessage: expect.objectContaining({
        content: '看图',
        extra: expect.objectContaining({
          attachments: [
            expect.objectContaining({
              attachment_id: 'f1',
              url: '/api/v1/collaboration/sessions/s1/files/f1/content?show=true',
            }),
          ],
        }),
      }),
    }),
  );
});

it('send allows image-only message when attachments are present', () => {
  const { result } = renderHook(() => useGroupChat(session));
  const attachments = [
    {
      attachment_id: 'f1',
      type: 'image' as const,
      file_name: 'a.png',
      size: 10,
      url: 'https://share.example/f1',
    },
  ];
  result.current.send('', undefined, attachments);
  expect(mockChat.onRequest).toHaveBeenCalledWith(
    expect.objectContaining({ content: '', sessionId: 's1', attachments }),
  );
});

it('stop calls chat.abort, reconnect calls provider.reconnect', async () => {
  // 让 isRequesting=true 使 stop 真正触达 abort
  mockChat.isRequesting = true;
  const { result } = renderHook(() => useGroupChat(session));
  result.current.stop();
  expect(mockChat.abort).toHaveBeenCalled();
  await result.current.reconnect();
  expect(mockProvider.reconnect).toHaveBeenCalled();
});

it('loadMoreHistory prepends deduped older messages and syncs hasMore from provider', async () => {
  // 首屏已加载两条消息（id m1/m2），provider.hasMoreHistory=true。
  mockProvider.loadHistory.mockResolvedValue([
    { id: 'm1', role: 'user', content: 'new', status: 'history', createdAt: 200 },
    { id: 'm2', role: 'user', content: 'newer', status: 'history', createdAt: 300 },
  ]);
  mockProvider.hasMoreHistory = true;
  // loadMoreHistory 返回更早消息：m1(与已存在重复，应被去重) + m0(新)。
  // 注意 provider 返回旧→新升序：m0(ts100) 在前、m1(ts150) 在后。
  mockProvider.loadMoreHistory.mockResolvedValue([
    { id: 'm0', role: 'user', content: 'older', status: 'history', createdAt: 100 },
    { id: 'm1', role: 'user', content: 'dup', status: 'history', createdAt: 150 },
  ]);
  const { result } = renderHook(() => useGroupChat(session));
  await waitFor(() => expect(mockProvider.loadHistory).toHaveBeenCalled());
  await waitFor(() => expect(result.current.hasMoreHistory).toBe(true));

  await result.current.loadMoreHistory();

  // setMessages 收到 updater：执行后应前置去重 —— 仅 m0 新增，重复 m1 被丢弃。
  const updater = mockChat.setMessages.mock.calls.at(-1)?.[0];
  expect(typeof updater).toBe('function');
  const prev = [
    { id: 'm1', role: 'user', content: 'new', status: 'history', createdAt: 200 },
    { id: 'm2', role: 'user', content: 'newer', status: 'history', createdAt: 300 },
  ];
  const next = updater(prev);
  expect(next.map((m: { id: string }) => m.id)).toEqual(['m0', 'm1', 'm2']);

  // loadMoreHistory 完成后 isLoadingMoreHistory 归位。
  await waitFor(() => expect(result.current.isLoadingMoreHistory).toBe(false));
  expect(mockProvider.loadMoreHistory).toHaveBeenCalled();
});

// ============ A/B 全局单例 ChatBridge + useChatBridge 集中注册 ============
// （反转前序 change task3.6「installGlobal:false 不污染全局」反向断言——方向反，恰在庆祝沙箱承重点
//   被破坏；改为断言全局单例可达、useChatBridge 集中注册一次。）

it('暴露全局单例 chatBridge 且 globalThis.aixBridge 指向它(沙箱可达真桥)', () => {
  const { result } = renderHook(() => useGroupChat(session));
  expect(result.current.chatBridge).toBeDefined();
  // installGlobal:true 使 globalThis.aixBridge = 单例,沙箱 ReactRender 由此取真桥而非空 {}
  expect((globalThis as any).aixBridge).toBe(result.current.chatBridge);
});

it('useChatBridge 集中注册一次,bridge=全局单例/panelRef/chat/inputRef 形态正确', () => {
  const { result } = renderHook(() => useGroupChat(session));
  // useGroupChat 单一活跃 chat → useChatBridge 集中注册一次(非每 hook 各接)
  expect(mockUseChatBridgeCalls.length).toBeGreaterThanOrEqual(1);
  const call = mockUseChatBridgeCalls[mockUseChatBridgeCalls.length - 1];
  expect(call.bridge).toBe(result.current.chatBridge);
  expect(call.panelRef).toStrictEqual(result.current.panelRef);
  expect(call.chat).toBeDefined();
  // inputRef 透传(SenderRef ref,供卡片填输入框);群聊经 GroupChatComposer 绑定原生 Sender(forwardRef)
  expect(call.inputRef).toBe(result.current.inputRef);
});
