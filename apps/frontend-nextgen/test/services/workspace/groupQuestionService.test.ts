/**
 * BCS 群聊 ask_user 交互 service 测试。
 *
 * 依据 specs/2026-10-13-bcs-ask-user-question 与 bcs-provider-2.0-sse-protocol.md
 * §6.2/§7：严格群/会话帧解析与问题规范化、快照订阅（区分 terminal 移除与
 * connection reset）、resolve 请求关联（匹配 ACK、10 秒超时、幂等键复用、
 * 终态单调不复活）以及断开时的等待/定时器清理。
 */
import type { GroupQuestionResolution } from '@/domain/collaboration/groupQuestions';
import {
  GroupQuestionResolveError,
  GroupQuestionService,
  type GroupQuestionSnapshot,
} from '@/services/workspace/groupQuestionService';
import { afterEach, beforeEach, describe, expect, it, jest } from '@jest/globals';

const GROUP_ID = 'g1';
const SESSION_ID = 's1';

/** 任务给定的真实协议 fixture 基线：缺省 multiSelect/allowOther 的单选题。 */
const BASE_QUESTIONS = [
  {
    questionId: 'q1',
    question: '环境？',
    options: [{ value: 'stage', label: '预发布' }],
  },
];

/** 入站 interaction 事件帧构造：可信 envelope 字段 + payload 覆盖（内容允许非法值）。 */
function interactionEvent({
  groupId = GROUP_ID,
  botId = 'bot1',
  runId = 'run1',
  sessionId = SESSION_ID,
  payload,
}: {
  groupId?: unknown;
  botId?: unknown;
  runId?: unknown;
  sessionId?: unknown;
  payload: Record<string, unknown>;
}): Record<string, unknown> {
  return {
    type: 'event',
    event: 'interaction',
    group_id: groupId,
    bot_uuid: botId,
    bcsRunId: runId,
    bcsSessionId: sessionId,
    payload,
  };
}

function requestedPayload(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    phase: 'requested',
    interactionId: 'i1',
    kind: 'ask_user',
    questions: BASE_QUESTIONS,
    ...overrides,
  };
}

function terminalPayload(phase: string): Record<string, unknown> {
  return { phase, interactionId: 'i1', kind: 'ask_user' };
}

/** §7.2 成功响应：request id 匹配等待中的请求才算提交成功。 */
function okResponse(requestId: string): Record<string, unknown> {
  return {
    type: 'res',
    id: requestId,
    ok: true,
    payload: {
      accepted: true,
      interactionId: 'i1',
      interactionStatus: 'resolved',
      idempotencyKey: 'idem-irrelevant',
    },
  };
}

/** §7.2 失败响应：结构在顶层 error，终态状态在 error.details.interactionStatus。 */
function failResponse(requestId: string, error: Record<string, unknown>): Record<string, unknown> {
  return { type: 'res', id: requestId, ok: false, error };
}

const RETRYABLE_ERROR = {
  code: 'interaction_resolve_failed',
  message: 'Failed to deliver the resolution to Provider',
  retryable: true,
  details: { interactionId: 'i1', interactionStatus: 'pending' },
};

type SendFn = (frame: unknown) => Promise<unknown>;
type SnapshotListener = (snapshot: GroupQuestionSnapshot) => void;

interface ServiceHarness {
  service: GroupQuestionService;
  send: jest.Mock<SendFn>;
  listener: jest.Mock<SnapshotListener>;
  setConnected: (connected: boolean) => void;
}

function createService(): ServiceHarness {
  let connected = true;
  const send = jest.fn<SendFn>().mockResolvedValue(undefined);
  const service = new GroupQuestionService({
    groupId: GROUP_ID,
    bcsSessionId: SESSION_ID,
    send,
    isConnected: () => connected,
  });
  const listener = jest.fn<SnapshotListener>();
  service.subscribe(listener);
  // 订阅时的立即回放已消费，之后的断言只看新增通知
  listener.mockClear();
  return {
    service,
    send,
    listener,
    setConnected: (value: boolean) => {
      connected = value;
    },
  };
}

