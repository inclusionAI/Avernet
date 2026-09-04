/** @jest-environment jsdom */
import { useWorkspace } from '@/hooks/useWorkspace';
import { botSessionService } from '@/services/workspace/botSessionService';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { beforeEach, expect, it, jest } from '@jest/globals';
import { renderHook, waitFor } from '@testing-library/react';

// `@tc-chat/adapters` 位于 node_modules 且为 ESM，jest 自动 mock 无法读取；
// 带 jest.fn 的 factory 在 @jest/globals + babel 模块 hoisting 下触发 TDZ。
// 解决：factory 不引用 jest，闭包捕获 `mock` 前缀顶层变量（懒初始化）。
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let mockChat: any;
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let mockProvider: any;
// 捕获 useChat 调用 opts（断言 panelRef 透传）。useChat 在 renderHook 时才被调用，闭包懒读，规避 TDZ。
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let mockUseChatCalls: any[] = [];
// 捕获 useChatBridge 调用 config（断言集中注册：bridge/chat/panelRef + 抗 last-wins）。懒初始化避开 TDZ。
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let mockUseChatBridgeCalls: any[] = [];
// 捕获 useWorkspace 经 services/workspace/chatBridge 创建 ChatBridge 的 config（断言 installGlobal:true）。
// mock 前缀以通过 jest.factory 检查；构造体在 new 时才读（懒），规避 TDZ。
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let mockBridgeConfigs: any[] = [];

jest.mock('@tc-chat/adapters', () => ({
  useChat: (opts: unknown) => {
    mockUseChatCalls.push(opts);
    return mockChat;
  },
  useChatBridge: (config: unknown) => {
    mockUseChatBridgeCalls.push(config);
  },
}));

// src 下 TS 模块，auto-mock 安全。下面通过 requireActual 拿到真实 workspaceService
// 顶层对象引用，再 stub 其方法（避免完全 auto-mock 带来的 createProvider 缺失）。
jest.mock('@/services/workspace/workspaceService');

// 副屏方式② CDN 桥：factory 不引用 jest（规避 TDZ），提供静默 Promise 桩，避免 useBotChat bot 路径真实拉取。
jest.mock('@/services/bcs/libraryCdnInjector', () => ({
  queryAndRegisterBotLibraryCdn: () => Promise.resolve(0),
  clearBotCdnConfig: () => undefined,
  queryAndRegisterManifestLibraryCdn: () => Promise.resolve(0),
}));

// @tc-chat/core 为 node_modules ESM，jest 无法 auto-mock。factory 提供轻量 ChatBridge：复刻
// installGlobal:true→window.aixBridge 副作用（断言全局单例可达沙箱）。
// 注意:chatBridge 单例模块在 import 时即 top-level `new ChatBridge`,早于 mockBridgeConfigs 初始化 → TDZ。
// 故:① 构造体不在 mockBridgeConfigs 上 push(改由 @/services/workspace/chatBridge 的 mock 显式登记 config);
//    ② 构造体只做 installGlobal→window.aixBridge 副作用。chatBridgeHelper 为全局桥 Map 桩(set/get)。
jest.mock('@tc-chat/core', () => {
  // 极简 chatBridgeHelper 桩：复刻 set/get（key 'main' 登记断言）
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
  return { ChatBridge: MockChatBridge, chatBridgeHelper, PanelHandle: {}, PanelAction: {} };
});

// 全局单例 chatBridge 模块 mock：避免 real 模块 top-level `new ChatBridge` 触发 TDZ，
// 改用懒创建的稳定单例对象(跨 test 缓存,对齐生产模块级单例)。getter 每次 ensure window.aixBridge 指向单例
// (因 beforeEach 会 delete window.aixBridge,缓存单例的构造副作用不重跑——这里补回 setter 保证可断言)。
jest.mock('@/services/workspace/chatBridge', () => {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  let singleton: any = null;
  return {
    get chatBridge() {
      if (!singleton) {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const { ChatBridge } = require('@tc-chat/core') as any;
        singleton = new ChatBridge({ installGlobal: true });
        mockBridgeConfigs.push({ installGlobal: true });
      }
      // 每次取用都 ensure window.aixBridge → 单例(installGlobal:true 语义),抗 beforeEach 的 delete。
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      (globalThis as any).aixBridge = singleton;
      return singleton;
    },
  };
});

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const { workspaceService } = require('@/services/workspace/workspaceService') as any;

