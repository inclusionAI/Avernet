/**
 * B9 翻译层测试 —— oracle 驱动：真 OpenClawParser 是行为裁判，不做形状自说自话。
 * 输入 fixture 用真实引擎快照 __tests__/fixtures/engine-wire-snapshot.json
 * （由 engine wire.py 函数产出）；v3 钉形引用 B8 的 V3_FRAME_SHAPES。
 */
import * as fsmod from 'fs';
import * as path from 'path';
import type { WireEvent } from '@/adapters/a2a/stream';
import {
  translateEvent, type TranslateCtx,
} from '@/adapters/a2a/translate';
import { V3_FRAME_SHAPES, loadOpenClawParser } from './v3-frame-shapes.test';

const OpenClawParser = loadOpenClawParser();

const CTX: TranslateCtx = { sessionKey: 'agent:main:main', taskRunId: 't1' };

interface OracleResult { blocks: any[]; content: string; done: boolean }
type OracleParser = InstanceType<typeof OpenClawParser> & {
  onUpdate?: (r: OracleResult) => void;
  onComplete?: (r: OracleResult) => void;
  onError?: (e: Error) => void;
  handleMessage(m: Record<string, any>): boolean;
  getResult(): OracleResult;
  setSessionKey(k: string): void;
  updateApprovalStatus(id: string, s: 'approved' | 'rejected'): boolean;
  clearPendingApproval(): Record<string, any>[];
};

/** B10 Provider 配方：parser 以**同一 ctx.sessionKey** 配置（钉板 #2 会话门）。 */
function newOracle(ctx: TranslateCtx = CTX): OracleParser {
  const parser = new OpenClawParser({ enableThinking: true, generateId: () => 'test-msg-id' }) as OracleParser;
  parser.setSessionKey(ctx.sessionKey);
  return parser;
}

function rolling(fullText = ''): { fullText: string } {
  return { fullText };
}

/** 引擎快照 fixture → WireEvent（与 stream.ts extractMemberEvent 同口径）。
 * fixture 由真引擎 A2aKernel 产出（真实发布序，勿手改——重生成命令见
 * fixture 的 _header 字段 / scripts/gen-engine-wire-snapshot.py）。 */
const MEMBER_KEYS = ['task', 'message', 'statusUpdate', 'artifactUpdate'];
function fixtureEvents(): WireEvent[] {
  const file = path.join(__dirname, 'fixtures', 'engine-wire-snapshot.json');
  const parsed = JSON.parse(fsmod.readFileSync(file, 'utf8')) as {
    envelopes: Array<{ result: Record<string, unknown> }>;
  };
  return parsed.envelopes.map((env) => {
    const result = env.result as Record<string, unknown>;
    const type = MEMBER_KEYS.find((k) => Object.prototype.hasOwnProperty.call(result, k))!;
    const member = result[type] as Record<string, unknown>;
    const metadata = (member.metadata ?? {}) as Record<string, unknown>;
    const seq = typeof metadata.seq === 'number' ? metadata.seq : null;
    return { seq, type, member, metadata };
  });
}

/** 事件构造便捷件（wire.py 同构） */
function artifactUpdate(text: string, seq: number, opts: { append?: boolean; lastChunk?: boolean } = {}): WireEvent {
  return {
    seq, type: 'artifactUpdate',
    member: {
      taskId: 't1',
      artifact: { artifactId: `text-t1`, parts: [{ text }] },
      append: opts.append ?? true,
      lastChunk: opts.lastChunk ?? false,
      metadata: { seq },
    },
    metadata: { seq },
  };
}

function statusUpdate(state: string, seq: number, metadata: Record<string, unknown> = {}): WireEvent {
  return {
    seq, type: 'statusUpdate',
    member: { taskId: 't1', status: { state }, metadata: { seq, ...metadata } },
    metadata: { seq, ...metadata },
  };
}

// ─── (a) 全量快照序列：真 oracle 走到 final、文本累计不翻倍 ────────────────