function lastSnapshot(listener: ServiceHarness['listener']): GroupQuestionSnapshot {
  const calls = listener.mock.calls;
  if (calls.length === 0) throw new Error('尚无快照通知');
  return calls[calls.length - 1][0];
}

function onlyKey(listener: ServiceHarness['listener']): string {
  const interactions = lastSnapshot(listener).interactions;
  if (interactions.length !== 1) throw new Error(`预期仅一个待处理交互，实际 ${interactions.length}`);
  return interactions[0].key;
}

/** 读取第 n 次出站 interaction.resolve 帧。 */
function sentFrame(
  send: ServiceHarness['send'],
  call: number,
): { id: string; params: Record<string, unknown> } {
  expect(send.mock.calls.length).toBeGreaterThan(call);
  return send.mock.calls[call][0] as { id: string; params: Record<string, unknown> };
}

/** 捕获 resolve 的结构化失败；成功完成或非结构化错误都会让测试失败。 */
async function resolveFailure(promise: Promise<void>): Promise<GroupQuestionResolveError> {
  try {
    await promise;
  } catch (error) {
    if (error instanceof GroupQuestionResolveError) return error;
    throw new Error(`预期 GroupQuestionResolveError，实际抛出：${String(error)}`);
  }
  throw new Error('预期 resolve 失败，但 promise 成功完成');
}

/** 等待已 settle 的 promise 通知其观察者（不推进定时器）。 */
async function flushMicrotasks(): Promise<void> {
  for (let i = 0; i < 8; i += 1) {
    await Promise.resolve();
  }
}

const submitStage: GroupQuestionResolution = {
  action: 'submit',
  answers: { q1: { values: ['stage'] } },
};

