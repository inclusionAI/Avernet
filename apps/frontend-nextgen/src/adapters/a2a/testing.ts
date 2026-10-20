/**
 * B16 — 双 SDK 行为契约套件（公共/宿主解耦的验收门槛）。
 * ============================================================================
 * `createParserContractSuite(parserFactory)`：同一套断言对两种宿主 SDK 各跑
 * 一遍——nextgen 侧 contract.tc-chat.test.ts 对开源聊天 SDK 跑绿；OCB 消费公共
 * 核心时对内部聊天 SDK 跑同一套件（README 消费契约）。**不凭 TS 类型兼容即
 * 认定行为一致**——这是行为层的对拍门槛。
 *
 * 断言范围按 A2AProvider 的 parser 消费面收口（wireParser/reset/setSessionKey/
 * handleMessage/getPendingApproval/updateApprovalStatus/clearPendingApproval/
 * getResult + 三钩子），fixture = v3FrameShapes.ts 的钉形表（真 parser oracle
 * 已在既有钉板测试验证）。**消费面之外**（hydrate/markAborted/buildInjectRequest
 * 等）不在契约内：开源 SDK 有其一、内部 SDK 无——公共核心恰好不消费，契约
 * 按"消费面存在性"断言（末组），不做缺席断言（缺席不构成失败）。
 *
 * 公共化边界：本文件属公共核心，直接调全局 describe/it/expect（jest 类 runner
 * 通用），零宿主字样；供任意宿主测试文件 import 消费。
 */
import type { SdkParser, SdkParserConfig, SdkParserFactory } from './sdkParser';
import { V3_FRAME_SHAPES } from './v3FrameShapes';

const SESSION = 'agent:main:main';

export interface ParserContractSuiteOptions {
  /** describe 标题（宿主可注明 SDK 身份，如 'parser contract (tc-chat)'） */
  suiteName?: string;
}

/**
 * 注册契约套件到当前 jest 环境（调用位置：宿主测试文件顶层）。
 * 帧样例来自钉形表（chatError 组走钩子断言，chatAborted/res* 是 no-op 帧
 * 供三章 verifier——本套件只收消费面最小集，不复制钉板的逐帧断言）。
 */
