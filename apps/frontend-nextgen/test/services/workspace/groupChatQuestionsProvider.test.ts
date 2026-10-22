import * as sessionController from '@/services/backendApi/collaboration/sessionController';
import {
  createGroupChatProvider,
  type GroupChatProvider,
} from '@/services/workspace/groupChatProvider';
import type { GroupQuestionSnapshot } from '@/services/workspace/groupQuestionService';
import type { GroupChatProviderConfig, GroupChatProvider as SdkGroupChatProvider } from '@tc-chat/adapters';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';

jest.mock('@/services/backendApi/collaboration/sessionController');

// SDK 包以 ESM 发布，jest+babel 不转译 node_modules；stub 掉默认导出的 SDK GroupChatProvider 类。
// 真实集成由下方可控 fake SDK transport 注入，本文件只验证真实包装层（groupChatProvider.ts）
// 对 ask_user 交互的协议消费与生命周期治理。
jest.mock('@tc-chat/adapters', () => ({
  GroupChatProvider: class MockGroupChatProvider {},
}));

const sc = sessionController as unknown as Record<string, jest.Mock<any>>;

type CreateSdkProvider = (config: GroupChatProviderConfig) => SdkGroupChatProvider;

const tokenResp = (token: string) => ({
  code: 20000,
  message: '',
  request_id: 'r',
  data: { token, expires_at: 999 },
});

interface FakeInner {
  config: GroupChatProviderConfig;
  /** 出站 wire 断言目标：包装层 capture 的 originalSend。 */
  wireSend: jest.Mock<any>;
  /** 入站 SDK 原始解析器断言目标：包装层 capture 的 originalOnMessage。 */
  originalOnMessage: jest.Mock<any>;
  /** 被包装层替换后的 transport 视图：出站驱动 / 入站注入都从这里走。 */
  transport: { send: (frame: unknown) => Promise<unknown>; onMessage: (msg: unknown) => void };
  connect: jest.Mock<any>;
  disconnect: jest.Mock<any>;
  request: jest.Mock<any>;
  abort: jest.Mock<any>;
  isConnected: boolean;
  statusListeners: Array<(event: unknown) => void>;
  subscribeToConnectionStatus: jest.Mock<any>;
}

/**
 * 可控 fake SDK：记录创建的全部 inner；connect() 像真实 SDK GroupChatProvider 一样
 * 经 transport.send 发 connect 帧（provider 依赖该出站点检测每个真实握手的开始）。
 */
function makeFakeSdk(): { factory: CreateSdkProvider; inners: FakeInner[] } {
  const inners: FakeInner[] = [];
  const factory: CreateSdkProvider = (cfg) => {
    const wireSend = jest.fn<any>().mockResolvedValue(undefined);
    const originalOnMessage = jest.fn<any>();
    const statusListeners: Array<(event: unknown) => void> = [];
    const connect = jest.fn<any>();
    const index = inners.length;
    const inner: FakeInner = {
      config: cfg,
      wireSend,
      originalOnMessage,
      transport: { send: wireSend, onMessage: originalOnMessage },
      connect,
      disconnect: jest.fn<any>(),
      request: jest.fn<any>().mockResolvedValue(undefined),
      abort: jest.fn<any>(),
      isConnected: false,
      statusListeners,
      subscribeToConnectionStatus: jest.fn<any>((listener: (event: unknown) => void) => {
        statusListeners.push(listener);
        return () => {};
      }),
    };
    inner.transport.send = wireSend;
    inner.transport.onMessage = originalOnMessage;
    connect.mockImplementation(async () => {
      inner.isConnected = true;
      // 与真实 SDK 一致：建链后经（可能已被包装的）transport.send 发 connect 帧
      await inner.transport.send({ type: 'req', id: `connect-${index}`, method: 'connect', params: {} });
    });
    inners.push(inner);
    return inner as unknown as SdkGroupChatProvider;
  };
  return { factory, inners };
}

function createProvider(factory: CreateSdkProvider): GroupChatProvider {
  return createGroupChatProvider({
    sessionId: 's1',
    groupId: 'g1',
    identityId: 'me',
    createSdkProvider: factory,
    wsOrigin: 'wss://x.test',
  });
}

/** ask_user requested 帧（默认匹配 g1/s1/run-1/i1，over 覆写任意字段）。 */
const askFrame = (over: Record<string, unknown> = {}) => {
  const base = {
    type: 'event',
    event: 'interaction',
    group_id: 'g1',
    bot_uuid: 'bot-1',
    bcsRunId: 'run-1',
    bcsSessionId: 's1',
    payload: {
      phase: 'requested',
      interactionId: 'i1',
      kind: 'ask_user',
      questions: [{ questionId: 'q1', question: '环境？', options: [{ value: 'stage', label: '预发' }] }],
    },
  };
  return { ...base, ...over };
};

