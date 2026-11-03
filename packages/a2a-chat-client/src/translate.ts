/**
 * B9 — A2A wire 事件 → v3 ws 帧翻译层（纯函数，零网络）。
 * ============================================================================
 *
 * 上游：stream.ts 的 WireEvent（A2A SSE 单成员事件，engine wire.py 真实口径）。
 * 下游：OpenClawParser.handleMessage —— 吃**整条 ws 帧**（B8 钉板契约 #1：
 *   `{type:'event', event:'agent'|'chat'|'exec.approval.requested'|..., payload}`
 *   或 `{type:'res', ok, payload?, error?}`）。
 *
 * 事件类 → kind 映射表（唯一事实源，所有分支按此核销）：
 * ┌─────────────────────────────────────────┬──────────────┬──────────────────────────┐
 * │ A2A wire 事件                            │ kind         │ v3 帧                    │
 * ├─────────────────────────────────────────┼──────────────┼──────────────────────────┤
 * │ task（run 建立快照，metadata={}）       │ null          │ 无 —— 传输层生命周期，   │
 * │                                         │               │ v3 无对应播报面          │
 * │ message（context 级 fanout/inject 推送） │ null          │ out-of-scope：v3 inject  │
 * │                                         │               │ 帧走 runId='inject-*'    │
 * │                                         │               │ 独立作用域，由 B10 的    │
 * │                                         │               │ context 订阅通道处理     │
 * │ artifactUpdate append=true（含 (T,T)）  │ delta         │ chat delta 帧，content=  │
 * │                                         │               │ **累计快照**（钉板 #3）   │
 * │ artifactUpdate append=false（终稿全量）  │ delta         │ 同上；fullText 整体替换  │
 * │                                         │               │ （kernel.publish_final） │
 * │ statusUpdate final='true' COMPLETED     │ final         │ chat final 帧（钉板 #7，│
 * │                                         │               │ 与 B8 chatFinal 钉条对齐）│
 * │ statusUpdate final='true' CANCELED/      │ final         │ chat **aborted** 帧——   │
 * │ REJECTED                                │               │ oracle 纯 no-op（B8     │
 * │                                         │               │ chatAborted：取消清理归  │
 * │                                         │               │ 传输层，不喂 onComplete）│
 * │ statusUpdate final='true' FAILED（或携   │ error         │ chatError 帧（钉板 #8：  │
 * │ errorCode/errorMessage）                │               │ state:'error' → onError）│
 * │ statusUpdate INPUT_REQUIRED + engine_    │ passthrough   │ exec.approval.requested 整│
 * │ frame（wire_interaction_requested）      │               │帧=engine_frame（钉板 #6） │
 * │ statusUpdate kind='agent' + original_    │ passthrough   │ {type:'event',event:      │
 * │ event + engine_frame（agent 进度无损道） │               │ original_event, payload=  │
 * │                                         │               │ engine_frame}（钉板 #5）  │
 * │ statusUpdate kind='interaction.resolved' │ passthrough   │ chat 帧（engine 把决议重 │
 * │ + engine_frame                           │               │ 包成 chat——B8            │
 * │                                         │               │ approvalResolved 钉形）。 │
 * │                                         │               │ ⚠️ 帧释放不了审批（会被  │
 * │                                         │               │ shouldBlockMessage 拦）； │
 * │                                         │               │ 真释放=B10 走 parser 两步 │
 * │                                         │               │ API（updateApprovalStatus │
 * │                                         │               │ +clearPendingApproval）  │
 * │ statusUpdate 非 final、无 engine_frame   │ null          │ 钉板 #9：纯 WORKING 心跳，│
 * │                                         │               │ parser 不需要            │
 * └─────────────────────────────────────────┴──────────────┴──────────────────────────┘
 *
 * 钉板红线（B8 __tests__/v3-frame-shapes.test.ts，违反=bug）：
 *   #2 会话门：parser 按 `parser.sessionKey.includes(payload.sessionKey)`
 *      丢帧 → 合成帧一律 stamp ctx.sessionKey（由 B10 用**同一 ctx.sessionKey**
 *      配置 parser，等值必然通过包含式互查）。
 *   #3 oracle 渲染 chat 只读 payload.message.content **累计快照**（不读 delta）——
 *      A2A artifactUpdate 给的是增量（append=true），本层维护滚动全文并输出
 *      content=fullText；append=false 时 parts 文本即终稿全量（kernel.publish_final
 *      R12-7 快照替换语义），整体替换而非追加。
 *   #4 exec.approval.requested 的 payload.request REQUIRED（缺失 parser 直接
 *      throw）→ INPUT_REQUIRED 帧对缺失 request 的 engine_frame 兜底合成。
 *
 * state.fullText 契约：**translate 会就地更新它**（不可变换新对象做不到跨帧滚动，
 * Provider B10 传入共享对象、读它做 applied-cursor 基线）——artifactUpdate 事件
 * （追加/替换）是唯一会改写它的路径；final/error/aborted 帧只读它作为终态快照。
 * 返回 null 表示"无帧可喂"（parser 不需要）；此时事件游标仍由 B10 直接取 ev.seq。
 */