it('(a) 引擎快照 fixture 全序列 → parser 到 final，累计文本 "你好！"（kernel 真实发布序）', () => {
  const parser = newOracle();
  const onUpdate = jest.fn();
  const onComplete = jest.fn();
  parser.onUpdate = onUpdate;
  parser.onComplete = onComplete;

  const state = rolling();
  const events = fixtureEvents();
  // fixture 形状自检（防静默漂移）：7 行 = task + WORKING + 3 段 artifact
  // （首块 append=false / 追加 append=true / publish_final 全量替换）+
  // complete_run 的 state-change@N+1 非 final + **同 seq** final marker
  expect(events).toHaveLength(7);
  const kinds: Array<string | null> = [];
  for (const ev of events) {
    const out = translateEvent(ev, CTX, state);
    kinds.push(out ? out.kind : null);
    // eslint-disable-next-line jest/no-conditional-expect -- 钉板逐帧驱动：有渲染面的事件才喂真 parser
    if (out) expect(parser.handleMessage(out.frame)).toBe(true);
  }
  // task 快照无渲染面；WORKING 心跳无渲染面；state-change(COMPLETED)@N+1
  // 非 final 无渲染面；terminal marker 在 translate 层恒 kind=final
  expect(kinds).toEqual([null, null, 'delta', 'delta', 'delta', null, 'final']);

  expect(onComplete).toHaveBeenCalledTimes(1);
  const finalResult = onComplete.mock.calls[0][0];
  expect(finalResult.done).toBe(true);
  expect(finalResult.blocks).toEqual([
    expect.objectContaining({ type: 'text', content: '你好！' }),
  ]);
  // B10 applied-cursor 基线：滚动全文与 parser 渲染一致
  expect(state.fullText).toBe('你好！');
});

// ─── (b) engine_frame 无损通道：生命周期事件直通 ──────────────────────────

it('(b) statusUpdate kind=agent + original_event + engine_frame → passthrough 整帧，tool 块出现', () => {
  const toolFrame = JSON.parse(JSON.stringify(V3_FRAME_SHAPES.agentToolStart.frame));
  const engineFrame = toolFrame.payload;    // wire.py：engine_frame=内层 payload
  const ev = statusUpdate('TASK_STATE_WORKING', 7, {
    kind: 'agent', original_event: 'agent', engine_frame: engineFrame,
  });

  const out = translateEvent(ev, CTX, rolling())!;
  expect(out.kind).toBe('passthrough');
  expect(out.consumedSeq).toBe(7);
  // 钉板 #5：engine_frame 是内层 payload——信封由此重建，payload 原样直通
  expect(out.frame).toEqual({ type: 'event', event: 'agent', seq: 7, payload: engineFrame });
  expect(out.frame.payload).toEqual(engineFrame);

  const parser = newOracle();
  expect(parser.handleMessage(out.frame)).toBe(true);
  expect(parser.getResult().blocks).toEqual([
    { type: 'tool_execution', steps: [expect.objectContaining({ id: 'call-1', tool: 'Bash', status: 'running' })] },
  ]);
});

it('(b2) thinking → lifecycle end 无损直通：块关闭 loading（多 original_event 兼路透传）', () => {
  const parser = newOracle();
  const state = rolling();
  const thinkingEv = statusUpdate('TASK_STATE_WORKING', 8, {
    kind: 'agent', original_event: 'agent',
    engine_frame: JSON.parse(JSON.stringify(V3_FRAME_SHAPES.agentThinking.frame)).payload,
  });
  const endEv = statusUpdate('TASK_STATE_WORKING', 9, {
    kind: 'agent', original_event: 'agent',
    engine_frame: JSON.parse(JSON.stringify(V3_FRAME_SHAPES.agentLifecycleEnd.frame)).payload,
  });
  for (const ev of [thinkingEv, endEv]) {
    const out = translateEvent(ev, CTX, state)!;
    expect(out.kind).toBe('passthrough');
    expect(parser.handleMessage(out.frame)).toBe(true);
  }
  expect(parser.getResult().blocks[0]).toMatchObject({ type: 'thinking', isLoading: false });
});

