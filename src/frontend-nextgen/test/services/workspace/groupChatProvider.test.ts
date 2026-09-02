import * as sessionController from '@/services/backendApi/collaboration/sessionController';
import { buildGroupWsUrl, createGroupChatProvider } from '@/services/workspace/groupChatProvider';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';
import type { ChatMessage } from '@tc-chat/core';

jest.mock('@/services/backendApi/collaboration/sessionController');

// SDK 包以 ESM 发布，jest+babel-node 不转译 node_modules；这里 stub 掉默认导出的 SDK GroupChatProvider 类，
// 真实集成由 fakeSdkProvider 注入，ConnectionStatusEvent 仅作类型使用。
jest.mock('@tc-chat/adapters', () => ({
  GroupChatProvider: class MockGroupChatProvider {},
}));
const sc = sessionController as unknown as Record<string, jest.Mock<any>>;

let lastOriginalSend: jest.Mock<any>;

const fakeSdkProvider = jest.fn<any>().mockImplementation((cfg: any) => {
  const send = jest.fn<any>().mockResolvedValue(undefined);
  lastOriginalSend = send;
  const inner: any = {
    config: cfg,
    transport: { send },
    request: jest.fn<any>().mockResolvedValue(undefined),
    abort: jest.fn<any>(),
    connect: jest.fn<any>(),
    disconnect: jest.fn<any>(),
    onMessage: undefined,
    onComplete: undefined,
    onError: undefined,
    isConnected: false,
    hydrateRun: jest.fn<any>((message: ChatMessage) => message),
    beginHistoryHydration: jest.fn<any>(),
    enterLiveMode: jest.fn<any>(),
    subscribeToConnectionStatus: jest.fn<any>(() => jest.fn<any>()),
  };
  inner.connect.mockImplementation(async () => {
    inner.isConnected = true;
  });
  return inner;
});

beforeEach(() => {
  jest.resetAllMocks();
  // 重新绑定 fakeSdkProvider 的实现（resetAllMocks 会清掉 mockImplementation）
  fakeSdkProvider.mockImplementation((cfg: any) => {
    const send = jest.fn<any>().mockResolvedValue(undefined);
    lastOriginalSend = send;
    const inner: any = {
      config: cfg,
      transport: { send },
      request: jest.fn<any>().mockResolvedValue(undefined),
      abort: jest.fn<any>(),
      connect: jest.fn<any>(),
      disconnect: jest.fn<any>(),
      onMessage: undefined,
      onComplete: undefined,
      onError: undefined,
      isConnected: false,
      hydrateRun: jest.fn<any>((message: ChatMessage) => message),
      beginHistoryHydration: jest.fn<any>(),
      enterLiveMode: jest.fn<any>(),
      subscribeToConnectionStatus: jest.fn<any>(() => jest.fn<any>()),
    };
    inner.connect.mockImplementation(async () => {
      inner.isConnected = true;
    });
    return inner;
  });
});

