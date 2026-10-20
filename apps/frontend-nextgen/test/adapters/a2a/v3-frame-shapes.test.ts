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

// 钉形表已外提为公共数据文件（src/adapters/a2a/v3FrameShapes.ts）：契约套件
//（testing.ts）与 oracle 测试共用同一份事实源；translate.test 直接 import 数据
// 文件，不再经本测试文件转手。
import { V3_FRAME_SHAPES } from '@/adapters/a2a/v3FrameShapes';

// ─── 钉板自检辅助（宿主侧自检用；表本体已外提） ─────────────────────

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value));
}

function newParser(): SdkParser {
  return new OpenClawParser({ enableThinking: true, generateId: () => "test-msg-id" });
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
