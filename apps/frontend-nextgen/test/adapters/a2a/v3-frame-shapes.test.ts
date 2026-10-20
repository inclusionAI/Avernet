/**
 * B8 — v3 帧形状钉板（M2 translate.ts 的唯一契约事实源）
 * （nextgen 移植注：oracle 换 @tc-chat/adapters@2.1.0 真 dist——钉板单元
 *  与 aix 线（@alipay/aix-chat-adapters）逐条相同，下方 describe 全量跑过。）
 * ============================================================================
 *
 * 【头号交付物：OpenClawParser.handleMessage 输入契约】
 *   - 吃的是 **整条 ws 帧**，不是裸内层 payload。事件帧头固定
 *     `{ type: 'event', event: '<name>', payload: {...} }`，响应帧为
 *     `{ type: 'res', ok: boolean, payload?, error?: { message } }`
 *     （OpenClawTransport.d.ts: OpenClawEventFrame / OpenClawResponseFrame）。
 *   - 派生字段是帧顶层 `type` + 事件帧的 `event`（OpenClawParser.js:164-222
 *     handleMessage 只认 event ∈ {'agent','chat','exec.approval.requested'}，
 *     其余事件名一律 return false）；'res' 帧派生 ok / error.message。
 *     另外任何帧只要 `payload.state === 'error'` 就直接走 onError，与
 *     event 名无关（OpenClawParser.js:180-185）。
 *   - 会话门：payload.sessionKey 与 parser.setSessionKey(...) 同时存在时按
 *     小写"包含"互查（this.sessionKey.includes(payload.sessionKey)），不匹配
 *     则整帧丢弃返回 false（OpenClawParser.js:167-171）。
 *   - 帧顶层 seq/stateVersion 是传输层字段，parser 不读
 *     （engine 帧两处都带：AICodingParser.test.ts:318-331 的 toolFrame）。
 *
 * 【与 A2A wire 的对接面（translate.ts 的输入侧，供 B9+ 对照）】
 *   - agent/tool/thinking/lifecycle 类：A2A statusUpdate metadata.kind='agent'
 *     + metadata.engine_frame = 原始 v3 payload 全量（wire.py:160-176，
 *     kernel.publish_agent_progress 原样转发 frame.payload，engine_bridge.py:236-242）。
 *   - chat delta/final 正文：A2A artifactUpdate 的 parts[].text 流
 *     （kernel.publish_delta/publish_final → wire.py:67-80）——translate 需
 *     在这条路上合成 v3 chat 帧；engine 源帧本身增量在 deltaText|delta、
 *     message.content 是累计快照（engine_bridge.py:_payload_text:301-331）。
 *   - 失败终态：statusUpdate metadata 携 errorCode/errorMessage/engine_frame
 *     （wire.py:83-94 wire_terminal_marker）→ 需合成 state:'error' 帧或 res 错误帧。
 *   - HITL/审批：wire_interaction_requested 的 INPUT_REQUIRED + engine_frame 全量
 *     （wire.py:120-131）；v3 侧审批身份键是 approvalId|id|requestId 之一
 *     （gateway_client.py:962-977 setdefault 证据）。
 *
 * 每个条目的验证测试都在下方 describe 里对真 SDK 跑过——真 parser 是 oracle，
 * 形状分歧时以 OpenClawParser.js 为准（它就是 translate 的直接下游）。
 */
import * as fs from 'fs';

// ─── 真 SDK 加载（oracle；禁止手搓 wire 形状替代它） ───────────────────────

interface SdkParserConfig {
  enableThinking?: boolean;
  generateId?: () => string;
  xIAMToken?: string;
}

interface SdkParseResult {
  blocks: Record<string, any>[];
  content: string;
  done: boolean;
  toolCalls: Record<string, any>[];
  sessionKey?: string;
  isApproval: boolean;
  approvalRequest?: Record<string, any>;
  runId?: string;
}

interface SdkParser {
  onUpdate?: (result: SdkParseResult) => void;
  onComplete?: (result: SdkParseResult) => void;
  onError?: (error: Error) => void;
  handleMessage(message: Record<string, any>): boolean;
  getResult(): SdkParseResult;
  reset(): void;
  setSessionKey(sessionKey: string): void;
  buildRequest(params: Record<string, any>): Record<string, any>;
  updateApprovalStatus(id: string, status: 'approved' | 'rejected'): boolean;
  clearPendingApproval(): Record<string, any>[];
}