describe('接收：ask_user interaction 帧', () => {
  it('当前群/会话 requested 建立问答，缺省 multiSelect=false、allowOther=true', () => {
    const { service, listener } = createService();
    const consumed = service.handleFrame(interactionEvent({ payload: requestedPayload() }));

    expect(consumed).toBe(true);
    expect(listener).toHaveBeenCalledTimes(1);
    const snapshot = listener.mock.calls[0][0];
    expect(snapshot.reason).toBe('update');
    expect(snapshot.interactions).toHaveLength(1);
    const interaction = snapshot.interactions[0];
    expect(interaction).toMatchObject({
      runId: 'run1',
      interactionId: 'i1',
      botId: 'bot1',
    });
    // 缺省按协议规范化：multiSelect 省略为 false，allowOther 省略为 true
    expect(interaction.questions).toEqual([
      {
        questionId: 'q1',
        question: '环境？',
        multiSelect: false,
        allowOther: true,
        options: [{ value: 'stage', label: '预发布' }],
      },
    ]);
  });

  it('显式 multiSelect/allowOther、header 与选项 description 保留原值', () => {
    const { service, listener } = createService();
    service.handleFrame(
      interactionEvent({
        payload: requestedPayload({
          questions: [
            {
              questionId: 'q1',
              question: '环境？',
              header: '部署',
              multiSelect: true,
              allowOther: false,
              options: [{ value: 'stage', label: '预发布', description: '预发环境' }],
            },
          ],
        }),
      }),
    );

    expect(lastSnapshot(listener).interactions[0].questions).toEqual([
      {
        questionId: 'q1',
        question: '环境？',
        header: '部署',
        multiSelect: true,
        allowOther: false,
        options: [{ value: 'stage', label: '预发布', description: '预发环境' }],
      },
    ]);
  });

  it('其他群或其他会话的事件不消费、不产生问答', () => {
    const { service, listener } = createService();

    expect(
      service.handleFrame(interactionEvent({ groupId: 'g2', payload: requestedPayload() })),
    ).toBe(false);
    expect(
      service.handleFrame(interactionEvent({ sessionId: 's2', payload: requestedPayload() })),
    ).toBe(false);
    expect(listener).not.toHaveBeenCalled();
  });

  it('非 ask_user kind 不消费（交回原 SDK 链路）', () => {
    const { service, listener } = createService();

    expect(
      service.handleFrame(
        interactionEvent({
          payload: {
            phase: 'requested',
            interactionId: 'i1',
            kind: 'exec',
            command: 'npm run deploy',
            options: [{ decision: 'allow_once', label: 'Allow once' }],
          },
        }),
      ),
    ).toBe(false);
    expect(listener).not.toHaveBeenCalled();
  });

  it('缺失身份字段（bot_uuid/bcsRunId/interactionId）不消费', () => {
    const { service, listener } = createService();
    const missingBot = interactionEvent({ payload: requestedPayload() });
    delete missingBot.bot_uuid;
    const missingRun = interactionEvent({ payload: requestedPayload() });
    delete missingRun.bcsRunId;

    expect(service.handleFrame(missingBot)).toBe(false);
    expect(service.handleFrame(missingRun)).toBe(false);
    expect(
      service.handleFrame(interactionEvent({ payload: requestedPayload({ interactionId: '' }) })),
    ).toBe(false);
    expect(listener).not.toHaveBeenCalled();
  });

  it('无效 questions 不产生表单，且不影响后续有效帧', () => {
    const { service, listener } = createService();
    const invalidPayloads: Array<[string, Record<string, unknown>]> = [
      ['缺 questions', { questions: undefined }],
      ['questions 非数组', { questions: 'q1' }],
      ['questions 为空数组', { questions: [] }],
      ['问题缺 question 文本', { questions: [{ questionId: 'q1' }] }],
      ['问题缺 questionId', { questions: [{ question: '环境？' }] }],
      ['questionId 重复', { questions: [...BASE_QUESTIONS, { questionId: 'q1', question: '重复' }] }],
      ['questionId 为空白', { questions: [{ questionId: '  ', question: '环境？' }] }],
      [
        '选项缺 value',
        { questions: [{ questionId: 'q1', question: '环境？', options: [{ label: '预发布' }] }] },
      ],
      ['options 为空数组', { questions: [{ questionId: 'q1', question: '环境？', options: [] }] }],
      ['multiSelect 非 boolean', { questions: [{ questionId: 'q1', question: '环境？', multiSelect: 'yes' }] }],
      ['allowOther 非 boolean', { questions: [{ questionId: 'q1', question: '环境？', allowOther: 0 }] }],
    ];

    for (const [reason, overrides] of invalidPayloads) {
      const consumed = service.handleFrame(interactionEvent({ payload: requestedPayload(overrides) }));
      if (consumed) throw new Error(`无效帧不应被消费：${reason}`);
      expect(listener).not.toHaveBeenCalled();
    }

    // 无效帧不毒化 service：后续有效帧仍建立问答
    expect(service.handleFrame(interactionEvent({ payload: requestedPayload() }))).toBe(true);
    expect(lastSnapshot(listener).interactions).toHaveLength(1);
  });

  it('同一连接内重复 requested 不重复展示、不重置快照', () => {
    const { service, listener } = createService();

    expect(service.handleFrame(interactionEvent({ payload: requestedPayload() }))).toBe(true);
    expect(service.handleFrame(interactionEvent({ payload: requestedPayload() }))).toBe(true);

    expect(listener).toHaveBeenCalledTimes(1);
    expect(lastSnapshot(listener).interactions).toHaveLength(1);
  });

  it('不同 run 的同名 interactionId 相互独立，终态只关闭对应 run', () => {
    const { service, listener } = createService();

    expect(service.handleFrame(interactionEvent({ payload: requestedPayload() }))).toBe(true);
    expect(
      service.handleFrame(interactionEvent({ runId: 'run2', payload: requestedPayload() })),
    ).toBe(true);

    const snapshot = lastSnapshot(listener);
    expect(snapshot.interactions).toHaveLength(2);
    expect(new Set(snapshot.interactions.map((item) => item.key)).size).toBe(2);

    // run1 的终态只关闭 run1 的交互
    service.handleFrame(interactionEvent({ payload: terminalPayload('resolved') }));
    const afterTerminal = lastSnapshot(listener);
    expect(afterTerminal.reason).toBe('terminal');
    expect(afterTerminal.interactions).toHaveLength(1);
    expect(afterTerminal.interactions[0].runId).toBe('run2');
  });

  it('终态事件按 terminal 通知移除，迟到 requested 不复活', () => {
    const { service, listener } = createService();

    service.handleFrame(interactionEvent({ payload: requestedPayload() }));
    listener.mockClear();

    expect(service.handleFrame(interactionEvent({ payload: terminalPayload('resolved') }))).toBe(true);
    expect(listener).toHaveBeenCalledTimes(1);
    const snapshot = lastSnapshot(listener);
    expect(snapshot.reason).toBe('terminal');
    expect(snapshot.interactions).toHaveLength(0);

    // 同一连接内迟到 requested 不复活已终态的交互
    listener.mockClear();
    expect(service.handleFrame(interactionEvent({ payload: requestedPayload() }))).toBe(true);
    expect(listener).not.toHaveBeenCalled();

    // 新订阅回放当前快照仍为空
    const fresh = jest.fn<SnapshotListener>();
    service.subscribe(fresh);
    expect(fresh.mock.calls[0][0].interactions).toHaveLength(0);
  });

  it('终态不需要 questions；accepted/invalidated 也会结束待处理', () => {
    const { service, listener } = createService();

    // 带无关回显字段的终态帧仍按终态语义处理：未知交互只做静默标记
    expect(
      service.handleFrame(
        interactionEvent({
          payload: {
            phase: 'resolved',
            interactionId: 'ghost',
            kind: 'ask_user',
            question: '迟到字段不影响终态语义',
          },
        }),
      ),
    ).toBe(true);
    expect(listener).not.toHaveBeenCalled();

    expect(service.handleFrame(interactionEvent({ payload: requestedPayload({ interactionId: 'i9' }) }))).toBe(
      true,
    );
    listener.mockClear();

    for (const phase of ['accepted', 'invalidated'] as const) {
      service.handleFrame(interactionEvent({ payload: { phase, interactionId: 'i9', kind: 'ask_user' } }));
      const snapshot = lastSnapshot(listener);
      expect(snapshot.reason).toBe('terminal');
      expect(snapshot.interactions).toHaveLength(0);
      // 同一连接内再次 requested 不复活终态交互
      expect(
        service.handleFrame(interactionEvent({ payload: requestedPayload({ interactionId: 'i9' }) })),
      ).toBe(true);
      expect(lastSnapshot(listener)).toBe(snapshot);
    }
  });

  it('未收到过 requested 的终态事件静默标记，不打扰快照', () => {
    const { service, listener } = createService();

    expect(service.handleFrame(interactionEvent({ payload: terminalPayload('resolved') }))).toBe(true);
    expect(listener).not.toHaveBeenCalled();
  });

  it('未知 phase 与非对象帧不消费', () => {
    const { service, listener } = createService();

    expect(
      service.handleFrame(interactionEvent({ payload: requestedPayload({ phase: 'paused' }) })),
    ).toBe(false);
    expect(service.handleFrame('interaction')).toBe(false);
    expect(service.handleFrame(null)).toBe(false);
    expect(service.handleFrame(42)).toBe(false);
    expect(listener).not.toHaveBeenCalled();
  });

  it('无关帧不消费：普通 chat 事件、method 帧、陌生 res', () => {
    const { service, listener } = createService();

    expect(
      service.handleFrame({
        type: 'event',
        event: 'chat',
        group_id: GROUP_ID,
        bcsSessionId: SESSION_ID,
        payload: { delta: 'hi' },
      }),
    ).toBe(false);
    expect(
      service.handleFrame({ type: 'req', id: 'other-1', method: 'chat.send', params: {} }),
    ).toBe(false);
    expect(
      service.handleFrame({ type: 'res', id: 'never-sent', ok: true, payload: {} }),
    ).toBe(false);
    expect(listener).not.toHaveBeenCalled();
  });

  it('订阅时立即回放当前快照，退订后不再接收通知', () => {
    const { service } = createService();

    service.handleFrame(interactionEvent({ payload: requestedPayload() }));
    const lateSubscriber = jest.fn<SnapshotListener>();
    const unsubscribe = service.subscribe(lateSubscriber);

    expect(lateSubscriber).toHaveBeenCalledTimes(1);
    const replay = lateSubscriber.mock.calls[0][0];
    expect(replay.reason).toBe('update');
    expect(replay.interactions).toHaveLength(1);
    expect(replay.interactions[0].interactionId).toBe('i1');

    unsubscribe();
    service.handleFrame(interactionEvent({ payload: terminalPayload('resolved') }));
    expect(lateSubscriber).toHaveBeenCalledTimes(1);
  });
});