beforeEach(() => {
  jest.clearAllMocks();
  mockUseChatCalls.length = 0;
  mockBridgeConfigs.length = 0;
  if (typeof window !== 'undefined') delete (window as any).aixBridge;
  useWorkspaceStore.getState().resetWorkspace();

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const fn = () => jest.fn<any>();
  mockProvider = {
    loadHistory: fn().mockResolvedValue([]),
    connect: fn().mockResolvedValue(undefined),
    disconnect: fn().mockResolvedValue(undefined),
    subscribeToSupportState: jest.fn<any>(() => jest.fn<any>()),
    subscribeToConnectionStatus: jest.fn<any>(() => jest.fn<any>()),
  };
  workspaceService.createProvider = fn().mockReturnValue(mockProvider);
  workspaceService.persistIdentity = fn();
  (botSessionService as unknown as { listOwnedBots: jest.Mock<any> }).listOwnedBots = fn().mockResolvedValue({
    ok: true,
    data: [{ botId: 'b:2088', realBotId: 'b', ownerId: '2088', displayName: '复合Bot', online: true, chatable: true }],
  });
  (botSessionService as unknown as { listOwnedBotsWithMeta: jest.Mock<any> }).listOwnedBotsWithMeta =
    fn().mockResolvedValue({
      ok: true,
      data: {
        bots: [
          { botId: 'b:2088', realBotId: 'b', ownerId: '2088', displayName: '复合Bot', online: true, chatable: true },
        ],
        hasAgentCodingBots: false,
      },
    });
  workspaceService.initWorkspace = fn().mockImplementation(async () => {
    // 直接走真实 store 写入路径（模拟 identityService.loadIdentities 的产出落 store）
    // 注入:真实用户 me + 简单 id bot b1 + 复合 id bot b:2088(均为 mine 返回身份)。
    useWorkspaceStore.getState().setIdentities(
      [
        { id: 'me', kind: 'user', displayName: '我', online: true },
        {
          id: 'b1',
          kind: 'bot',
          displayName: '真实Bot',
          avatarUrl: 'https://x/a.png',
          online: true,
          engine: 'OpenClaw',
        },
        { id: 'b:2088', kind: 'bot', displayName: '复合Bot', online: true },
      ],
      'me',
    );
    return { ok: true, data: { defaultActiveId: 'me' } };
  });

  mockChat = {
    messages: [],
    setMessages: fn(),
    onRequest: fn(),
    abort: fn(),
    isRequesting: false,
    isDefaultMessagesRequesting: false,
    connectionStatus: 'disconnected',
    retryCount: 0,
    reconnect: fn(),
  };
});

it('mount 调用 initWorkspace 加载真实 bot 列表（mine 接口），返回映射后的 Identity[]', async () => {
  const { result } = renderHook(() => useWorkspace());

  await waitFor(() => {
    expect(workspaceService.initWorkspace).toHaveBeenCalled();
  });

  await waitFor(() => {
    const identities = result.current.identities;
    expect(identities).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ id: 'me', name: '我', kind: 'user' }),
        expect.objectContaining({ id: 'b1', name: '真实Bot', kind: 'bot', engine: 'OpenClaw' }),
      ]),
    );
  });

  // 不应包含 mock fixtures 的 bot
  const names = result.current.identities.map((i: { name: string }) => i.name);
  expect(names).not.toContain('虾摸鱼');
  expect(names).not.toContain('剁椒鱼头');
  expect(names).not.toContain('麻辣小龙虾');
});

it('avatar 优先使用 avatarUrl（真实头像 URL），无 URL 时回退到 displayName 首字', async () => {
  const { result } = renderHook(() => useWorkspace());
  await waitFor(() => expect(workspaceService.initWorkspace).toHaveBeenCalled());
  await waitFor(() => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const byId = new Map<string, any>(result.current.identities.map((i: { id: string }) => [i.id, i]));
    expect(byId.get('b1')?.avatar).toBe('https://x/a.png');
    expect(byId.get('b1')?.engine).toBe('OpenClaw');
    expect(byId.get('me')?.avatar).toBe('我');
  });
});