type SdkParserCtor = new (config?: SdkParserConfig) => SdkParser;

/** SDK 解析器候选位：包安装位直取（本 app 硬依赖 @tc-chat/adapters@2.1.0，
 * jest 经 babel 转译其后对 require 透明）。可用环境变量
 * A2A_OPENCLAW_PARSER_PATH 显式覆盖。 */
// eslint-disable-next-line jest/no-export -- 契约基建：translate.test 经同一库取 true parser 对拍
export function loadOpenClawParser(): SdkParserCtor {
  const relay = '@tc-chat/adapters/dist/openclaw/OpenClawParser.js';
  const candidates: Array<() => string | null> = [
    () => {
      try {
        return require.resolve(relay);
      } catch {
        return null;
      }
    },
    () => process.env.A2A_OPENCLAW_PARSER_PATH || null,
  ];
  for (const candidate of candidates) {
    const resolved = candidate();
    if (resolved && fs.existsSync(resolved)) {
      const mod = require(resolved) as { OpenClawParser?: SdkParserCtor };
      if (mod && mod.OpenClawParser) return mod.OpenClawParser;
    }
  }
  throw new Error(
    'OpenClawParser oracle not found: install @tc-chat/adapters or ' +
      'set A2A_OPENCLAW_PARSER_PATH to dist/openclaw/OpenClawParser.js',
  );
}

const OpenClawParser = loadOpenClawParser();

// ─── 钉板本体 ─────────────────────────────────────────────────────────────

interface PinnedFrame {
  /** 整帧最小合法样例（含帧头，非裸 payload——handleMessage 吃整帧） */
  frame: Record<string, any>;
  /** OpenClawParser 实际读取的必填字段 */
  required: readonly string[];
  /** 读了但可缺省的字段 */
  optional?: readonly string[];
  /** engine/aicoding 同帧携带、但 oracle 不读的字段 */
  ignored?: readonly string[];
  /** 钉住该形状的权威来源 */
  source: string;
  notes?: string;
}

/**
 * 每类 wire 事件一格：条目名即 translate.ts 的事件类。
 * 后续 translate.test.ts 直接 import 这份常量当 fixture。
 */