it('(b3) mode_transition.resolved 等未收录事件名也原样透传（oracle 承认不渲染的 no-op）', () => {
  const engineFrame = { runId: 'run-1', sessionKey: 'agent:main:main', mode: 'plan' };
  const ev = statusUpdate('TASK_STATE_WORKING', 10, {
    kind: 'agent', original_event: 'mode_transition.resolved', engine_frame: engineFrame,
  });
  const out = translateEvent(ev, CTX, rolling())!;
  expect(out.kind).toBe('passthrough');
  expect(out.frame).toEqual({
    type: 'event', event: 'mode_transition.resolved', seq: 10, payload: engineFrame,
  });
  // oracle 不认识该事件名：承认帧但不渲染（B8 agentNoOpStream 同构）
  const parser = newOracle();
  expect(parser.handleMessage(out.frame)).toBe(false);
  expect(parser.getResult().blocks).toEqual([]);
});

// ─── (c) delta 累计：滚动全文，无双写 ────────────────────────────────────

it('(c) 两个增量事件 → content 递进（"你"→"你好"），oracle 前缀差分不双写', () => {
  const state = rolling();
  const out1 = translateEvent(artifactUpdate('你', 1), CTX, state)!;
  const out2 = translateEvent(artifactUpdate('好', 2), CTX, state)!;
  expect(out1.kind).toBe('delta');
  expect(out2.kind).toBe('delta');
  // 钉板 #3：content 必须是累计快照——增量字段 oracle 根本不读
  expect(out1.frame.payload.message.content).toEqual([{ type: 'text', text: '你' }]);
  expect(out2.frame.payload.message.content).toEqual([{ type: 'text', text: '你好' }]);
  expect(out1.frame.payload.state).toBe('delta');

  // 每个合成帧四件套齐备：sessionKey（#2）/request（#4）/runId/seq
  for (const out of [out1, out2]) {
    expect(out.frame.payload.sessionKey).toBe(CTX.sessionKey);
    expect(typeof out.frame.payload.request).toBe('object');
    expect(out.frame.payload.runId).toBe('t1');
    expect(out.frame.payload.seq).toBe(out.consumedSeq);
  }

  const parser = newOracle();
  expect(parser.handleMessage(out1.frame)).toBe(true);
  expect(parser.handleMessage(out2.frame)).toBe(true);
  expect(parser.getResult().blocks).toEqual([
    expect.objectContaining({ type: 'text', content: '你好' }),   // 不是 "你你好"
  ]);
  expect(state.fullText).toBe('你好');
});

it('(c2) append=false = 终稿全量替换（kernel.publish_final）：fullText 换血不追加', () => {
  const parser = newOracle();
  const state = rolling();
  const d1 = translateEvent(artifactUpdate('你', 1), CTX, state)!;
  const d2 = translateEvent(artifactUpdate('好', 2), CTX, state)!;
  const d3 = translateEvent(artifactUpdate('你好！', 3, { append: false, lastChunk: true }), CTX, state)!;
  // 换血：滚动全文=终稿全量（不是 "你好你好！"）
  expect(state.fullText).toBe('你好！');
  expect(d3.frame.payload.message.content).toEqual([{ type: 'text', text: '你好！' }]);

  for (const out of [d1, d2, d3]) expect(parser.handleMessage(out.frame)).toBe(true);
  // oracle 前缀差分：'你好' 之后替换终稿只补 "！" —— 绝无双写
  expect(parser.getResult().blocks).toEqual([
    expect.objectContaining({ type: 'text', content: '你好！' }),
  ]);
});

// ─── (d) 失败终态 → parser onError 路径 ──────────────────────────────────

it('(d) final FAILED + errorCode/errorMessage → chatError 帧，oracle 走 onError', () => {
  const engineFrame = { runId: 'run-1', state: 'error', errorMessage: 'model provider timeout' };
  const ev = statusUpdate('TASK_STATE_FAILED', 12, {
    final: 'true', lastSeq: 12,
    errorCode: 'E_PROVIDER_TIMEOUT', errorMessage: 'model provider timeout',
    engine_frame: engineFrame,
  });
  const out = translateEvent(ev, CTX, rolling())!;
  expect(out.kind).toBe('error');
  expect(out.consumedSeq).toBe(12);
  // 钉板 #8：state:'error' + errorMessage + errorCode（+optional engine_frame）
  expect(out.frame.event).toBe('chat');
  expect(out.frame.payload).toMatchObject({
    state: 'error',
    errorMessage: 'model provider timeout',
    errorCode: 'E_PROVIDER_TIMEOUT',
    engine_frame: engineFrame,
    sessionKey: CTX.sessionKey,
  });

  const parser = newOracle();
  const onError = jest.fn();
  const onComplete = jest.fn();
  parser.onError = onError;
  parser.onComplete = onComplete;
  expect(parser.handleMessage(out.frame)).toBe(false);
  expect(onError).toHaveBeenCalledTimes(1);
  expect(onError.mock.calls[0][0].message).toBe('model provider timeout');
  expect(onComplete).not.toHaveBeenCalled();
});