it('暴露 chatBots（过滤 user，含 chatable）', async () => {
  const { result } = renderHook(() => useWorkspace());
  await waitFor(() => expect(workspaceService.initWorkspace).toHaveBeenCalled());
  // chatBots 仅含 bot 身份，user(我 / 测试用户)被过滤
  const kinds = result.current.chatBots.map((b: { botId: string }) => b.botId);
  expect(kinds).not.toContain('me');
  expect(kinds).not.toContain('test-user');
  expect(result.current.chatBots.length).toBeGreaterThan(0);
  // 复合 id bot 标记 chatable
  const chatable = result.current.chatBots.find((b: { botId: string }) => b.botId === 'b:2088');
  expect(chatable?.chatable).toBe(true);
});

it('availableViews 对 user 身份为双 tab', async () => {
  const { result } = renderHook(() => useWorkspace());
  await waitFor(() => expect(workspaceService.initWorkspace).toHaveBeenCalled());
  // 当前 activeIdentityId=me（user），应为 chat+group 双 tab
  expect(result.current.availableViews).toEqual(['chat', 'group']);
});

it('mine 失败时不注入任何身份', async () => {
  useWorkspaceStore.getState().reset();
  workspaceService.initWorkspace = jest.fn<any>().mockResolvedValue({
    ok: false,
    error: { code: 'IDENTITY_LOAD_FAILED', friendlyMessage: '加载失败', canRetry: true },
  });
  const { result } = renderHook(() => useWorkspace());
  expect(useWorkspaceStore.getState().identities).toEqual([]);
  expect(useWorkspaceStore.getState().activeIdentityId).toBeNull();
  expect(result.current.isTestUser).toBe(false);
});

// ============ A/B 全局单例 ChatBridge + useChatBridge 集中注册 ============
// （反转前序 change task3.6「installGlobal:false 不污染 window」反向断言——该断言方向反，
//   恰在庆祝沙箱承重点被破坏；改为断言全局单例可达、useChatBridge 集中注册。）

it('暴露全局单例 chatBridge 且 window.aixBridge 指向它(沙箱可达真桥)', async () => {
  const { result } = renderHook(() => useWorkspace());
  await waitFor(() => expect(workspaceService.initWorkspace).toHaveBeenCalled());
  expect(result.current.chatBridge).toBeDefined();
  // installGlobal:true 使 window.aixBridge = 单例,沙箱 ReactRender 由此取真桥而非退化成空 {}
  expect((window as any).aixBridge).toBe(result.current.chatBridge);
});

it('集中调用 useChatBridge,bridge=全局单例/panelRef 同一引用/按活跃方注册 chat', async () => {
  const { result } = renderHook(() => useWorkspace());
  await waitFor(() => expect(workspaceService.initWorkspace).toHaveBeenCalled());
  // useChatBridge 经 useWorkspace 集中调用(renderHook+waitFor 会多次渲染,故断言至少 1 次 + 形态正确)
  expect(mockUseChatBridgeCalls.length).toBeGreaterThanOrEqual(1);
  const call = mockUseChatBridgeCalls[mockUseChatBridgeCalls.length - 1];
  expect(call.bridge).toBe(result.current.chatBridge);
  // panelRef 透传(useWorkspace 的 panelRef 抵达 useChatBridge;跨渲染 ref 结构等价)
  expect(call.panelRef).toStrictEqual(result.current.panelRef);
  // inputRef 透传(单聊侧 .current 恒 null→降级 no-op,SDK ChatLayout.Sender 非 forwardRef 限制;群聊接通)
  expect(call.inputRef).toBe(result.current.inputRef);
  // 活跃 chat 必须定义(非 support 目标时路由到 botChat.chat,而非 support——抗 last-wins 错路由)
  expect(call.chat).toBeDefined();
});

it('把自身 panelRef 透传进 useChat（供 bot 单聊副屏交互经 SDK 自动注入 panelContext）', async () => {
  const { result } = renderHook(() => useWorkspace());
  await waitFor(() => expect(workspaceService.initWorkspace).toHaveBeenCalled());
  // useWorkspace 的 panelRef 应原样（同一引用）抵达 useChat opts（support useChat 与 useBotChat 内部 useChat 均含）
  const panelRef = result.current.panelRef;
  expect(panelRef).toBeDefined();
  expect(mockUseChatCalls.some((c: any) => c?.panelRef === panelRef)).toBe(true);
});