import type { WireEvent } from './stream';

export interface TranslateCtx {
  /** 会话键——合成帧的 sessionKey 源；B10 必须用它 setSessionKey(parser)（钉板 #2） */
  sessionKey: string;
  /** 传输层 run id 兜底：statusUpdate/artifactUpdate member.taskId 优先 */
  taskRunId?: string | null;
}

export type TranslateKind = 'passthrough' | 'delta' | 'final' | 'error';

export interface TranslateOutput {
  /** 整条 v3 ws 帧，parser.handleMessage-ready（含帧头，非裸 payload） */
  frame: Record<string, any>;
  kind: TranslateKind;
  /** 本事件的 metadata.seq——provider 恢复游标（B10 applied-cursor 用） */
  consumedSeq: number | null;
}

/** engine wire.py TERMINAL_STATES 口径（COMPLETED 终态由 A2AProvider.terminalStateOf
 *  直读成员 status.state，translate 无分支消费——nextgen noUnusedLocals 不留死常量） */
const STATE_FAILED = 'TASK_STATE_FAILED';
const STATE_CANCELED = 'TASK_STATE_CANCELED';
const STATE_REJECTED = 'TASK_STATE_REJECTED';
const STATE_INPUT_REQUIRED = 'TASK_STATE_INPUT_REQUIRED';

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}

/** 合成帧的 runId：member.taskId（statusUpdate/artifactUpdate 成员恒带）→ ctx.taskRunId */
function runIdOf(member: Record<string, unknown>, ctx: TranslateCtx): string {
  const taskId = member.taskId;
  if (typeof taskId === 'string' && taskId) return taskId;
  return typeof ctx.taskRunId === 'string' && ctx.taskRunId ? ctx.taskRunId : 'a2a-run';
}

/**
 * 钉板 #4：合成帧必带最小 request。parser 只在 exec.approval.requested 路径
 * 实际读它（handleApprovalRequest: req.resolvedPath||req.command），chat 路径
 * 不读但按 B8 钉条字段形状补齐，保帧形状与 engine v3 同构。
 */
function synthRequest(ctx: TranslateCtx): Record<string, unknown> {
  return { sessionKey: ctx.sessionKey };
}

/** artifactUpdate.parts 拼接净文本（engine wire 单 part text） */
function partsText(member: Record<string, unknown>): string | null {
  const artifact = member.artifact;
  if (!isPlainObject(artifact)) return null;
  const parts = artifact.parts;
  if (!Array.isArray(parts)) return null;
  return parts
    .map((p) => (isPlainObject(p) && typeof p.text === 'string' ? p.text : ''))
    .join('');
}

/** 合成 chat 帧公共骨架：sessionKey（钉板 #2）+ request（钉板 #4）+ runId + seq 帧顶/payload 双带（engine 帧口径） */
function chatFrame(
  ev: WireEvent, runId: string, ctx: TranslateCtx,
  payload: Record<string, unknown>,
): Record<string, any> {
  return {
    type: 'event',
    event: 'chat',
    seq: ev.seq,
    payload: {
      runId,
      sessionKey: ctx.sessionKey,          // 钉板 #2：B10 以同一 sessionKey 配 parser
      request: synthRequest(ctx),          // 钉板 #4：request REQUIRED
      seq: ev.seq,
      ...payload,
    },
  };
}