// ─── (e) 会话门双向 ──────────────────────────────────────────────────────

it('(e) sessionKey 门：同 ctx 配置的 parser 放行；异 sessionKey 的 parser 拒收', () => {
  const out = translateEvent(artifactUpdate('你', 1), CTX, rolling())!;
  // 翻译层责任：stamp 的一定是 ctx.sessionKey（正确的一侧）
  expect(out.frame.payload.sessionKey).toBe(CTX.sessionKey);

  const rightParser = newOracle();        // B10 配方：setSessionKey(CTX.sessionKey)
  expect(rightParser.handleMessage(out.frame)).toBe(true);
  expect(rightParser.getResult().blocks).toEqual([
    expect.objectContaining({ type: 'text', content: '你' }),
  ]);

  const foreignParser = newOracle({ sessionKey: 'app-bot:bot-2:other' });
  expect(foreignParser.handleMessage(out.frame)).toBe(false);      // 钉板 #2：不匹配整帧丢弃
  expect(foreignParser.getResult().blocks).toEqual([]);
});

// ─── (f) 纯 WORKING 心跳 ─────────────────────────────────────────────────

it('(f) 非 final 无 engine_frame 的 statusUpdate → null（parser 不需要）', () => {
  const ev = statusUpdate('TASK_STATE_WORKING', 3);
  expect(translateEvent(ev, CTX, rolling())).toBeNull();
});

// ─── (g) message 成员 ────────────────────────────────────────────────────

it('(g) message 成员事件（context fanout）→ null（documented out-of-scope）', () => {
  const ev: WireEvent = {
    seq: null, type: 'message',
    member: { messageId: 'm1', contextId: 'c1', role: 'user', parts: [{ text: 'push' }], metadata: {} },
    metadata: {},
  };
  expect(translateEvent(ev, CTX, rolling())).toBeNull();
});

it('(g2) task 成员事件（run 建立快照）→ null（v3 无 run-start 播报面）', () => {
  const ev: WireEvent = {
    seq: null, type: 'task',
    member: { id: 't1', contextId: 'c1', status: { state: 'TASK_STATE_WORKING' }, metadata: {} },
    metadata: {},
  };
  expect(translateEvent(ev, CTX, rolling())).toBeNull();
});

// ─── HITL：INPUT_REQUIRED + engine_frame → exec.approval.requested（钉板 #6） ──

it('HITL INPUT_REQUIRED → exec.approval.requested 整帧：oracle 建 approval 块并阻塞', () => {
  const approvalPayload = JSON.parse(JSON.stringify(V3_FRAME_SHAPES.approvalRequested.frame)).payload;
  const ev = statusUpdate('TASK_STATE_INPUT_REQUIRED', 20, {
    interactionId: 'apr-1',
    engine_frame: { ...approvalPayload, runId: 'run-1', sessionKey: CTX.sessionKey, state: 'approval_requested' },
  });
  const out = translateEvent(ev, CTX, rolling())!;
  expect(out.kind).toBe('passthrough');
  expect(out.frame.event).toBe('exec.approval.requested');
  expect(out.frame.payload.request).toEqual(approvalPayload.request);   // v3 审批 payload 原样
  expect(out.consumedSeq).toBe(20);

  const parser = newOracle();
  expect(parser.handleMessage(out.frame)).toBe(true);
  expect(parser.getResult()).toMatchObject({ isApproval: true });
  expect(parser.getResult().blocks).toEqual([
    expect.objectContaining({ type: 'approval', approvalId: 'apr-1', status: 'pending' }),
  ]);
  // 审批期间 chat 帧被拦（B8 approvalRequested 钉条同构）
  const delta = translateEvent(artifactUpdate('x', 21), CTX, rolling())!;
  expect(parser.handleMessage(delta.frame)).toBe(false);
});