describe('groupChatProvider', () => {
  it('buildGroupWsUrl uses wsOrigin when provided', () => {
    expect(buildGroupWsUrl({ token: 'tk', wsOrigin: 'wss://gw.example.com' })).toBe(
      'wss://gw.example.com/openapi/v1/collaboration/messages/ws?token=tk',
    );
  });

  // 根因 B'：包装层 MUST 透出 supportsConcurrentRequests = true（对齐 SDK GroupChatProvider.js:42）。
  // 否则 useChat.onRequest 在 isRequesting && supportsConcurrentRequests 时落入 chat.send 静默丢
  // （bot 回复期间桥路径卡片发送无声丢失 ws 帧）。
  it('透出 supportsConcurrentRequests = true（bot 回复期间并发消息不静默丢）', () => {
    const provider = createGroupChatProvider({ sessionId: 's1', groupId: 'g1', identityId: 'me' });
    expect(provider.supportsConcurrentRequests).toBe(true);
  });

  // 真实 GroupChatProvider：token 获取失败必须直接抛出，不重试 / 不降级 / 不构造 SDK Provider。
  it('connect: token fetch failure surfaces, does not retry or downgrade or build SDK provider', async () => {
    sc.createSessionToken.mockRejectedValue(new Error('401'));
    const provider = createGroupChatProvider({
      sessionId: 's1',
      groupId: 'g1',
      identityId: 'me',
      createSdkProvider: fakeSdkProvider as unknown as Parameters<
        typeof createGroupChatProvider
      >[0]['createSdkProvider'],
      wsOrigin: 'wss://x.test',
    });
    await expect(provider.connect()).rejects.toThrow('401');
    // 失败后允许后续重试 → initializePromise 被清空，但单次 connect 只调用一次 token 接口
    expect(sc.createSessionToken).toHaveBeenCalledTimes(1);
    // token 失败短路：不应构造 SDK Provider（无降级到匿名连接）
    expect(fakeSdkProvider).not.toHaveBeenCalled();
    // 失败必须外发 error 状态（Hook / UI 据此展示）
    expect(provider.supportState.phase).toBe('error');
    expect(provider.supportState.error).toBe('401');
  });

  it('SDK connect Promise 成功但底层未连接时，不误报为已连接', async () => {
    sc.createSessionToken.mockResolvedValue({
      code: 20000,
      message: '',
      request_id: 'r',
      data: { token: 'tk', expires_at: 999 },
    });
    const disconnectedSdk = jest.fn<any>().mockImplementation((cfg: any) => ({
      config: cfg,
      connect: jest.fn<any>().mockResolvedValue(undefined),
      disconnect: jest.fn<any>(),
      request: jest.fn<any>(),
      abort: jest.fn<any>(),
      isConnected: false,
      subscribeToConnectionStatus: jest.fn<any>(() => jest.fn<any>()),
    }));
    const provider = createGroupChatProvider({
      sessionId: 's-disconnected',
      groupId: 'g1',
      identityId: 'me',
      createSdkProvider: disconnectedSdk,
      wsOrigin: 'wss://x.test',
    });

    await expect(provider.connect()).rejects.toThrow('协作会话 WebSocket 未建立');
    expect(provider.supportState).toEqual({ phase: 'error', error: '协作会话 WebSocket 未建立。' });
  });

  // 集成层（通过 fakeSdkProvider 注入）：attach 只取一次 token，再 connect
  it('creates SDK provider once with token-loaded URL, then forwards request/stop', async () => {
    sc.createSessionToken.mockResolvedValue({
      code: 20000,
      message: '',
      request_id: 'r',
      data: { token: 'tk', expires_at: 999 },
    });
    sc.listSessionMessages.mockResolvedValue({
      code: 20000,
      message: '',
      request_id: 'r',
      data: [],
    });
    const provider = createGroupChatProvider({
      sessionId: 's1',
      groupId: 'g1',
      identityId: 'me',
      createSdkProvider: fakeSdkProvider as unknown as Parameters<
        typeof createGroupChatProvider
      >[0]['createSdkProvider'],
      wsOrigin: 'wss://x.test',
    });
    await provider.connect();
    expect(fakeSdkProvider).toHaveBeenCalledTimes(1);
    const cfg = fakeSdkProvider.mock.calls[0][0] as {
      url: string;
      currentUserId: string;
      groupId: string;
      sessionId: string;
    };
    expect(cfg.url).toBe('wss://x.test/openapi/v1/collaboration/messages/ws?token=tk');
    expect(cfg.currentUserId).toBe('me');
    expect(cfg.groupId).toBe('g1');
    expect(cfg.sessionId).toBe('s1');

    // BCS 协议：connect 携带 groupId（SDK connect 帧 = { group_id }）
    const inner = provider as unknown as { inner: { connect: jest.Mock; request: jest.Mock; abort: jest.Mock } };
    expect(inner.inner.connect).toHaveBeenCalledWith({ groupId: 'g1', sessionId: 's1' });

    await provider.request({ content: 'hi', sessionId: 's1' });
    expect(inner.inner.request).toHaveBeenCalledWith({
      query: 'hi',
      groupId: 'g1',
      sessionId: 's1',
      senderId: 'me',
      botUuid: 'human_me',
    });
    provider.stop();
    expect(inner.inner.abort).toHaveBeenCalledWith('g1');
  });

  it('human request strips sender id and forwards mention bot ids', async () => {
    sc.createSessionToken.mockResolvedValue({
      code: 20000,
      message: '',
      request_id: 'r',
      data: { token: 'tk', expires_at: 999 },
    });
    const provider = createGroupChatProvider({
      sessionId: 's1',
      groupId: 'g1',
      identityId: 'human_327325',
      createSdkProvider: fakeSdkProvider as unknown as Parameters<
        typeof createGroupChatProvider
      >[0]['createSdkProvider'],
      wsOrigin: 'wss://x.test',
    });
    await provider.connect();
    const inner = provider as unknown as { inner: { request: jest.Mock } };

    await provider.request({
      content: '@波士顿龙虾 你在干嘛',
      sessionId: 's1',
      mentions: ['20260528_udt1y38n:327325'],
    });
    expect(inner.inner.request).toHaveBeenCalledWith({
      query: '@波士顿龙虾 你在干嘛',
      groupId: 'g1',
      sessionId: 's1',
      senderId: '327325',
      botUuid: 'human_327325',
      mentions: ['20260528_udt1y38n:327325'],
    });
  });

  it('human request forwards uploaded image attachments to SDK request', async () => {
    sc.createSessionToken.mockResolvedValue({
      code: 20000,
      message: '',
      request_id: 'r',
      data: { token: 'tk', expires_at: 999 },
    });
    const provider = createGroupChatProvider({
      sessionId: 's1',
      groupId: 'g1',
      identityId: 'human_327325',
      createSdkProvider: fakeSdkProvider as unknown as Parameters<
        typeof createGroupChatProvider
      >[0]['createSdkProvider'],
      wsOrigin: 'wss://x.test',
    });
    await provider.connect();
    const inner = provider as unknown as { inner: { request: jest.Mock } };
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

    await provider.request({ content: '看图', sessionId: 's1', attachments });

    expect(inner.inner.request).toHaveBeenCalledWith({
      query: '看图',
      groupId: 'g1',
      sessionId: 's1',
      senderId: '327325',
      botUuid: 'human_327325',
      attachments,
    });
  });

  it('通过兼容适配器为 SDK connect/chat.send 帧注入当前 session', async () => {
    sc.createSessionToken.mockResolvedValue({
      code: 20000,
      message: '',
      request_id: 'r',
      data: { token: 'tk', expires_at: 999 },
    });
    const provider = createGroupChatProvider({
      sessionId: 's1',
      groupId: 'g1',
      identityId: 'me',
      createSdkProvider: fakeSdkProvider as unknown as Parameters<
        typeof createGroupChatProvider
      >[0]['createSdkProvider'],
      wsOrigin: 'wss://x.test',
    });
    await provider.connect();
    const inner = provider as unknown as {
      inner: { connect: jest.Mock; beginHistoryHydration: jest.Mock; transport: { send: jest.Mock } };
    };
    expect(inner.inner.connect).toHaveBeenCalledWith({ groupId: 'g1', sessionId: 's1' });
    expect(inner.inner.beginHistoryHydration).toHaveBeenCalled();

    await inner.inner.transport.send({
      type: 'req',
      id: 'connect-1',
      method: 'connect',
      params: { group_id: 'g1' },
    });
    expect(lastOriginalSend).toHaveBeenCalledWith(
      expect.objectContaining({
        method: 'connect',
        params: { group_id: 'g1', session_id: 's1' },
      }),
    );

    await inner.inner.transport.send({
      type: 'req',
      id: 'send-1',
      method: 'chat.send',
      params: { group_id: 'g1', sessionKey: 'main', message: 'hi' },
    });
    expect(lastOriginalSend).toHaveBeenLastCalledWith(
      expect.objectContaining({
        method: 'chat.send',
        params: { group_id: 'g1', sessionKey: 's1', session_id: 's1', message: 'hi' },
      }),
    );
  });

  it('断开后重连会重新获取 token、创建新的 SDK provider 并断开旧 provider', async () => {
    sc.createSessionToken
      .mockResolvedValueOnce({
        code: 20000,
        message: '',
        request_id: 'r1',
        data: { token: 'tk-1', expires_at: 999 },
      })
      .mockResolvedValueOnce({
        code: 20000,
        message: '',
        request_id: 'r2',
        data: { token: 'tk-2', expires_at: 1000 },
      });
    const provider = createGroupChatProvider({
      sessionId: 's-reconnect',
      groupId: 'g1',
      identityId: 'me',
      createSdkProvider: fakeSdkProvider as unknown as Parameters<
        typeof createGroupChatProvider
      >[0]['createSdkProvider'],
      wsOrigin: 'wss://x.test',
    });

    await provider.connect();
    const firstInner = (provider as unknown as { inner: { disconnect: jest.Mock } }).inner;
    await provider.reconnect();
    const secondInner = (provider as unknown as { inner: { disconnect: jest.Mock } }).inner;

    expect(sc.createSessionToken).toHaveBeenCalledTimes(2);
    expect(fakeSdkProvider).toHaveBeenCalledTimes(2);
    expect(firstInner.disconnect).toHaveBeenCalledTimes(1);
    expect(fakeSdkProvider.mock.calls[1][0].url).toBe('wss://x.test/openapi/v1/collaboration/messages/ws?token=tk-2');
    expect(secondInner).not.toBe(firstInner);
  });

  it('兼容不提供 hydration 扩展能力的 SDK 版本', async () => {
    sc.createSessionToken.mockResolvedValue({
      code: 20000,
      message: '',
      request_id: 'r',
      data: { token: 'tk', expires_at: 999 },
    });
    sc.listSessionMessages.mockResolvedValue({
      code: 20000,
      message: '',
      request_id: 'r',
      data: [
        {
          id: 'm1',
          timestamp: 1_700_000_000_100,
          sender: 'user-1',
          content: '历史消息',
          message_type: 'human',
          role: 'user',
        },
      ],
    });
    const sdkWithoutHydration = jest.fn<any>().mockImplementation((cfg: any) => {
      const inner: any = {
        config: cfg,
        request: jest.fn<any>().mockResolvedValue(undefined),
        abort: jest.fn<any>(),
        connect: jest.fn<any>(),
        disconnect: jest.fn<any>(),
        onMessage: undefined,
        onComplete: undefined,
        onError: undefined,
        isConnected: false,
        subscribeToConnectionStatus: jest.fn<any>(() => jest.fn<any>()),
      };
      inner.connect.mockImplementation(async () => {
        inner.isConnected = true;
      });
      return inner;
    });
    const provider = createGroupChatProvider({
      sessionId: 's-legacy-sdk',
      groupId: 'g1',
      identityId: 'me',
      createSdkProvider: sdkWithoutHydration,
      wsOrigin: 'wss://x.test',
    });

    await expect(provider.connect()).resolves.toBeUndefined();
    await expect(provider.loadHistory()).resolves.toHaveLength(1);
    expect(sdkWithoutHydration).toHaveBeenCalledTimes(1);

    // wrapper 自己的 hydration 状态仍可安全切换；底层缺少扩展方法不应抛错。
    expect(() => provider.beginHistoryHydration()).not.toThrow();
    expect(() => provider.enterLiveMode()).not.toThrow();
  });

  it('buffers SDK output until history hydration enters live mode', async () => {
    sc.createSessionToken.mockResolvedValue({
      code: 20000,
      message: '',
      request_id: 'r',
      data: { token: 'tk', expires_at: 999 },
    });
    const provider = createGroupChatProvider({
      sessionId: 's1',
      groupId: 'g1',
      identityId: 'me',
      createSdkProvider: fakeSdkProvider as unknown as Parameters<
        typeof createGroupChatProvider
      >[0]['createSdkProvider'],
      wsOrigin: 'wss://x.test',
    });
    const onMessage = jest.fn();
    provider.onMessage = onMessage;
    await provider.connect();
    const inner = provider as unknown as {
      inner: { onMessage?: (message: ChatMessage) => void; enterLiveMode: jest.Mock };
    };
    const liveMessage = { id: 'm1', role: 'assistant', content: 'live', status: 'streaming' } as ChatMessage;

    inner.inner.onMessage?.(liveMessage);
    expect(onMessage).not.toHaveBeenCalled();

    provider.enterLiveMode();
    expect(inner.inner.enterLiveMode).toHaveBeenCalled();
    expect(onMessage).toHaveBeenCalledWith(liveMessage);
  });

  it('concurrent connect shares initializePromise — token fetched once', async () => {
    sc.createSessionToken.mockResolvedValue({
      code: 20000,
      message: '',
      request_id: 'r',
      data: { token: 'tk2', expires_at: 999 },
    });
    const provider = createGroupChatProvider({
      sessionId: 's2',
      groupId: 'g1',
      identityId: 'me',
      createSdkProvider: fakeSdkProvider as unknown as Parameters<
        typeof createGroupChatProvider
      >[0]['createSdkProvider'],
      wsOrigin: 'wss://x.test',
    });
    await Promise.all([provider.connect(), provider.connect()]);
    expect(sc.createSessionToken).toHaveBeenCalledTimes(1);
    expect(fakeSdkProvider).toHaveBeenCalledTimes(1);
  });

  // 契约测试：后端返回 **新→旧（降序）** 的扁平消息数组（data 为数组而非 {messages}）。
  // loadHistory 必须 .reverse() 翻为旧→新升序，mapGroupHistoryMessages 才能正确聚合连续同 run_id 的 assistant 消息。
  it('loadHistory reverses backend descending order so mapper coalesces same-run_id assistant messages', async () => {
    sc.createSessionToken.mockResolvedValue({
      code: 20000,
      message: '',
      request_id: 'r',
      data: { token: 'tk', expires_at: 999 },
    });
    // 后端返回顺序：新→旧（降序）。createdAt 从大到小。
    sc.listSessionMessages.mockResolvedValue({
      code: 20000,
      message: '',
      request_id: 'r',
      data: [
        // 后端返回顺序：新→旧（降序）。timestamp 从大到小。
        // a1_p1/a1_p2 共享 run_id=r1，应被聚合为一条 assistant ChatMessage。
        {
          id: 'm-a2',
          timestamp: 1700000000201,
          sender: 'bot-a',
          content: '第二个回答',
          message_type: 'bot',
          role: 'assistant',
          run_id: 'r2',
        },
        {
          id: 'm-user2',
          timestamp: 1700000000200,
          sender: 'user-1',
          content: '第二个问题',
          message_type: 'human',
          role: 'user',
        },
        {
          id: 'm-a1-p2',
          timestamp: 1700000000102,
          sender: 'bot-a',
          content: '回答1第二段',
          message_type: 'bot',
          role: 'assistant',
          run_id: 'r1',
        },
        {
          id: 'm-a1-p1',
          timestamp: 1700000000101,
          sender: 'bot-a',
          content: '回答1第一段',
          message_type: 'bot',
          role: 'assistant',
          run_id: 'r1',
        },
        {
          id: 'm-user1',
          timestamp: 1700000000100,
          sender: 'user-1',
          content: '第一个问题',
          message_type: 'human',
          role: 'user',
        },
      ],
    });
    const provider = createGroupChatProvider({
      sessionId: 's-hist',
      groupId: 'g1',
      identityId: 'me',
      createSdkProvider: fakeSdkProvider as unknown as Parameters<
        typeof createGroupChatProvider
      >[0]['createSdkProvider'],
      wsOrigin: 'wss://x.test',
    });

    const history = await provider.loadHistory();

    // loadHistory 调用时带上 view_bot_id=identityId
    expect(sc.listSessionMessages).toHaveBeenCalledWith('s-hist', {
      limit: 50,
      view_bot_id: 'me',
      include_pending: true,
    });
    // (a) 升序 createdAt：旧→新
    expect(history.map((m) => m.createdAt)).toEqual([1700000000100, 1700000000101, 1700000000200, 1700000000201]);
    // (b) 角色顺序正确：user → assistant(聚合 r1) → user → assistant(r2)
    expect(history.map((m) => m.role)).toEqual(['user', 'assistant', 'user', 'assistant']);
    // (c) 同 conversationRoundId=r1 的两段 assistant 文本聚合为一条，content 以 \n 拼接
    expect(history).toHaveLength(4);
    expect(history[1].content).toBe('回答1第一段\n回答1第二段');
    // 第二轮 assistant 独立保留
    expect(history[3].content).toBe('第二个回答');
  });

  // 用户消息（无 run_id 聚合）工厂：timestamp 升序即后端降序的反转。
  // 使用毫秒级时间戳（与后端一致），避免 normalizeTimestamp 把秒级小值 ×1000。
  const T0 = 1_700_000_000_100;
  const userMsg = (id: string, timestamp: number, content: string) => ({
    id,
    timestamp,
    sender: 'user-1',
    content,
    message_type: 'human' as const,
    role: 'user' as const,
  });

  it('loadHistory 满页置 hasMore=true，不足一页置 hasMore=false', async () => {
    sc.createSessionToken.mockResolvedValue({
      code: 20000,
      message: '',
      request_id: 'r',
      data: { token: 'tk', expires_at: 999 },
    });
    // 首屏满页 50 条（timestamp 100..149）；后端返回降序（新→旧）。
    const firstPage = Array.from({ length: 50 }, (_, i) => userMsg(`m-${i}`, T0 + i, `c${i}`));
    sc.listSessionMessages.mockResolvedValueOnce({
      code: 20000,
      message: '',
      request_id: 'r',
      data: [...firstPage].reverse(),
    });
    const provider = createGroupChatProvider({
      sessionId: 's-page',
      groupId: 'g1',
      identityId: 'me',
      createSdkProvider: fakeSdkProvider,
      wsOrigin: 'wss://x.test',
    });
    const history = await provider.loadHistory();
    expect(history).toHaveLength(50);
    // 满页 → 还有更早消息可加载；首屏不带 before 游标。
    expect(provider.hasMoreHistory).toBe(true);
    expect(sc.listSessionMessages).toHaveBeenLastCalledWith('s-page', {
      limit: 50,
      view_bot_id: 'me',
      include_pending: true,
    });

    // 不足一页场景：另起一个 provider，首屏只回 3 条 → hasMore=false。
    sc.listSessionMessages.mockResolvedValueOnce({
      code: 20000,
      message: '',
      request_id: 'r',
      data: [userMsg('a', T0 - 97, 'x'), userMsg('b', T0 - 98, 'y'), userMsg('c', T0 - 99, 'z')].reverse(),
    });
    const providerShort = createGroupChatProvider({
      sessionId: 's-short',
      groupId: 'g1',
      identityId: 'me',
      createSdkProvider: fakeSdkProvider,
      wsOrigin: 'wss://x.test',
    });
    await providerShort.loadHistory();
    expect(providerShort.hasMoreHistory).toBe(false);
  });

  it('loadMoreHistory 以最旧时间戳为 before 游标翻页，不足一页时停止', async () => {
    sc.createSessionToken.mockResolvedValue({
      code: 20000,
      message: '',
      request_id: 'r',
      data: { token: 'tk', expires_at: 999 },
    });
    // 首屏满页 50 条（timestamp 100..149 升序 → 后端降序 149..100）。
    const firstPage = Array.from({ length: 50 }, (_, i) => userMsg(`m-${i}`, T0 + i, `c${i}`));
    sc.listSessionMessages.mockResolvedValueOnce({
      code: 20000,
      message: '',
      request_id: 'r',
      data: [...firstPage].reverse(),
    });
    const provider = createGroupChatProvider({
      sessionId: 's-more',
      groupId: 'g1',
      identityId: 'me',
      createSdkProvider: fakeSdkProvider,
      wsOrigin: 'wss://x.test',
    });
    await provider.loadHistory();
    expect(provider.hasMoreHistory).toBe(true);

    // 第二页：3 条更早消息（timestamp 1..3，后端降序 3,2,1）—— 不足一页 → hasMore=false。
    sc.listSessionMessages.mockResolvedValueOnce({
      code: 20000,
      message: '',
      request_id: 'r',
      data: [
        userMsg('m-old-3', T0 - 97, 'old3'),
        userMsg('m-old-2', T0 - 98, 'old2'),
        userMsg('m-old-1', T0 - 99, 'old1'),
      ],
    });
    const older = await provider.loadMoreHistory();
    // before 游标 = 首屏最旧 timestamp（T0 = 1700000000100）。
    expect(sc.listSessionMessages).toHaveBeenLastCalledWith('s-more', {
      limit: 50,
      before: '1700000000100',
      view_bot_id: 'me',
      include_pending: true,
    });
    // 翻转为升序旧→新：old1(T0-99) → old3(T0-97)。
    expect(older.map((m) => m.createdAt)).toEqual([T0 - 99, T0 - 98, T0 - 97]);
    expect(provider.hasMoreHistory).toBe(false);
    expect(provider.isLoadingMoreHistory).toBe(false);
  });

  it('loadMoreHistory 在 hasMore=false 时不发请求、返回空数组', async () => {
    sc.createSessionToken.mockResolvedValue({
      code: 20000,
      message: '',
      request_id: 'r',
      data: { token: 'tk', expires_at: 999 },
    });
    // 首屏仅 2 条 → hasMore=false。
    sc.listSessionMessages.mockResolvedValueOnce({
      code: 20000,
      message: '',
      request_id: 'r',
      data: [userMsg('a', T0 - 98, 'x'), userMsg('b', T0 - 99, 'y')],
    });
    const provider = createGroupChatProvider({
      sessionId: 's-empty',
      groupId: 'g1',
      identityId: 'me',
      createSdkProvider: fakeSdkProvider,
      wsOrigin: 'wss://x.test',
    });
    await provider.loadHistory();
    expect(provider.hasMoreHistory).toBe(false);

    const older = await provider.loadMoreHistory();
    expect(older).toEqual([]);
    // 只有首屏那次调用，未额外翻页请求。
    expect(sc.listSessionMessages).toHaveBeenCalledTimes(1);
  });
});