describe('resolve 请求生命周期', () => {
  beforeEach(() => {
    jest.useFakeTimers();
  });
  afterEach(() => {
    jest.useRealTimers();
  });

  it('发送 interaction.resolve 帧（answers 仅 values），匹配成功响应后按终态移除', async () => {
    const { service, send, listener } = createService();
    service.handleFrame(interactionEvent({ payload: requestedPayload() }));
    const key = onlyKey(listener);
    listener.mockClear();

    const outcome = service.resolve(key, submitStage);

    expect(send).toHaveBeenCalledTimes(1);
    expect(send.mock.calls[0][0]).toEqual({
      type: 'req',
      id: expect.any(String),
      method: 'interaction.resolve',
      params: {
        bcsRunId: 'run1',
        interactionId: 'i1',
        idempotencyKey: expect.any(String),
        action: 'submit',
        answers: { q1: { values: ['stage'] } },
      },
    });

    expect(service.handleFrame(okResponse(sentFrame(send, 0).id))).toBe(true);
    await expect(outcome).resolves.toBeUndefined();
    expect(jest.getTimerCount()).toBe(0);

    const snapshot = lastSnapshot(listener);
    expect(snapshot.reason).toBe('terminal');
    expect(snapshot.interactions).toHaveLength(0);
  });

  it('仅匹配 ACK 的 request id 才成功', async () => {
    const { service, send, listener } = createService();
    service.handleFrame(interactionEvent({ payload: requestedPayload() }));
    const key = onlyKey(listener);
    listener.mockClear();

    const outcome = service.resolve(key, submitStage);
    const codes: string[] = [];
    outcome.then(
      () => codes.push('ok'),
      (error: GroupQuestionResolveError) => codes.push(error.code),
    );

    // 陌生 res 不属于本 service 的等待，不消费、也不结束等待
    expect(service.handleFrame(okResponse('someone-else-request'))).toBe(false);
    await jest.advanceTimersByTimeAsync(5_000);
    expect(codes).toEqual([]);

    expect(service.handleFrame(okResponse(sentFrame(send, 0).id))).toBe(true);
    await expect(outcome).resolves.toBeUndefined();
    expect(codes).toEqual(['ok']);
  });

  it('send 同步触发 ACK 也能匹配（等待先于发送注册）', async () => {
    const { service, send, listener } = createService();
    service.handleFrame(interactionEvent({ payload: requestedPayload() }));
    const key = onlyKey(listener);
    listener.mockClear();

    send.mockImplementation((frame: unknown) => {
      service.handleFrame(okResponse((frame as { id: string }).id));
      return Promise.resolve(undefined);
    });

    await expect(service.resolve(key, submitStage)).resolves.toBeUndefined();
    expect(jest.getTimerCount()).toBe(0);
    expect(lastSnapshot(listener).reason).toBe('terminal');
  });

  it('10 秒超时产生可重试错误，保留待处理并可再次提交', async () => {
    const { service, send, listener } = createService();
    service.handleFrame(interactionEvent({ payload: requestedPayload() }));
    const key = onlyKey(listener);
    listener.mockClear();

    const outcome = service.resolve(key, submitStage);
    const codes: string[] = [];
    outcome.then(
      () => codes.push('ok'),
      (error: GroupQuestionResolveError) => codes.push(error.code),
    );

    await jest.advanceTimersByTimeAsync(9_999);
    expect(codes).toEqual([]);

    await jest.advanceTimersByTimeAsync(1_001);
    expect(codes).toEqual(['TIMEOUT']);
    expect(listener).not.toHaveBeenCalled();
    expect(jest.getTimerCount()).toBe(0);

    // 超时保留待处理：再次提交仍成功
    const retry = service.resolve(key, submitStage);
    expect(send).toHaveBeenCalledTimes(2);
    service.handleFrame(okResponse(sentFrame(send, 1).id));
    await expect(retry).resolves.toBeUndefined();
  });

  it('send 失败产生可重试错误并保留待处理', async () => {
    const { service, send, listener } = createService();
    service.handleFrame(interactionEvent({ payload: requestedPayload() }));
    const key = onlyKey(listener);
    listener.mockClear();

    send.mockRejectedValueOnce(new Error('socket closed'));
    const error = await resolveFailure(service.resolve(key, submitStage));

    expect(error).toMatchObject({ code: 'SEND_FAILED', retryable: true, interactionStatus: null });
    expect(listener).not.toHaveBeenCalled();
    expect(jest.getTimerCount()).toBe(0);
  });

  it('可重试失败透传顶层 error 字段并保留待处理', async () => {
    const { service, send, listener } = createService();
    service.handleFrame(interactionEvent({ payload: requestedPayload() }));
    const key = onlyKey(listener);
    listener.mockClear();

    const outcome = service.resolve(key, submitStage);
    service.handleFrame(failResponse(sentFrame(send, 0).id, RETRYABLE_ERROR));
    const error = await resolveFailure(outcome);

    expect(error).toMatchObject({
      code: 'interaction_resolve_failed',
      retryable: true,
      interactionStatus: 'pending',
    });
    expect(error.message).toBe('Failed to deliver the resolution to Provider');
    expect(listener).not.toHaveBeenCalled();
  });

  it('明确拒绝（无终态状态）保留问答并显示原因', async () => {
    const { service, send, listener } = createService();
    service.handleFrame(interactionEvent({ payload: requestedPayload() }));
    const key = onlyKey(listener);
    listener.mockClear();

    const outcome = service.resolve(key, submitStage);
    service.handleFrame(
      failResponse(sentFrame(send, 0).id, {
        code: 'unauthorized',
        message: '无权限回答该问答',
        retryable: false,
      }),
    );
    const error = await resolveFailure(outcome);

    expect(error).toMatchObject({ code: 'unauthorized', retryable: false, interactionStatus: null });
    expect(listener).not.toHaveBeenCalled();
  });

  it('失败响应携带终态时按状态结束待处理，且不允许复活', async () => {
    const { service, send, listener } = createService();
    service.handleFrame(interactionEvent({ payload: requestedPayload() }));
    const key = onlyKey(listener);
    listener.mockClear();

    const outcome = service.resolve(key, submitStage);
    service.handleFrame(
      failResponse(sentFrame(send, 0).id, {
        code: 'interaction_resolve_failed',
        message: '该问答已由另一人处理',
        retryable: false,
        details: { interactionId: 'i1', interactionStatus: 'resolved' },
      }),
    );
    const error = await resolveFailure(outcome);

    expect(error.interactionStatus).toBe('resolved');
    const snapshot = lastSnapshot(listener);
    expect(snapshot.reason).toBe('terminal');
    expect(snapshot.interactions).toHaveLength(0);

    // 迟到 requested 不复活
    listener.mockClear();
    expect(service.handleFrame(interactionEvent({ payload: requestedPayload() }))).toBe(true);
    expect(listener).not.toHaveBeenCalled();
  });

  it('并发提交同一交互去重，复用同一次 ACK 结果', async () => {
    const { service, send, listener } = createService();
    service.handleFrame(interactionEvent({ payload: requestedPayload() }));
    const key = onlyKey(listener);
    listener.mockClear();

    const first = service.resolve(key, submitStage);
    const second = service.resolve(key, submitStage);

    expect(send).toHaveBeenCalledTimes(1);
    service.handleFrame(okResponse(sentFrame(send, 0).id));
    await expect(first).resolves.toBeUndefined();
    await expect(second).resolves.toBeUndefined();
    expect(jest.getTimerCount()).toBe(0);
    expect(lastSnapshot(listener).reason).toBe('terminal');
  });

  it('终态事件先于 ACK 时合并为成功，迟到失败不产生任何新状态', async () => {
    const { service, send, listener } = createService();
    service.handleFrame(interactionEvent({ payload: requestedPayload() }));
    const key = onlyKey(listener);
    listener.mockClear();

    const outcome = service.resolve(key, submitStage);
    // 原 SSE resolved 先于本次响应到达：BCN 将竞态合并为成功
    expect(service.handleFrame(interactionEvent({ payload: terminalPayload('resolved') }))).toBe(true);
    await expect(outcome).resolves.toBeUndefined();
    expect(jest.getTimerCount()).toBe(0);
    expect(lastSnapshot(listener).reason).toBe('terminal');
    listener.mockClear();

    // 迟到的失败响应已无等待可匹配：不消费、不产生新状态
    expect(
      service.handleFrame(
        failResponse(sentFrame(send, 0).id, {
          code: 'interaction_resolve_failed',
          message: 'late failure',
          retryable: false,
          details: { interactionStatus: 'invalidated' },
        }),
      ),
    ).toBe(false);
    expect(listener).not.toHaveBeenCalled();

    // 迟到 requested 也不复活
    expect(service.handleFrame(interactionEvent({ payload: requestedPayload() }))).toBe(true);
    expect(listener).not.toHaveBeenCalled();
  });

  it('相同回答复用幂等键且 requestId 更新；回答变化或取消使用新键', async () => {
    const { service, send, listener } = createService();
    service.handleFrame(
      interactionEvent({
        payload: requestedPayload({
          questions: [
            { questionId: 'q1', question: '环境？', options: [{ value: 'stage', label: '预发布' }] },
            {
              questionId: 'q2',
              question: '组件？',
              multiSelect: true,
              options: [
                { value: 'web', label: 'Web' },
                { value: 'worker', label: 'Worker' },
              ],
            },
          ],
        }),
      }),
    );
    const key = onlyKey(listener);
    listener.mockClear();

    const submitOrdered: GroupQuestionResolution = {
      action: 'submit',
      answers: { q1: { values: ['stage'] }, q2: { values: ['web'] } },
    };
    // 插入顺序不同、内容相同 → 按题目顺序规范化后指纹一致
    const submitReordered: GroupQuestionResolution = {
      action: 'submit',
      answers: { q2: { values: ['web'] }, q1: { values: ['stage'] } },
    };
    const submitChanged: GroupQuestionResolution = {
      action: 'submit',
      answers: { q1: { values: ['stage'] }, q2: { values: ['web', 'worker'] } },
    };
    const cancelAll: GroupQuestionResolution = { action: 'cancel' };

    // 每次尝试以可重试失败结束，保持待处理以便下一次尝试
    for (const resolution of [submitOrdered, submitReordered, submitChanged, cancelAll]) {
      const attempt = service.resolve(key, resolution);
      const frameIndex = send.mock.calls.length - 1;
      service.handleFrame(failResponse(sentFrame(send, frameIndex).id, RETRYABLE_ERROR));
      await resolveFailure(attempt);
    }

    const [first, second, third, fourth] = [0, 1, 2, 3].map((call) => sentFrame(send, call));
    // 同载荷重试：幂等键相同、request id 不同
    expect(second.params.idempotencyKey).toBe(first.params.idempotencyKey);
    expect(second.id).not.toBe(first.id);
    // 回答变化与取消：新幂等键
    expect(third.params.idempotencyKey).not.toBe(first.params.idempotencyKey);
    expect(fourth.params.idempotencyKey).not.toBe(first.params.idempotencyKey);
    expect(fourth.params.idempotencyKey).not.toBe(third.params.idempotencyKey);

    // 期间无终态通知，问答始终待处理
    expect(listener).not.toHaveBeenCalled();
  });

  it('cancel 不附带 answers，成功后按终态移除', async () => {
    const { service, send, listener } = createService();
    service.handleFrame(interactionEvent({ payload: requestedPayload() }));
    const key = onlyKey(listener);
    listener.mockClear();

    const outcome = service.resolve(key, { action: 'cancel' });

    expect(send.mock.calls[0][0]).toEqual({
      type: 'req',
      id: expect.any(String),
      method: 'interaction.resolve',
      params: {
        bcsRunId: 'run1',
        interactionId: 'i1',
        idempotencyKey: expect.any(String),
        action: 'cancel',
      },
    });

    service.handleFrame(okResponse(sentFrame(send, 0).id));
    await expect(outcome).resolves.toBeUndefined();
    const snapshot = lastSnapshot(listener);
    expect(snapshot.reason).toBe('terminal');
    expect(snapshot.interactions).toHaveLength(0);
  });

  it('未连接或未知交互时拒绝提交且不发帧', async () => {
    const { service, send, listener, setConnected } = createService();
    service.handleFrame(interactionEvent({ payload: requestedPayload() }));
    const pendingKey = onlyKey(listener);
    listener.mockClear();

    setConnected(false);
    const disconnected = await resolveFailure(service.resolve(pendingKey, submitStage));
    expect(disconnected.code).toBe('DISCONNECTED');

    setConnected(true);
    const unknown = await resolveFailure(
      service.resolve(JSON.stringify(['run-somehow', 'missing']), submitStage),
    );
    expect(unknown.code).toBe('UNKNOWN_INTERACTION');

    expect(send).not.toHaveBeenCalled();
    expect(listener).not.toHaveBeenCalled();
    expect(jest.getTimerCount()).toBe(0);
  });

  it('断开重置：立即结束等待、清理定时器并区分 reset 快照', async () => {
    const { service, send, listener } = createService();
    service.handleFrame(interactionEvent({ payload: requestedPayload() }));
    const key = onlyKey(listener);
    listener.mockClear();

    const outcome = service.resolve(key, submitStage);
    const codes: string[] = [];
    outcome.then(
      () => codes.push('ok'),
      (error: GroupQuestionResolveError) => codes.push(error.code),
    );

    service.resetConnection();
    await flushMicrotasks();

    expect(codes).toEqual(['DISCONNECTED']);
    expect(jest.getTimerCount()).toBe(0);
    const snapshot = lastSnapshot(listener);
    expect(snapshot.reason).toBe('reset');
    expect(snapshot.interactions).toHaveLength(0);

    // 旧请求的迟到响应在新周期不再匹配
    expect(service.handleFrame(okResponse(sentFrame(send, 0).id))).toBe(false);

    // tombstone 与待处理被清理后，connect ACK 后的 replay 可恢复交互
    listener.mockClear();
    expect(service.handleFrame(interactionEvent({ payload: requestedPayload() }))).toBe(true);
    const replayed = lastSnapshot(listener);
    expect(replayed.reason).toBe('update');
    expect(replayed.interactions).toHaveLength(1);
  });

  it('重放恢复后复用重置前缓存周期的幂等键', async () => {
    const { service, send, listener } = createService();
    service.handleFrame(interactionEvent({ payload: requestedPayload() }));
    const key = onlyKey(listener);
    listener.mockClear();

    // 第一周期：可重试失败，为该回答内容建立幂等缓存
    const attempt = service.resolve(key, submitStage);
    service.handleFrame(failResponse(sentFrame(send, 0).id, RETRYABLE_ERROR));
    await resolveFailure(attempt);
    const idempotencyKeyBefore = sentFrame(send, 0).params.idempotencyKey;

    // 断开重连：重放 requested 恢复交互
    service.resetConnection();
    listener.mockClear();
    expect(service.handleFrame(interactionEvent({ payload: requestedPayload() }))).toBe(true);
    const replayedKey = onlyKey(listener);
    expect(replayedKey).toBe(JSON.stringify(['run1', 'i1']));

    const retried = service.resolve(replayedKey, submitStage);
    expect(sentFrame(send, 1).params.idempotencyKey).toBe(idempotencyKeyBefore);

    service.handleFrame(okResponse(sentFrame(send, 1).id));
    await expect(retried).resolves.toBeUndefined();
  });
});