/** artifactUpdate → 滚动全文 + chat delta 帧（content=累计快照，钉板 #3）。 */
function translateArtifact(
  ev: WireEvent, ctx: TranslateCtx, state: { fullText: string },
): TranslateOutput | null {
  const text = partsText(ev.member);
  if (text === null) return null;              // 协议破坏：无 parts 结构，无物可渲
  // kernel.publish_final（R12-7）：append=false + lastChunk=true = 终稿全量替换；
  // 其余（含 (T,T) final chunk）一律追加。
  const rolling = ev.member.append === false ? text : state.fullText + text;
  state.fullText = rolling;                    // 契约：artifactUpdate 独占改写权
  if (rolling === '') return null;             // 空串无渲染面

  return {
    kind: 'delta',
    // 钉板 #3：oracle 只渲染 message.content 累计快照（delta 字段不读），
    // 自做前缀差分——这里必须给滚动全文，绝不能给增量。
    frame: chatFrame(ev, runIdOf(ev.member, ctx), ctx, {
      state: 'delta',
      message: { role: 'assistant', content: [{ type: 'text', text: rolling }] },
    }),
    consumedSeq: ev.seq,
  };
}

/** 终态 completed → chat final 帧（钉板 #7：B8 chatFinal 钉形——state/message 决胜负）。 */
function translateFinal(
  ev: WireEvent, ctx: TranslateCtx, state: { fullText: string },
): TranslateOutput {
  return {
    kind: 'final',
    frame: chatFrame(ev, runIdOf(ev.member, ctx), ctx, {
      state: 'final',
      // 终态全文快照：final 信号本身不依赖正文（B8 chatFinal 钉注），但带上
      // 滚动全文与钉形对齐；若终稿 artifact 已来过，oracle 前缀差分自然幂等。
      message: { role: 'assistant', content: [{ type: 'text', text: state.fullText }] },
    }),
    consumedSeq: ev.seq,
  };
}

/** 终态 canceled/rejected → chat aborted 帧（B8 chatAborted：oracle 无取消分支，纯 no-op；清理归 B10）。 */
function translateAborted(ev: WireEvent, ctx: TranslateCtx): TranslateOutput {
  return {
    kind: 'final',                              // run 已终：游标推进 + 停流语义不变
    frame: chatFrame(ev, runIdOf(ev.member, ctx), ctx, { state: 'aborted' }),
    consumedSeq: ev.seq,
  };
}

/** 失败终态 → chatError 帧（钉板 #8：state:'error' 与 event 名无关地进 onError）。 */
function translateError(ev: WireEvent, metadata: Record<string, unknown>, ctx: TranslateCtx): TranslateOutput {
  const errorMessage = typeof metadata.errorMessage === 'string' ? metadata.errorMessage : undefined;
  const errorCode = typeof metadata.errorCode === 'string' ? metadata.errorCode : undefined;
  const engineFrame = isPlainObject(metadata.engine_frame) ? metadata.engine_frame : undefined;
  // B8 chatError 钉条：errorMessage 缺省由 parser 自己回退 'Unknown error'；
  // engine_frame 为 optional 透传（失败详情的 lossless 记录）。
  return {
    kind: 'error',
    frame: chatFrame(ev, runIdOf(ev.member, ctx), ctx, {
      state: 'error',
      ...(errorMessage !== undefined ? { errorMessage } : {}),
      ...(errorCode !== undefined ? { errorCode } : {}),
      ...(engineFrame !== undefined ? { engine_frame: engineFrame } : {}),
    }),
    consumedSeq: ev.seq,
  };
}

/**
 * statusUpdate 带 engine_frame 的无损通道（钉板 #5/#6）。
 * 注意 wire.py `wire_agent_progress` 的 engine_frame = dict(payload) —— 是**内层
 * payload**，不含 {type,event} 帧；外层信封由此处重建：
 *   event 名 ← metadata.original_event（R17-2 保真）；mapping 见 B8 钉板事件表
 *   ('agent'|'chat'|'exec.approval.requested' 由 oracle handleMessage 收，其余
 *   如 mode_transition.* 是承认但不渲染的 no-op，原样透传)。
 */