const askFrameWith = (runId: string, interactionId: string) =>
  askFrame({
    bcsRunId: runId,
    payload: { ...askFrame().payload, interactionId },
  });

/** 交互 key 与 service 一致：(bcsRunId, interactionId) 的 JSON 数组形态。 */
const interactionKey = (runId: string, interactionId: string) => JSON.stringify([runId, interactionId]);

const submitResolution = { action: 'submit' as const, answers: { q1: { values: ['stage'] } } };

/** 收集订阅收到的全部快照事件。 */
function snapshotsOf(listener: jest.Mock<any>): GroupQuestionSnapshot[] {
  return listener.mock.calls.map(([snapshot]: [GroupQuestionSnapshot]) => snapshot);
}

const wireFramesOf = (inner: FakeInner, method: string) =>
  inner.wireSend.mock.calls
    .map(([frame]: [Record<string, unknown>]) => frame)
    .filter((frame: Record<string, unknown>) => frame?.method === method);

beforeEach(() => {
  jest.resetAllMocks();
});

describe('groupChatProvider ask_user 集成（真实包装层）', () => {
  it('入站 ask_user 交互进入快照且不透传 SDK；普通消息仍透传原解析器', async () => {
    sc.createSessionToken.mockResolvedValue(tokenResp('tk-1'));
    const { factory, inners } = makeFakeSdk();
    const provider = createProvider(factory);
    const subscriber = jest.fn<any>();
    provider.subscribeToQuestions(subscriber);
    await provider.connect();
    const inner = inners[0];

    // 普通聊天帧：不属于问答模块，原样交给 SDK 解析器
    const ordinaryFrame = { type: 'event', event: 'chat', group_id: 'g1', payload: {} };
    inner.transport.onMessage(ordinaryFrame);
    expect(inner.originalOnMessage).toHaveBeenCalledWith(ordinaryFrame);
    expect(snapshotsOf(subscriber).every((s) => s.interactions.length === 0)).toBe(true);

    // 当前 session 的 ask_user 交互：进入快照，不透传 SDK
    inner.transport.onMessage(askFrame());
    const updates = snapshotsOf(subscriber).filter((s) => s.reason === 'update');
    expect(updates.at(-1)?.interactions).toHaveLength(1);
    expect(updates.at(-1)?.interactions[0]).toMatchObject({
      key: interactionKey('run-1', 'i1'),
      runId: 'run-1',
      interactionId: 'i1',
      botId: 'bot-1',
    });
    expect(updates.at(-1)?.interactions[0].questions).toEqual([
      {
        questionId: 'q1',
        question: '环境？',
        multiSelect: false,
        allowOther: true,
        options: [{ value: 'stage', label: '预发' }],
      },
    ]);
    expect(inner.originalOnMessage).not.toHaveBeenCalledWith(askFrame());
  });

  it('resolveQuestion 经包装层发 interaction.resolve（不注入 session_id），本模块的 ACK 被消费', async () => {
    sc.createSessionToken.mockResolvedValue(tokenResp('tk-1'));
    const { factory, inners } = makeFakeSdk();
    const provider = createProvider(factory);
    const subscriber = jest.fn<any>();
    provider.subscribeToQuestions(subscriber);
    await provider.connect();
    const inner = inners[0];
    inner.transport.onMessage(askFrame());

    const pending = provider.resolveQuestion(interactionKey('run-1', 'i1'), submitResolution);

    const resolveFrames = wireFramesOf(inner, 'interaction.resolve');
    expect(resolveFrames).toHaveLength(1);
    expect(resolveFrames[0]).toEqual({
      type: 'req',
      id: expect.any(String),
      method: 'interaction.resolve',
      params: {
        bcsRunId: 'run-1',
        interactionId: 'i1',
        idempotencyKey: expect.any(String),
        action: 'submit',
        answers: { q1: { values: ['stage'] } },
      },
    });

    // ACK 回包进入包装层入站：匹配等待 request id → 消费并 settle，不透传 SDK
    inner.transport.onMessage({
      type: 'res',
      id: resolveFrames[0].id,
      ok: true,
      payload: { accepted: true, interactionId: 'i1', interactionStatus: 'accepted' },
    });
    await expect(pending).resolves.toBeUndefined();
    expect(inner.originalOnMessage).not.toHaveBeenCalledWith(
      expect.objectContaining({ id: resolveFrames[0].id, ok: true }),
    );
    const lastUpdate = snapshotsOf(subscriber).filter((s) => s.reason === 'terminal').at(-1);
    expect(lastUpdate?.interactions).toEqual([]);

    // 陌生 res（如 connect ACK）：不属于本模块等待中的请求 → 透传 SDK
    inner.transport.onMessage({ type: 'res', id: 'connect-0', ok: true, payload: {} });
    expect(inner.originalOnMessage).toHaveBeenCalledWith(expect.objectContaining({ id: 'connect-0' }));
  });

  it('旧 inner 的入站回调在替换后被丢弃，不再触碰问答或 SDK 状态', async () => {
    sc.createSessionToken
      .mockResolvedValueOnce(tokenResp('tk-1'))
      .mockResolvedValueOnce(tokenResp('tk-2'));
    const { factory, inners } = makeFakeSdk();
    const provider = createProvider(factory);
    const subscriber = jest.fn<any>();
    provider.subscribeToQuestions(subscriber);
    await provider.connect();
    const firstInner = inners[0];
    firstInner.transport.onMessage(askFrame());
    const staleIncoming = firstInner.transport.onMessage;

    await provider.reconnect();
    expect(inners).toHaveLength(2);
    subscriber.mockClear();

    // 旧连接迟到回调：帧虽会流经旧解析器，但不得更新新连接的问答状态
    const staleFrame = askFrameWith('run-1', 'i2');
    staleIncoming(staleFrame);
    expect(snapshotsOf(subscriber)).toHaveLength(0);
    expect(firstInner.originalOnMessage).not.toHaveBeenCalled();

    // 新连接正常消费交互（同 ID 交互按新周期重放语义重建）
    inners[1].transport.onMessage(askFrame());
    const updates = snapshotsOf(subscriber).filter((s) => s.reason === 'update');
    expect(updates.at(-1)?.interactions.map((i) => i.interactionId)).toEqual(['i1']);
  });

  it('显式重连：新 connect 握手开始即清空旧快照，重放交互重建', async () => {
    sc.createSessionToken
      .mockResolvedValueOnce(tokenResp('tk-1'))
      .mockResolvedValueOnce(tokenResp('tk-2'));
    const { factory, inners } = makeFakeSdk();
    const provider = createProvider(factory);
    const subscriber = jest.fn<any>();
    provider.subscribeToQuestions(subscriber);
    await provider.connect();
    inners[0].transport.onMessage(askFrame());
    expect(
      snapshotsOf(subscriber).some((s) => s.interactions.some((i) => i.interactionId === 'i1')),
    ).toBe(true);

    await provider.reconnect();

    // 替换后，刚出现的交互被重置（重连重放将重建，终态/幂等缓存属 service 层语义）
    const resets = snapshotsOf(subscriber).filter((s) => s.reason === 'reset');
    expect(resets.at(-1)).toEqual({ reason: 'reset', interactions: [] });

    inners[1].transport.onMessage(askFrame());
    const updates = snapshotsOf(subscriber).filter((s) => s.reason === 'update');
    const last = updates.at(-1);
    expect(last?.interactions).toHaveLength(1);
    expect(last?.interactions[0].interactionId).toBe('i1');
  });

  it('SDK 自动重连重发 connect：握手前重置；connected 通知不清空重放快照', async () => {
    sc.createSessionToken.mockResolvedValue(tokenResp('tk-1'));
    const { factory, inners } = makeFakeSdk();
    const provider = createProvider(factory);
    const subscriber = jest.fn<any>();
    provider.subscribeToQuestions(subscriber);
    await provider.connect();
    const inner = inners[0];
    inner.transport.onMessage(askFrame());
    subscriber.mockClear();

    // 模拟 SDK 侧自动重连：断线后同一 inner 重发 connect 帧走包装层出站
    inner.isConnected = false;
    await inner.transport.send({ type: 'req', id: 'reconnect-1', method: 'connect', params: {} });
    expect(snapshotsOf(subscriber).some((s) => s.reason === 'reset')).toBe(true);
    inner.isConnected = true;

    // connect ACK 与服务端 Pending 重放：重建快照
    inner.transport.onMessage({ type: 'res', id: 'reconnect-1', ok: true, payload: {} });
    inner.transport.onMessage(askFrame());
    const updates = snapshotsOf(subscriber).filter((s) => s.reason === 'update');
    expect(updates.at(-1)?.interactions.map((i) => i.interactionId)).toEqual(['i1']);

    // 其后到达的 connected 状态通知不得清空重放结果
    subscriber.mockClear();
    for (const listener of inner.statusListeners) listener({ status: 'connected', retryCount: 0 });
    expect(subscriber).not.toHaveBeenCalled();
  });

  it('disconnect 立即结束在途回答等待并清空快照，此后的旧线帧不再更新状态', async () => {
    sc.createSessionToken.mockResolvedValue(tokenResp('tk-1'));
    const { factory, inners } = makeFakeSdk();
    const provider = createProvider(factory);
    const subscriber = jest.fn<any>();
    provider.subscribeToQuestions(subscriber);
    await provider.connect();
    const inner = inners[0];
    inner.transport.onMessage(askFrame());

    const pending = provider.resolveQuestion(interactionKey('run-1', 'i1'), submitResolution);
    provider.disconnect();

    await expect(pending).rejects.toMatchObject({ code: 'DISCONNECTED', retryable: true });
    expect(snapshotsOf(subscriber).some((s) => s.reason === 'reset')).toBe(true);
    expect(inner.disconnect).toHaveBeenCalled();

    // 断开后同线迟到交互帧不得复活问答状态
    subscriber.mockClear();
    inner.transport.onMessage(askFrameWith('run-1', 'i3'));
    expect(snapshotsOf(subscriber)).toHaveLength(0);
  });

  it('异步初始化期间 disconnect：随后完成的旧连接不得重新激活交互状态', async () => {
    const tokenGate: { resolve: ((value: ReturnType<typeof tokenResp>) => void) | null } = { resolve: null };
    sc.createSessionToken.mockImplementation(
      () => new Promise((resolve) => { tokenGate.resolve = resolve; }),
    );
    const { factory, inners } = makeFakeSdk();
    const provider = createProvider(factory);
    const subscriber = jest.fn<any>();
    provider.subscribeToQuestions(subscriber);

    // connect 初始化在途时用户断开：迟到的 token 不得恢复连接或问答状态。
    const connecting = provider.connect();
    provider.disconnect();
    tokenGate.resolve?.(tokenResp('tk-1'));
    await connecting;

    expect(inners).toHaveLength(0);
    expect(provider.isConnected).toBe(false);
    const interactionsSeen = snapshotsOf(subscriber)
      .flatMap((s) => s.interactions)
      .map((i) => i.interactionId);
    expect(interactionsSeen).toEqual([]);
  });

  it('不同 Provider 实例的问答状态完全隔离', async () => {
    sc.createSessionToken.mockResolvedValue(tokenResp('tk-1'));
    const sdkA = makeFakeSdk();
    const sdkB = makeFakeSdk();
    const providerA = createProvider(sdkA.factory);
    const providerB = createProvider(sdkB.factory);
    const subscriberA = jest.fn<any>();
    const subscriberB = jest.fn<any>();
    providerA.subscribeToQuestions(subscriberA);
    providerB.subscribeToQuestions(subscriberB);
    await providerA.connect();
    await providerB.connect();

    sdkA.inners[0].transport.onMessage(askFrame());
    sdkB.inners[0].transport.onMessage(askFrameWith('run-9', 'i9'));

    const interactionsOf = (subscriber: jest.Mock<any>) =>
      snapshotsOf(subscriber)
        .flatMap((s) => s.interactions)
        .map((i) => i.interactionId);
    expect(interactionsOf(subscriberA)).toEqual(['i1']);
    expect(interactionsOf(subscriberB)).toEqual(['i9']);

    // A 的回答只走 A 的 transport；B 的快照与订阅不受影响
    subscriberB.mockClear();
    const pendingA = providerA.resolveQuestion(interactionKey('run-1', 'i1'), submitResolution);
    const resolveFramesA = wireFramesOf(sdkA.inners[0], 'interaction.resolve');
    expect(resolveFramesA).toHaveLength(1);
    sdkA.inners[0].transport.onMessage({
      type: 'res',
      id: resolveFramesA[0].id,
      ok: true,
      payload: { interactionStatus: 'accepted' },
    });
    await expect(pendingA).resolves.toBeUndefined();
    // B 的 transport 未出现任何 interaction.resolve 出站帧，其快照订阅也未被触碰
    expect(wireFramesOf(sdkB.inners[0], 'interaction.resolve')).toHaveLength(0);
    expect(snapshotsOf(subscriberB)).toHaveLength(0);
  });
});
