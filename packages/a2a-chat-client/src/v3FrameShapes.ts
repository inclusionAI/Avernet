/**
 * B9/B8 — v3 帧"钉形"表：每类 wire 事件一格的真 parser 最小合法帧样例。
 * ============================================================================
 * 从 test/adapters/a2a/v3-frame-shapes.test.ts 外提为公共数据（纯数据、
 * 零 jest/SDK 依赖）：契约套件（testing.ts 的 createParserContractSuite）与
 * 既有 oracle 测试共用同一份事实源。表内容一字不改——形状漂移由 fail-fast
 * 钉板测试抓（"不许改测试"原则：真 parser 是 oracle）。
 * 公共化边界：本文件属公共核心（hostIsolation 钉板登记），不含宿主/SDK 字样。
 */

export interface PinnedFrame {
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