it('HITL engine_frame 缺 request 时兜底合成（钉板 #4：缺失 parser 直接 throw）', () => {
  const ev = statusUpdate('TASK_STATE_INPUT_REQUIRED', 22, {
    interactionId: 'apr-9',
    engine_frame: { id: 'apr-9', sessionKey: CTX.sessionKey },       // 破约：无 request
  });
  const out = translateEvent(ev, CTX, rolling())!;
  expect(out.frame.payload.request).toEqual({ sessionKey: CTX.sessionKey });
  const parser = newOracle();
  expect(() => parser.handleMessage(out.frame)).not.toThrow();
  expect(parser.getResult().blocks).toEqual([
    expect.objectContaining({ type: 'approval', approvalId: 'apr-9' }),
  ]);
});

it('interaction.resolved → chat 透传帧（B8 approvalResolved 钉形），且帧本身释放不了审批', () => {
  const parser = newOracle();
  // 先建 pending 审批
  const requested = translateEvent(statusUpdate('TASK_STATE_INPUT_REQUIRED', 30, {
    interactionId: 'apr-1',
    engine_frame: { ...JSON.parse(JSON.stringify(V3_FRAME_SHAPES.approvalRequested.frame)).payload,
                    sessionKey: CTX.sessionKey },
  }), CTX, rolling())!;
  parser.handleMessage(requested.frame);

  // resolved 事件 → chat 帧（engine 重包语义）
  const resolvedEv = statusUpdate('TASK_STATE_WORKING', 31, {
    kind: 'interaction.resolved', interactionId: 'apr-1',
    engine_frame: { runId: 't1', sessionKey: CTX.sessionKey, state: 'approval_resolved', approvalId: 'apr-1' },
  });
  const out = translateEvent(resolvedEv, CTX, rolling())!;
  expect(out.kind).toBe('passthrough');
  expect(out.frame).toEqual({
    type: 'event', event: 'chat', seq: 31,
    payload: { runId: 't1', sessionKey: CTX.sessionKey, state: 'approval_resolved', approvalId: 'apr-1' },
  });
  // B8 approvalResolved 钉注：帧被审批阻塞拦下，释放必须走两步 parser API
  expect(parser.handleMessage(out.frame)).toBe(false);
  expect(parser.getResult().isApproval).toBe(true);
  expect(parser.updateApprovalStatus('apr-1', 'approved')).toBe(true);
  const drained = parser.clearPendingApproval();
  expect(drained).toHaveLength(1);
});

// ─── 终态旁路 ─────────────────────────────────────────────────────────────

it('R5-2 多审批部分决议：INPUT_REQUIRED + interaction.resolved → chat（不得误译成新审批请求）', () => {
  // wire.py wire_interaction_resolved docstring（R5-2）：多 pending 部分决议后
  // run 仍 INPUT_REQUIRED，帧状态是真实状态；"是否 resolved"只看 meta.kind，
  // 不依赖帧状态。翻译顺序若先判状态，会把本帧误译成 exec.approval.requested
  // ——真实 SDK 会新增 pending approval 并覆盖当前 pendingApproval。
  const parser = newOracle();
  // 先建 pending 审批（多 pending 场景中的一员已决议，剩余成员未决议）
  const requested = translateEvent(statusUpdate('TASK_STATE_INPUT_REQUIRED', 30, {
    interactionId: 'apr-1',
    engine_frame: { ...JSON.parse(JSON.stringify(V3_FRAME_SHAPES.approvalRequested.frame)).payload,
                    sessionKey: CTX.sessionKey },
  }), CTX, rolling())!;
  parser.handleMessage(requested.frame);
  expect(parser.getResult().blocks).toEqual([
    expect.objectContaining({ type: 'approval', approvalId: 'apr-1', status: 'pending' }),
  ]);

  // apr-2 决议完毕，但还有别的 pending → 帧状态仍 INPUT_REQUIRED
  const resolvedEv = statusUpdate('TASK_STATE_INPUT_REQUIRED', 31, {
    kind: 'interaction.resolved', interactionId: 'apr-2',
    engine_frame: { runId: 't1', sessionKey: CTX.sessionKey, state: 'approval_resolved', approvalId: 'apr-2' },
  });
  const out = translateEvent(resolvedEv, CTX, rolling())!;
  expect(out.kind).toBe('passthrough');
  expect(out.frame).toEqual({
    type: 'event', event: 'chat', seq: 31,
    payload: { runId: 't1', sessionKey: CTX.sessionKey, state: 'approval_resolved', approvalId: 'apr-2' },
  });
  // 不产生新 pending approval：真实 SDK 收到 exec.approval.requested 会 append/覆盖
  // pendingApproval —— resolved payload 缺 request.id 时还会生成残缺审批。
  // pending 仍只有残留成员 apr-1；释放流程归 WORKING 分支既有测试，此处不复述。
  expect(parser.handleMessage(out.frame)).toBe(false);
  expect(parser.getResult().blocks).toEqual([
    expect.objectContaining({ type: 'approval', approvalId: 'apr-1', status: 'pending' }),
  ]);
});