export function createParserContractSuite(
  parserFactory: SdkParserFactory,
  opts: ParserContractSuiteOptions = {},
): void {
  const describeName = opts.suiteName ?? 'a2a parser behavior contract'

  describe(describeName, () => {
    /** 契约套件内构造：与 A2AProvider 构造同参形状 + 会话键对齐钉形表 */
    async function makeParser(): Promise<SdkParser> {
      const parser = await Promise.resolve(parserFactory({
        enableThinking: true,
        generateId: () => 'test-msg-id',
      }));
      parser.setSessionKey(SESSION);
      return parser;
    }

    function clone<T>(value: T): T {
      return JSON.parse(JSON.stringify(value));
    }

    // ── 1. 构造与钩子桥（wireParser :1098-1107 的前提）─────────────────
    it('构造产出实例，onUpdate/onComplete/onError 三钩子可赋值并被调用', async () => {
      const parser = await makeParser();
      const seen: string[] = [];
      parser.onUpdate = () => { seen.push('update'); };
      parser.onComplete = () => { seen.push('complete'); };
      parser.onError = () => { seen.push('error'); };
      expect(typeof parser.handleMessage).toBe('function');
      expect(parser.handleMessage(clone(V3_FRAME_SHAPES.chatDelta.frame))).toBe(true);
      expect(seen).toContain('update');
      expect(parser.handleMessage(clone(V3_FRAME_SHAPES.chatFinal.frame))).toBe(true);
      expect(seen).toContain('complete');
    });

    // ── 2. handleMessage 帧形：吃整帧不吃裸 payload（B8 钉板前提）────────
    it('整帧被消费（true）；裸 payload（缺帧头）被拒（false）', async () => {
      const parser = await makeParser();
      expect(parser.handleMessage(clone(V3_FRAME_SHAPES.chatDelta.frame))).toBe(true);
      expect(parser.handleMessage(
        clone(V3_FRAME_SHAPES.chatDelta.frame.payload as Record<string, unknown>),
      )).toBe(false);
    });

    // ── 3. 增量/终稿累积语义：content 是累计快照，final 后全文不翻倍 ──────
    it('chatDelta 进累计快照，chatFinal 触发 onComplete(done) 且全文不双写', async () => {
      const parser = await makeParser();
      const results: Array<{ content: string; done: boolean }> = [];
      parser.onComplete = (r) => { results.push({ content: r.content, done: r.done }); };
      expect(parser.handleMessage(clone(V3_FRAME_SHAPES.chatDelta.frame))).toBe(true);
      expect(parser.getResult().content.length).toBeGreaterThan(0);
      expect(parser.handleMessage(clone(V3_FRAME_SHAPES.chatFinal.frame))).toBe(true);
      expect(results).toHaveLength(1);
      expect(results[0].done).toBe(true);
      // 全文恰等于终稿快照（前缀差分不产生双写）
      expect(parser.getResult().content).toBe(results[0].content);
    });

    // ── 4. 消息身份：getMessageId 生效、getResult 形状齐备 ────────────────
    it('消息身份与结果形状：getMessageId() 为 string；getResult() 含消费面字段', async () => {
      const parser = await makeParser();
      expect(typeof parser.getMessageId()).toBe('string');
      const result = parser.getResult();
      expect(typeof result.content).toBe('string');
      expect(Array.isArray(result.blocks)).toBe(true);
      expect(typeof result.done).toBe('boolean');
      expect(typeof result.isApproval).toBe('boolean');
    });

    // ── 5. setSessionKey 等值门（B9 钉板 #2）：同 k 过、异 k 拒 ───────────
    it('会话键等值门：同 sessionKey 帧消费，异 sessionKey 帧拒绝', async () => {
      const parser = await makeParser();
      expect(parser.handleMessage(clone(V3_FRAME_SHAPES.chatDelta.frame))).toBe(true);
      parser.setSessionKey('agent:other:other');
      // 钉形表帧全部钉在 SESSION 上——换键后同帧必被 includes 门拒（返回 false）
      expect(parser.handleMessage(clone(V3_FRAME_SHAPES.chatDelta.frame))).toBe(false);
      parser.setSessionKey(SESSION);
      parser.reset();
      expect(parser.handleMessage(clone(V3_FRAME_SHAPES.chatDelta.frame))).toBe(true);
    });

    // ── 6. 审批两步释放（B8 钉板）：pending 建立 → status 决议 → 清空 ─────
    it('审批面：approvalRequested 建挂起并阻塞入流；两步 API 释放并回放阻塞帧', async () => {
      const parser = await makeParser();
      const pendingId = String(V3_FRAME_SHAPES.approvalRequested.frame.payload.approvalId
        ?? V3_FRAME_SHAPES.approvalRequested.frame.payload.id
        ?? 'apr-1');
      expect(parser.handleMessage(clone(V3_FRAME_SHAPES.approvalRequested.frame))).toBe(true);
      const pending = parser.getPendingApproval();
      expect(pending).not.toBeNull();
      expect(pending!.approvalId).toBe(pendingId);
      // 审批期间 chat 帧被阻塞（shouldBlockMessage）
      expect(parser.handleMessage(clone(V3_FRAME_SHAPES.chatDelta.frame))).toBe(false);
      // 两步释放：updateApprovalStatus → clearPendingApproval（B8 钉板）
      expect(parser.updateApprovalStatus(pendingId, 'approved')).toBe(true);
      const blocked = parser.clearPendingApproval();
      expect(blocked.length).toBeGreaterThan(0);
      expect(parser.getPendingApproval()).toBeNull();
    });

    // ── 7. onError 面：chatError 帧触发错误钩子 ───────────────────────────
    it('chatError 帧进 onError 钩子（req 互斥解锁语义的消费前提）', async () => {
      const parser = await makeParser();
      const errors: Error[] = [];
      parser.onError = (e) => { errors.push(e); };
      parser.handleMessage(clone(V3_FRAME_SHAPES.chatError.frame));
      expect(errors.length).toBeGreaterThan(0);
      expect(errors[0]).toBeInstanceOf(Error);
    });

    // ── 8. 消费面收口（正向存在性）：12 成员按 SdkParser 契约齐备 ─────────
    it('消费面方法集齐备（hydrate/markAborted 等消费面外成员不做缺席断言）', async () => {
      const parser = await makeParser();
      const methodFace: Array<[keyof SdkParser, string]> = [
        ['getMessageId', 'function'],
        ['getResult', 'function'],
        ['reset', 'function'],
        ['handleMessage', 'function'],
        ['buildRequest', 'function'],
        ['updateApprovalStatus', 'function'],
        ['clearPendingApproval', 'function'],
        ['getPendingApproval', 'function'],
        ['setSessionKey', 'function'],
      ];
      for (const [member, kind] of methodFace) {
        expect(typeof (parser as unknown as Record<string, unknown>)[member as string])
          .toBe(kind);
      }
      // reset() 可循环调用（A2AProvider 每轮 request 复位）
      parser.reset();
      parser.reset();
      expect(parser.getResult().content).toBe('');
    });
  });
}

/** 契约套件自定义时可直接引用的构造参形状（透传给 parserFactory） */
export type { SdkParserConfig };