function translateEngineFrame(
  ev: WireEvent, metadata: Record<string, unknown>, ctx: TranslateCtx,
): TranslateOutput | null {
  const engineFrame = metadata.engine_frame;
  if (!isPlainObject(engineFrame)) return null;

  const memberState = isPlainObject(ev.member.status) && typeof ev.member.status.state === 'string'
    ? ev.member.status.state : '';

  let eventName: string;
  let payload: Record<string, unknown> = engineFrame;
  if (typeof metadata.original_event === 'string' && metadata.original_event) {
    // wire_agent_progress：agent / mode_transition.* 等
    eventName = metadata.original_event;
  } else if (metadata.kind === 'interaction.resolved') {
    // wire_interaction_resolved：engine 把决议重包成 chat 事件（gateway_client
    // on_approval_resolved → EventFrame(event='chat')，engine_frame 内含
    // state:'approval_resolved'）。与 B8 approvalResolved 钉形完全同构。
    // ⚠️ 该帧**释放不了** pending approval（被 shouldBlockMessage 拦下后回放仍旧
    // return false）——真释放是 B10 的两步 parser API：
    // updateApprovalStatus(id, status) + clearPendingApproval()。
    // 判定顺序：R5-2（wire_interaction_resolved docstring）多 pending 部分决议后
    // run 仍是 INPUT_REQUIRED——"是否 resolved"只看 meta.kind，不依赖帧状态，
    // 必须先于状态分支命中；否则该帧被误译成 exec.approval.requested，
    // 真实 SDK 会新增 pending approval 并覆盖当前 pendingApproval。
    eventName = 'chat';
  } else if (memberState === STATE_INPUT_REQUIRED) {
    // wire_interaction_requested（钉板 #6）：审批 v3 payload 全量 in engine_frame；
    // event 名按钉板 #6 = 'exec.approval.requested'。requested 帧无 kind 字段，
    // 不会误入上面的 resolved 分支。
    eventName = 'exec.approval.requested';
    if (!isPlainObject(engineFrame.request)) {
      // 钉板 #4：审批帧 request 缺失时 parser handleApprovalRequest 直接 throw
      // ——engine 侧破了约就兜底合成，保下游不炸。
      payload = { ...engineFrame, request: synthRequest(ctx) };
    }
  } else {
    // 兜底：engine_frame 存在但无路由名——按 wire_agent_progress 默认事件名 'agent'
    eventName = 'agent';
  }

  return {
    kind: 'passthrough',
    frame: { type: 'event', event: eventName, seq: ev.seq, payload },
    consumedSeq: ev.seq,
  };
}

function translateStatus(
  ev: WireEvent, ctx: TranslateCtx, state: { fullText: string },
): TranslateOutput | null {
  const metadata = ev.metadata;
  const memberState = isPlainObject(ev.member.status) && typeof ev.member.status.state === 'string'
    ? ev.member.status.state : '';
  const isFinal = metadata.final === 'true';
  const hasError = typeof metadata.errorCode === 'string' && metadata.errorCode !== '';

  // 终态 marker（wire_terminal_marker）：final 优先于一切。
  // FAILED 的 detail（errorCode/errorMessage/engine_frame）随 marker 下发，钉板 #8。
  // wire 口径里 detail 只随失败终态走；final 帧携 errorCode 即按失败核销（防御位）。
  if (isFinal) {
    if (memberState === STATE_FAILED || hasError) return translateError(ev, metadata, ctx);
    if (memberState === STATE_CANCELED || memberState === STATE_REJECTED) {
      return translateAborted(ev, ctx);
    }
    // COMPLETED（或终态状态缺失的防御位）：final 信号，钉板 #7。
    return translateFinal(ev, ctx, state);
  }
  // 非 final 但携 errorCode：wire 口径中 errorCode 只随终态——出现即按失败核销
  if (hasError) return translateError(ev, metadata, ctx);

  // 非 final 带 engine_frame：无损通道（agent 进度 / HITL / 审批决议）
  const passthrough = translateEngineFrame(ev, metadata, ctx);
  if (passthrough) return passthrough;

  // 钉板 #9：纯 WORKING 心跳（无 engine_frame、非终态）——parser 不需要，skip。
  return null;
}

/**
 * A2A wire 事件 → v3 帧。返回 null = 该事件无渲染面（task/message 成员、
 * 纯 WORKING 心跳）：B10 直接以 ev.seq 推进游标即可，不产出帧。
 */
export function translateEvent(
  ev: WireEvent, ctx: TranslateCtx, state: { fullText: string },
): TranslateOutput | null {
  // task 成员：run 建立快照（member.id/status.state，metadata={} 无 seq）——
  // 传输层生命周期信号，v3 无对应播报面（v3 不播 run start）。
  if (ev.type === 'task') return null;

  // message 成员（SubscribeToContext 推送 / wire_inject_message）：context 级
  // fanout，不经本 run 流；v3 的 inject 帧是 runId='inject-*' 的独立作用域
  // （B8 injectChat 钉条），由 B10 的 context 订阅通道另行消费——超出本层范围。
  if (ev.type === 'message') return null;

  if (ev.type === 'artifactUpdate') return translateArtifact(ev, ctx, state);
  if (ev.type === 'statusUpdate') return translateStatus(ev, ctx, state);
  return null;                                   // 未知成员名：stream 层已协议门禁，防御位
}