it('CANCELED 终态 → kind final + chat aborted 帧：oracle 纯 no-op（不 onError/onComplete）', () => {
  const ev = statusUpdate('TASK_STATE_CANCELED', 40, { final: 'true', lastSeq: 40 });
  const state = rolling();
  const out = translateEvent(ev, CTX, state)!;
  expect(out.kind).toBe('final');
  expect(out.frame.payload).toMatchObject({ state: 'aborted', runId: 't1' });

  const parser = newOracle();
  const onError = jest.fn();
  const onComplete = jest.fn();
  parser.onError = onError;
  parser.onComplete = onComplete;
  expect(parser.handleMessage(out.frame)).toBe(true);      // B8 chatAborted：承认帧、零副作用
  expect(onError).not.toHaveBeenCalled();
  expect(onComplete).not.toHaveBeenCalled();
  expect(parser.getResult().blocks).toEqual([]);
});

it('无 artifact 直接 final：chatFinal 无正文也触发 onComplete（B8 chatFinal 钉注）', () => {
  const ev = statusUpdate('TASK_STATE_COMPLETED', 50, { final: 'true', lastSeq: 50 });
  const out = translateEvent(ev, CTX, rolling())!;
  expect(out.kind).toBe('final');
  const parser = newOracle();
  const onComplete = jest.fn();
  parser.onComplete = onComplete;
  expect(parser.handleMessage(out.frame)).toBe(true);
  expect(onComplete).toHaveBeenCalledTimes(1);
  expect(onComplete.mock.calls[0][0].done).toBe(true);
});

it('final 帧与 B8 chatFinal 钉形对齐（钉条优先于一切猜测）', () => {
  const state = rolling();
  translateEvent(artifactUpdate('你好！', 1), CTX, state);
  const out = translateEvent(statusUpdate('TASK_STATE_COMPLETED', 2, { final: 'true' }), CTX, state)!;
  const pinned = V3_FRAME_SHAPES.chatFinal.frame.payload;
  // 语义面完全一致：state/message/runId/sessionKey——request/seq 是钉板允许的 engine 侧字段
  expect(out.frame.payload).toMatchObject({
    state: pinned.state, sessionKey: pinned.sessionKey, message: pinned.message,
  });
  expect(out.frame.type).toBe('event');
  expect(out.frame.event).toBe('chat');
});

// ─── taskRunId 兜底 ──────────────────────────────────────────────────────

it('member 不带 taskId 时 runId 回落 ctx.taskRunId（钉板：snapshot 成员恒带 taskId，防御位）', () => {
  const ev: WireEvent = {
    seq: 60, type: 'artifactUpdate',
    member: { artifact: { artifactId: 'a', parts: [{ text: 'x' }] }, append: true, metadata: { seq: 60 } },
    metadata: { seq: 60 },
  };
  const out = translateEvent(ev, CTX, rolling())!;
  expect(out.frame.payload.runId).toBe('t1');
});