// eslint-disable-next-line jest/no-export -- 契约事实源：translate.test 复用本表作 fixture
export const V3_FRAME_SHAPES: Record<string, PinnedFrame> = {
  chatDelta: {
    frame: {
      type: 'event',
      event: 'chat',
      payload: {
        runId: 'run-1',
        sessionKey: 'agent:main:main',
        state: 'delta',
        // engine 增量字段（真实帧会带；oracle 不读，BaaS/_payload_text 读）
        delta: '你',
        message: {
          role: 'assistant',
          // 累计快照——oracle 唯一渲染通道，自己做前缀差分
          content: [{ type: 'text', text: '你好' }],
        },
      },
    },
    required: ["payload.state != 'final'", 'payload.message.content[]'],
    ignored: ['payload.delta / payload.deltaText', 'payload.runId', 'payload.ts/seq/background'],
    source: 'OpenClawParser.js@handleChatEvent:627-667 + engine_bridge.py:_payload_text:301-317',
    notes:
      'engine 真帧 delta 增量在 deltaText|delta，message.content 恒为累计全文' +
      '（orchestrator-bridge 证据，读 delta 帧的 content 会"你"→"你你好"双写）。' +
      'aicoding fixture AICodingParser.test.ts:177-180 的裸 delta 形态喂 oracle 是 no-op' +
      '——translate 从 artifactUpdate 合成 delta 时必须产出累计 content。',
  },
  chatFinal: {
    frame: {
      type: 'event',
      event: 'chat',
      payload: {
        runId: 'run-1',
        sessionKey: 'agent:main:main',
        state: 'final',
        message: {
          role: 'assistant',
          content: [{ type: 'text', text: '你好！' }],
        },
      },
    },
    required: ["payload.state === 'final'"],
    optional: ['payload.message.content[]（终态全文快照，权威）'],
    ignored: ['payload.runId', 'payload.origin', 'payload.background'],
    source:
      'OpenClawParser.js@handleChatEvent:649-699 + AICodingParser.test.ts:183-192 + engine_bridge.py:250-254',
    notes:
      '缺少 message 时（如 AICodingParser.test.ts:272-281 的裸 final）oracle 仍会' +
      '触发 onComplete——final 信号不依赖正文；若已收到 assistant 流' +
      '（hasAssistantStream），非 final chat 帧文本一律跳过，但 final 走' +
      'reconcileFinalText 以服务端终态快照做幂等对账（tc-chat 2.1.0 分歧点，' +
      'aix 线只保留完成信号）——对 A2A 是更强保证：final 帧携带 wire 权威全文。',
  },
  agentToolStart: {
    frame: {
      type: 'event',
      event: 'agent',
      payload: {
        runId: 'run-1',
        sessionKey: 'agent:main:main',
        stream: 'tool',
        data: {
          phase: 'start',
          toolCallId: 'call-1',
          name: 'Bash',
          args: { command: 'pwd' },
        },
      },
    },
    required: ["payload.stream === 'tool'", 'payload.data', "data.phase === 'start'", 'data.toolCallId'],
    optional: ['data.name | data.toolName（oracle: name 优先）', 'data.args'],
    ignored: ['payload.seq/ts（帧顶与 payload 双带）'],
    source: 'OpenClawParser.js@handleAgentMessage:373-401 + handleToolCall:410-444',
    notes:
      '⚠️ aicoding 方言用 data.type:"start"+toolName+input（AICodingParser.test.ts:378-384），' +
      'oracle 只认 data.phase；分歧按 oracle 收敛——engine 透传 agent 帧走 OpenClaw 方言。',
  },
  agentToolResult: {
    frame: {
      type: 'event',
      event: 'agent',
      payload: {
        runId: 'run-1',
        sessionKey: 'agent:main:main',
        stream: 'tool',
        data: {
          phase: 'result',
          toolCallId: 'call-1',
          name: 'Bash',
          result: '/workspace',
          isError: false,
        },
      },
    },
    required: ["data.phase === 'result'", 'data.toolCallId'],
    optional: ['data.result（非 string 会被 JSON.stringify）', 'data.isError（缺省=成功）', 'data.error', 'data.args（start 缺失时兜底）'],
    ignored: ['data.parentToolUseId', 'data.output（aicoding 方言字段）'],
    source: 'OpenClawParser.js@handleToolCall:445-470 + AICodingParser.test.ts:396-407（command_output 方言对照）',
    notes:
      'aicoding 方言的 result 是 command_output 流 + phase:"end" + output 字段；' +
      'oracle 要 phase:"result" + result 字段。文档里的"end"在 oracle 上不代表工具收尾。',
  },
  agentToolUpdate: {
    frame: {
      type: 'event',
      event: 'agent',
      payload: {
        runId: 'run-1',
        sessionKey: 'agent:main:main',
        stream: 'tool',
        data: {
          phase: 'update',
          toolCallId: 'call-2',
          name: 'Read',
          partialResult: { content: [{ type: 'text', text: 'line 1' }] },
        },
      },
    },
    required: ["data.phase === 'update'", 'data.toolCallId'],
    optional: ['data.partialResult.content[]（BCS 增量输出）', 'data.name | data.toolName', 'data.args（无 start 首帧时建 running step）'],
    source: 'OpenClawParser.js@handleToolCall:471-530',
    notes: 'BCS 群聊协议的中间增量；首帧 update 无 start 时会自建 running step。',
  },
  agentThinking: {
    frame: {
      type: 'event',
      event: 'agent',
      payload: {
        runId: 'run-1',
        sessionKey: 'agent:main:main',
        stream: 'thinking',
        data: { text: '(正在思考)', delta: '(正在思考)' },
      },
    },
    required: ["payload.stream === 'thinking'", 'payload.data', 'data.text | data.delta'],
    optional: ['data.text（首帧/覆盖式全量）', 'data.delta（增量优先）'],
    ignored: ['data.parentToolUseId', 'payload.seq/ts'],
    source: 'OpenClawParser.js@handleThinking:537-559 + AICodingParser.test.ts:346-355, 529-535',
    notes:
      '首帧建块读 text；后续增量 delta 优先、否则 text 全量覆盖。' +
      '需要 config.enableThinking（缺省 true）。' +
      'aicoding 另有 content_block 包裹语义（AICodingParser.test.ts:335-343），oracle 不认——见 agentNoOpStream。',
  },
  agentAssistantStream: {
    frame: {
      type: 'event',
      event: 'agent',
      payload: {
        runId: 'run-1',
        sessionKey: 'agent:main:main',
        stream: 'assistant',
        data: { delta: '你', text: '你好' },
      },
    },
    required: ["payload.stream === 'assistant'", 'payload.data', 'data.delta（驱动文本增量）'],
    optional: ['data.text（textContent 追踪用：给了用全量，缺了自行累加）'],
    source: 'OpenClawParser.js@handleAssistantStream:583-614 + intent_eval_observer.py:192-206（engine 侧 stream=assistant 全量语义）',
    notes:
      '收到首个 assistant 帧后置 hasAssistantStream，后续非 final chat 帧文本全部跳过' +
      '（避免双写）；final 以服务端快照 reconcileFinalText 幂等对账（tc-chat 2.1.0）。' +
      'token 级流式走这条路，chat delta 粒度更粗。',
  },
  agentLifecycleEnd: {
    frame: {
      type: 'event',
      event: 'agent',
      payload: {
        runId: 'run-1',
        sessionKey: 'agent:main:main',
        stream: 'lifecycle',
        data: { phase: 'end' },
      },
    },
    required: ['payload.data', "data.phase === 'end'"],
    ignored: ["payload.stream（'end' 判定先于 stream 路由，值任意/可缺）"],
    source: 'OpenClawParser.js@handleAgentMessage:379-384 + intent_eval_observer.py:112-116（engine lifecycle end）',
    notes: 'agent 结束信号：关闭 thinking 块的 loading；不产出任何块。',
  },
  approvalRequested: {
    frame: {
      type: 'event',
      event: 'exec.approval.requested',
      payload: {
        id: 'apr-1',
        request: {
          command: 'npm install',
          resolvedPath: '/usr/bin/npm',
          cwd: '/workspace',
          sessionKey: 'agent:main:main',
        },
        createdAtMs: 1760000000000,
        expiresAtMs: 1760003600000,
      },
    },
    required: ['payload.id', 'payload.request（对象，缺失会直接 throw）'],
    optional: ['request.command（标题）', 'request.resolvedPath / cwd / sessionKey', 'payload.createdAtMs / expiresAtMs（details 行）'],
    source:
      'OpenClawParser.js@handleApprovalRequest:804-827 + gateway_client.py:962-977（engine approval.requested/exec.approval.requested 双事件名同一处理）+ wire.py:120-131（A2A INPUT_REQUIRED + engine_frame）',
    notes:
      'pending 审批建立后，后续 chat/agent 帧被 shouldBlockMessage 拦截入 blockedMessages' +
      '（OpenClawParser.js:833-844：exec.approval.requested 自身不拦）。',
  },
  approvalResolved: {
    frame: {
      type: 'event',
      event: 'chat',
      payload: {
        runId: 'run-1',
        sessionKey: 'agent:main:main',
        state: 'approval_resolved',
        approvalId: 'apr-1',
      },
    },
    required: ["payload.state === 'approval_resolved'（engine v3 语义）"],
    ignored: ['整帧——oracle 无 exec.approval.resolved/approval_resolved 分支'],
    source:
      'gateway_client.py:962-996（engine 把 approval.requested/resolved 重包成 chat 事件）+ OpenClawParser.js:206-221（handleMessage 仅有三个事件名）+ wire.py:134-158（A2A kind:"interaction.resolved"）',
    notes:
      '⚠️ 释放审批不能靠喂帧：真 parser 没有任何 resolved 帧处理器（甚至会被' +
      '审批阻塞逻辑拦下）。translate 必须走两步 API：updateApprovalStatus(id,status)' +
      '翻卡片状态，clearPendingApproval() 解除阻塞并回放被拦帧序列' +
      '——与 R8"释放锚定 run 终帧"的评审结论同向。',
  },
  injectChat: {
    frame: {
      type: 'event',
      event: 'chat',
      payload: {
        runId: 'inject-cb-1',
        sessionKey: 'agent:main:main',
        state: 'delta',
        message: {
          role: 'user',
          content: [{ type: 'text', text: '后台任务已完成' }],
        },
      },
    },
    required: ["payload.runId 且以 'inject-' 开头", "payload.state ∈ {'delta','final'}（final 标记该 inject 完成）", 'payload.message.content[]'],
    source:
      'OpenClawParser.js@handleChatEvent:632-641 + handleInjectMessage:708-767 + engine_bridge.py:197-206（inject-* 帧=会话级 fanout，不入本 run 流）',
    notes:
      'inject 消息走独立 blocks/runId 作用域：onUpdate 结果带 runId，inject final 不触发' +
      '主 onComplete；主消息与 inject 消息互不污染。',
  },
  chatError: {
    frame: {
      type: 'event',
      event: 'chat',
      payload: {
        runId: 'run-1',
        sessionKey: 'agent:main:main',
        state: 'error',
        errorMessage: 'model provider timeout',
        errorCode: 'E_PROVIDER_TIMEOUT',
      },
    },
    required: ["payload.state === 'error'", 'payload.errorMessage（缺省回退 "Unknown error"）'],
    optional: ['payload.errorCode', 'payload.engine_frame'],
    source: 'OpenClawParser.js:180-185 + engine_bridge.py:255-268（_EngineRunFailed detail）+ wire.py:83-94（wire_terminal_marker 携 errorCode/errorMessage/engine_frame）',
    notes: '与 event 名无关：任何帧 payload.state==="error" 都进 onError；translate 收到 FAILED 终帧时用它合成。',
  },
  chatAborted: {
    frame: {
      type: 'event',
      event: 'chat',
      payload: {
        runId: 'run-1',
        sessionKey: 'agent:main:main',
        state: 'aborted',
      },
    },
    required: ["payload.state === 'aborted'（engine 取消终态）"],
    ignored: ['整帧——oracle 无取消处理（无 throw、无 onComplete）'],
    source: 'engine_bridge.py:76-78 + 269-270（_EngineRunAborted）+ OpenClawParser.js@handleChatEvent:637（非 final 无 msg 即早退）',
    notes: 'agent 端取消清理（R7 取消归属锚：run 执行期）必须由 translate/传输层接管，parser 面对 abort 帧是纯 no-op。',
  },
  resError: {
    frame: {
      type: 'res',
      id: 'req-1',
      ok: false,
      error: { code: 'INVALID_PARAMS', message: 'session not found' },
    },
    required: ["type === 'res'", 'ok 为假值', 'error.message（缺省回退 "Unknown error"）'],
    source: 'OpenClawParser.js:174-179 + OpenClawTransport.d.ts:9-21（OpenClawResponseFrame）',
    notes: 'ws 请求级失败帧；A2A 的 JSON-RPC 错误信封（stream.ts classifyFailure 已有分类）由 translate 折算成这个形态喂 parser。',
  },
  resInjectOk: {
    frame: {
      type: 'res',
      id: 'req-1',
      ok: true,
      payload: { ok: true },
    },
    required: ["type === 'res'", 'ok === true', 'payload.ok === true', "parser.currentMethod === 'chat.inject'（buildRequest isInject 置位）"],
    source: 'OpenClawParser.js:189-198 + buildInjectRequest:272-289',
    notes: '仅 inject 请求的成功响应会触发 onComplete(done:true)；普通 chat.send 的 res.ok 被 parser 忽略。',
  },
  agentNoOpStream: {
    frame: {
      type: 'event',
      event: 'agent',
      payload: {
        runId: 'run-1',
        sessionKey: 'agent:main:main',
        stream: 'content_block',
        data: { phase: 'start', blockType: 'thinking' },
      },
    },
    required: ["payload.data（缺则整帧 return false）"],
    ignored: ['stream ∈ {content_block, command_output, task, mode_transition…} 中 oracle 不认识的流'],
    source:
      'OpenClawParser.js@handleAgentMessage:388-397（只路由 tool/thinking/assistant）+ AICodingParser.test.ts:335-343（content_block）、:396-407（command_output）、:642-658（task）',
    notes:
      'aicoding 生态里这些流驱动 AICodingParser 的 subagent 聚合；对 OpenClaw oracle 是' +
      '"承认帧、不渲染"的 no-op（返回 true、emitUpdate、不产块）。translate 经过它们时' +
      '要么丢弃要么直通——不能指望 SDK 渲染。',
  },
};

// ─── 钉板自检：真 parser 是 oracle，最小样例不过就改形状，不许改测试 ────────

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value));
}

function newParser(): SdkParser {
  return new OpenClawParser({ enableThinking: true, generateId: () => 'test-msg-id' });
}

describe('v3 frame shapes pinned against the real OpenClawParser', () => {
  it('V3_FRAME_SHAPES 每个条目都是带完整帧头的整帧（handleMessage 吃整帧不吃裸 payload）', () => {
    for (const [name, pinned] of Object.entries(V3_FRAME_SHAPES)) {
      expect(pinned.frame.type).toEqual(expect.stringMatching(/^(event|res)$/));
      if (pinned.frame.type === 'event') {
        // eslint-disable-next-line jest/no-conditional-expect -- 表格循环校验：event 帧必须带非空 event 名
        expect(typeof pinned.frame.event).toBe('string');
        // eslint-disable-next-line jest/no-conditional-expect -- 同上（第二断言）
        expect(pinned.frame.event.length).toBeGreaterThan(0);
      }
      expect(typeof pinned.source).toBe('string');
      expect(pinned.required.length).toBeGreaterThan(0);
      expect(name.length).toBeGreaterThan(0);
    }
  });

  it('chat.delta 内容只由 message.content 累计快照承载，oracle 自做前缀差分不双写', () => {
    const parser = newParser();
    expect(parser.handleMessage(clone(V3_FRAME_SHAPES.chatDelta.frame))).toBe(true);

    const second = clone(V3_FRAME_SHAPES.chatDelta.frame);
    // 增量字段在 engine 侧前进，oracle 不读
    second.payload.delta = '好';
    second.payload.message.content[0].text = '你好，世界';
    expect(parser.handleMessage(second)).toBe(true);

    expect(parser.getResult().blocks).toEqual([
      expect.objectContaining({ type: 'text', content: '你好，世界' }),
    ]);
  });

  it('chat.final 触发 onUpdate→onComplete(done) 且全文不翻倍', () => {
    const parser = newParser();
    const onUpdate = jest.fn();
    const onComplete = jest.fn();
    parser.onUpdate = onUpdate;
    parser.onComplete = onComplete;

    parser.handleMessage(clone(V3_FRAME_SHAPES.chatDelta.frame));
    expect(parser.handleMessage(clone(V3_FRAME_SHAPES.chatFinal.frame))).toBe(true);

    expect(onComplete).toHaveBeenCalledTimes(1);
    const finalResult = onComplete.mock.calls[0][0];
    expect(finalResult.done).toBe(true);
    expect(finalResult.blocks).toEqual([
      expect.objectContaining({ type: 'text', content: '你好！' }),
    ]);
    expect(onUpdate.mock.calls.at(-1)![0].content).toBe('你好！');
  });

  it('agent.assistant 流式 token 之后，chat delta 防双写；final 是权威快照对账（tc-chat 2.1.0 与 aix 线的分歧点）', () => {
    const parser = newParser();
    const onComplete = jest.fn();
    parser.onComplete = onComplete;
    const stream0 = clone(V3_FRAME_SHAPES.agentAssistantStream.frame);
    const stream1 = clone(V3_FRAME_SHAPES.agentAssistantStream.frame);
    stream1.payload.data.delta = '好';
    stream1.payload.data.text = '你好';

    expect(parser.handleMessage(stream0)).toBe(true);
    expect(parser.handleMessage(stream1)).toBe(true);

    // 同全文的 chat delta（translate 从累计快照合成时常见）必须不再追加
    const redundantChat = clone(V3_FRAME_SHAPES.chatDelta.frame);
    redundantChat.payload.message.content[0].text = '你好';
    expect(parser.handleMessage(redundantChat)).toBe(true);
    // hasAssistantStream 防双写仍完整：delta 期文本不重复（'你好' 不翻倍）
    expect(parser.getResult().blocks).toEqual([
      expect.objectContaining({ type: 'text', content: '你好' }),
    ]);

    // ⚠️ 分歧点（tc-chat 2.1.0 dist 注释原文："assistant stream 可能只覆盖工具调用前
    // 的文本，chat.final 才携带最终正文…final 则以服务端快照对当前尾部文本做幂等
    // 对账"）：final 不再是纯完成信号，而是 reconcileFinalText(finalText)——
    // 引擎权威终态快照覆盖开放文本段。aix 线此处忽略 final 正文只发完成信号；
    // 对 A2A translate 是更强保证（final 帧携带 wire artifactUpdate 的权威全文，
    // parser 侧幂等对账，流式增量漂移不会留在终稿里）。
    expect(parser.handleMessage(clone(V3_FRAME_SHAPES.chatFinal.frame))).toBe(true);
    expect(onComplete.mock.calls[0][0].done).toBe(true);
    expect(parser.getResult().blocks).toEqual([
      expect.objectContaining({ type: 'text', content: '你好！' }),
    ]);
  });

  it('agent.tool start+result 合并为单 tool_execution 块', () => {
    const parser = newParser();
    parser.handleMessage(clone(V3_FRAME_SHAPES.agentToolStart.frame));

    expect(parser.getResult().blocks).toEqual([
      {
        type: 'tool_execution',
        steps: [expect.objectContaining({ id: 'call-1', tool: 'Bash', status: 'running' })],
      },
    ]);

    expect(parser.handleMessage(clone(V3_FRAME_SHAPES.agentToolResult.frame))).toBe(true);
    expect(parser.getResult().blocks[0].steps[0]).toMatchObject({
      id: 'call-1',
      status: 'success',
      output: '/workspace',
    });
  });

  it('agent.tool.update 无 start 首帧也能建 running step，partialResult 提供增量 output', () => {
    const parser = newParser();
    expect(parser.handleMessage(clone(V3_FRAME_SHAPES.agentToolUpdate.frame))).toBe(true);
    expect(parser.getResult().blocks).toEqual([
      {
        type: 'tool_execution',
        steps: [
          expect.objectContaining({ id: 'call-2', status: 'running', output: 'line 1' }),
        ],
      },
    ]);
  });

  it('agent.thinking：首帧建块、delta 追加、lifecycle end 关闭 loading', () => {
    const parser = newParser();
    parser.handleMessage(clone(V3_FRAME_SHAPES.agentThinking.frame));
    const followUp = clone(V3_FRAME_SHAPES.agentThinking.frame);
    followUp.payload.data.text = '';
    followUp.payload.data.delta = ' 更深';
    parser.handleMessage(followUp);

    expect(parser.getResult().blocks[0]).toMatchObject({
      type: 'thinking',
      content: '(正在思考) 更深',
      isLoading: true,
    });

    expect(parser.handleMessage(clone(V3_FRAME_SHAPES.agentLifecycleEnd.frame))).toBe(true);
    expect(parser.getResult().blocks[0]).toMatchObject({ type: 'thinking', isLoading: false });
  });

  it('exec.approval.requested 建 approval 块并阻塞后续 chat 帧', () => {
    const parser = newParser();
    expect(parser.handleMessage(clone(V3_FRAME_SHAPES.approvalRequested.frame))).toBe(true);

    const before = parser.getResult().blocks.length;
    expect(parser.getResult()).toMatchObject({ isApproval: true });
    expect(parser.getResult().blocks).toEqual([
      expect.objectContaining({
        type: 'approval',
        approvalId: 'apr-1',
        status: 'pending',
      }),
    ]);

    // 审批期间 chat 帧被拦（返回 false 且不入块）
    expect(parser.handleMessage(clone(V3_FRAME_SHAPES.chatDelta.frame))).toBe(false);
    expect(parser.getResult().blocks.length).toBe(before);

    // 释放路径是 parser API，不是帧
    expect(parser.updateApprovalStatus('apr-1', 'approved')).toBe(true);
  });

  it('payload.request 是 REQUIRED：缺失时真 parser 直接 throw（translate 必须总要合成 request 对象）', () => {
    const parser = newParser();
    const bad = clone(V3_FRAME_SHAPES.approvalRequested.frame);
    delete bad.payload.request;
    expect(() => parser.handleMessage(bad)).toThrow();
  });

  it('inject runId 走独立 ParseResult：onUpdate 带 runId，不触发主 onComplete', () => {
    const parser = newParser();
    const onUpdate = jest.fn();
    const onComplete = jest.fn();
    parser.onUpdate = onUpdate;
    parser.onComplete = onComplete;

    parser.handleMessage(clone(V3_FRAME_SHAPES.injectChat.frame));
    expect(onUpdate).toHaveBeenCalled();
    expect(onUpdate.mock.calls.at(-1)![0]).toMatchObject({
      runId: 'inject-cb-1',
      content: '后台任务已完成',
      done: false,
    });
    // inject 不写主消息块
    expect(parser.getResult().blocks).toEqual([]);

    const injectFinal = clone(V3_FRAME_SHAPES.injectChat.frame);
    injectFinal.payload.state = 'final';
    expect(parser.handleMessage(injectFinal)).toBe(true);
    expect(onUpdate.mock.calls.at(-1)![0]).toMatchObject({ runId: 'inject-cb-1', done: true });
    expect(onComplete).not.toHaveBeenCalled();
  });

  it('payload.state==="error" 帧走 onError 并返回 false（与 event 名无关）', () => {
    const parser = newParser();
    const onError = jest.fn();
    const onComplete = jest.fn();
    parser.onError = onError;
    parser.onComplete = onComplete;

    expect(parser.handleMessage(clone(V3_FRAME_SHAPES.chatError.frame))).toBe(false);
    expect(onError).toHaveBeenCalledTimes(1);
    expect(onError.mock.calls[0][0].message).toBe('model provider timeout');
    expect(onComplete).not.toHaveBeenCalled();
  });

  it('res 错误帧走 onError（error.message 缺省回退 Unknown error）', () => {
    const parser = newParser();
    const onError = jest.fn();
    parser.onError = onError;

    expect(parser.handleMessage(clone(V3_FRAME_SHAPES.resError.frame))).toBe(false);
    expect(onError.mock.calls[0][0].message).toBe('session not found');

    const bare = clone(V3_FRAME_SHAPES.resError.frame);
    delete bare.error;
    expect(parser.handleMessage(bare)).toBe(false);
    expect(onError.mock.calls[1][0].message).toBe('Unknown error');
  });

  it('chat.inject 请求的 res.ok 成功帧触发 onComplete(done:true)', () => {
    const parser = newParser();
    const onComplete = jest.fn();
    parser.onComplete = onComplete;

    const request = parser.buildRequest({ query: 'go', sessionKey: 'agent:main:main', isInject: true });
    expect(request).toMatchObject({ type: 'req', method: 'chat.inject' });

    expect(parser.handleMessage(clone(V3_FRAME_SHAPES.resInjectOk.frame))).toBe(true);
    expect(onComplete).toHaveBeenCalledTimes(1);
    expect(onComplete.mock.calls[0][0].done).toBe(true);
  });

  it('state="aborted" 对 oracle 是无副作用 no-op：不 throw、不 onComplete（取消清理归 translate）', () => {
    const parser = newParser();
    const onError = jest.fn();
    const onComplete = jest.fn();
    parser.onError = onError;
    parser.onComplete = onComplete;

    expect(parser.handleMessage(clone(V3_FRAME_SHAPES.chatAborted.frame))).toBe(true);
    expect(onError).not.toHaveBeenCalled();
    expect(onComplete).not.toHaveBeenCalled();
    expect(parser.getResult().blocks).toEqual([]);
  });

  it('v3 approval_resolved 帧不能释放 pending approval——会被审批阻塞拦下（释放必须走 updateApprovalStatus）', () => {
    const parser = newParser();
    parser.handleMessage(clone(V3_FRAME_SHAPES.approvalRequested.frame));

    expect(parser.handleMessage(clone(V3_FRAME_SHAPES.approvalResolved.frame))).toBe(false);
    expect(parser.getResult().isApproval).toBe(true);

    // 释放是两步 API：updateApprovalStatus 只翻卡片状态；解除阻塞+回放
    // blockedMessages 要靠 clearPendingApproval（帧永远做不到）
    expect(parser.updateApprovalStatus('apr-1', 'approved')).toBe(true);
    expect(parser.getResult().isApproval).toBe(true);
    expect(parser.getResult().blocks).toEqual([
      expect.objectContaining({ type: 'approval', status: 'approved' }),
    ]);

    const drained = parser.clearPendingApproval();
    expect(drained).toHaveLength(1); // 被拦下的 approval_resolved 帧
    expect(drained[0]).toMatchObject({ 'type': 'event', 'event': 'chat' });
    expect(parser.getResult().isApproval).toBe(false);
  });

  it('aicoding-only 流（content_block 等）对 oracle 是"承认但无渲染"的 no-op', () => {
    const parser = newParser();
    expect(parser.handleMessage(clone(V3_FRAME_SHAPES.agentNoOpStream.frame))).toBe(true);
    expect(parser.getResult().blocks).toEqual([]);
  });

  it('sessionKey 门是小写包含式互查：不匹配整帧丢弃', () => {
    const ok = newParser();
    ok.setSessionKey('app-bot:bot-1:conv-1');
    const frame = clone(V3_FRAME_SHAPES.chatDelta.frame);
    frame.payload.sessionKey = 'bot-1';
    expect(ok.handleMessage(frame)).toBe(true);

    const blocked = newParser();
    blocked.setSessionKey('app-bot:bot-1:conv-1');
    const other = clone(V3_FRAME_SHAPES.chatDelta.frame);
    other.payload.sessionKey = 'app-bot:bot-2:other';
    expect(blocked.handleMessage(other)).toBe(false);
    expect(blocked.getResult().blocks).toEqual([]);
  });
});
