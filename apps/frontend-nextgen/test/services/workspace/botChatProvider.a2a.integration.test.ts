/**
 * B16 — BotChatProvider 的 A2A 接缝活体集成（node env 真链路冒烟）。
 * ============================================================================
 *
 * 静态字面钉板在 .a2a.seam.test.ts；本文件驱动**真实接线行为**——不打桩任何
 * a2a 模块,只桩两个后端控制器(getBotConnection / getBotIamToken)与全局
 * fetch(引擎 card / SSE / 控制面单信封三面路由),验证整条缝:
 *
 *   switch 开(localStorage) → maybeBuildA2AProvider 探测收敛 appChoiceCache
 *   → BotChatProvider.initialize 缺省工厂 takeCachedA2AProvider 命中 → 真
 *   A2AProvider(connect card 探测 / request SSE 消费 / translate / 真 tc-chat
 *   parser)→ IAM 经请求前刷新直写 config.xIAMToken 的既有通道进 A2A RPC 头
 *   → onComplete/onCancelOutcome 经 BotChatProvider 转发,§3.1 禁令在
 *   isSendBanned() 谓词上可见(cancel confirmed 才解禁)。
 *
 * node 环境跑真 fetch/Response/ReadableStream 链;switch.ts 只读
 * window.localStorage——shim 最小 window 对象即可,无需 jsdom。
 * 决策表/恢复/解析断言在 test/adapters/a2a/ 全覆盖,此处只钉缝。
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { maybeBuildA2AProvider, takeCachedA2AProvider } from '@/adapters/a2a/bootstrap';
import { A2A_FILE_REFS_UNSUPPORTED_MESSAGE } from '@avernet/a2a-chat-client';
import { A2A_SWITCH_KEY } from '@/adapters/a2a/switch';
import * as ctrl from '@/services/backendApi/bots/privateBotSessionController';
import { getBotIamToken } from '@/services/backendApi/privateChat/iamTokenController';
import { afterAll, beforeEach, describe, expect, it, jest } from '@jest/globals';

jest.mock('@/services/backendApi/bots/privateBotSessionController');
jest.mock('@/services/backendApi/privateChat/iamTokenController');
// 注意:不 mock '@/adapters/a2a/*' 与 '@tc-chat/adapters'——本文件的价值就在真链路。

import { createBotChatProvider, type BotChatProvider } from '@/services/workspace/botChatProvider';
import type { BotChatRequest } from '@/services/workspace/botChatProvider';
import type { ChatBotView } from '@/services/workspace/botSessionService';

// ─── switch 所需的最小 window shim(node env 无 jsdom;switch.ts 只读 localStorage) ───

// 原值留存:node-env 套件写在 globalThis 的桩会活过文件边界,泄漏进同 worker
// 的后续套件(曾见 jsdom 页面套件撞上本文件的假 fetch 而串台)——afterAll 恢复
const RAW_WINDOW = (globalThis as any).window;
const RAW_FETCH = (globalThis as any).fetch;

const storage = new Map<string, string>();
(globalThis as any).window = {
  localStorage: {
    getItem: (k: string) => storage.get(k) ?? null,
    setItem: (k: string, v: string) => storage.set(k, v),
  },
};

// ─── 引擎面三路由 fetch 桩(card / SSE / 控制面单信封) ───────────────────────

const WS_URL = 'ws://127.0.0.1:20003/api/openclaw/ws';
const CARD_URL = 'http://127.0.0.1:20003/.well-known/agent-card.json';
const RPC_URL = 'http://127.0.0.1:20003/rpc';
const TASK_ID = 'a7821a80a3e7';

/** fixture:真引擎 wire 快照(task → working → 你/好/你好！ → COMPLETED 共 seq 终态对) */
const snapshotEnvelopes: any[] = (JSON.parse(readFileSync(
  join(__dirname, '..', '..', 'adapters', 'a2a', 'fixtures', 'engine-wire-snapshot.json'),
  'utf8',
)) as { envelopes: any[] }).envelopes;

const enc = new TextEncoder();
const jrpcOk = (id: number, result: Record<string, unknown>) =>
  ({ jsonrpc: '2.0', id, result });
const jsonBody = (env: any) => new Response(JSON.stringify(env), {
  status: 200, headers: { 'content-type': 'application/json' },
});
/** CancelTask 成功信封(engineAbort=confirmed → cancelled-clean 唯自动解禁位) */
const cancelConfirmed = (id: number) => jrpcOk(id, {
  task: {
    id: TASK_ID, contextId: 'sess-1',
    status: { state: 'TASK_STATE_CANCELED' }, metadata: {},
  },
  engineAbort: 'confirmed',
});
const engineCard = () => ({
  supportedInterfaces: [{
    url: RPC_URL, protocolBinding: 'JSONRPC', protocolVersion: '1.0',
  }],
  capabilities: {
    streaming: true,
    pushNotifications: false,
    extensions: ['inject', 'context', 'replay', 'retry', 'cancel', 'resolve'].map((k) => ({
      url: `https://avernet.local/a2a/ext/${k}/v1`, required: false,
    })),
  },
});

const sseResponse = (envs: any[]) => new Response(
  new ReadableStream({
    start(c) {
      envs.forEach((e) => c.enqueue(enc.encode(`data: ${JSON.stringify(e)}\n\n`)));
      c.close();
    },
  }),
  { status: 200, headers: { 'content-type': 'text/event-stream' } },
);

interface DeferredSse { response: Response; push(chunk: string): void; cut(): void }
/** 可控 SSE:onRequest 时先 push 首个信封(建 run 锚);abort 后用 cut() 模拟
 * (真实 abort 的 AbortSignal 会 error 整流——本层拿不到 provider 内部 signal,手动等价) */
function deferredSse(): DeferredSse {
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const stream = new ReadableStream({ start(c) { controller = c; } });
  return {
    response: new Response(stream, {
      status: 200, headers: { 'content-type': 'text/event-stream' },
    }),
    push: (chunk: string) => controller.enqueue(enc.encode(chunk)),
    cut: () => {
      try { controller.error(new Error('aborted')); } catch { /* 已结束 */ }
    },
  };
}

interface Captured { url: string; init: RequestInit }

/** SendStreamingMessage 面的可编程产流位:按真请求 id 重打信封后产 Response */
let sseFactory: ((reqId: number) => Response) | undefined;
/** Review 第四轮：ReplayEvents 可编程回真事件流（SSE Response——replay 与主
 * 请求同走 streamRpc；旧任务恢复的终局回流生产位） */
let replayFactory: ((reqId: number) => Response) | undefined;
let calls: Captured[] = [];

async function routingFetch(input: any, init?: any): Promise<Response> {
  const call = { url: String(input), init: init as RequestInit };
  calls.push(call);
  if (call.url === CARD_URL) return jsonBody(engineCard());
  const req = JSON.parse(String((init as RequestInit).body));
  if (req.method === 'SendStreamingMessage' && sseFactory) return sseFactory(req.id);
  if (req.method === 'CancelTask') return jsonBody(cancelConfirmed(req.id));
  if (req.method === 'GetTask') return jsonBody(jrpcOk(req.id, { task: { id: TASK_ID, contextId: 'sess-1', status: { state: 'TASK_STATE_COMPLETED' }, metadata: { seq: 2, lastSeq: 2 } } }));
  // R2-3 第二补丁：ReplayEvents 缺省打网络级拒（typed transport）——恢复轮的
  // 每次尝试都确定性失败，预算耗尽即 RECOVERY_STOPPED（不依赖引擎语义）；
  // 第四轮起 replayFactory 可编程回真事件（宿主入口恢复旧任务的终局回流）
  if (req.method === 'ReplayEvents') {
    if (replayFactory) return replayFactory(req.id);
    throw new TypeError('reset');
  }
  return jsonBody(jrpcOk(req.id, {}));
}

// ─── 用例 ─────────────────────────────────────────────────────────────────

const BOT = {
  botId: 'b:1', realBotId: 'b1', ownerId: '2088', displayName: 'B',
  online: true, chatable: true, runtimeStage: 'online',
} as unknown as ChatBotView;

const mockedGetConnection = (ctrl as any).getBotConnection as jest.Mock;
const mockedGetIamToken = getBotIamToken as unknown as jest.Mock;

function buildProvider(handlers: {
  onMessage?: BotChatProvider['onMessage'];
  onComplete?: BotChatProvider['onComplete'];
}): { provider: BotChatProvider; outcomes: string[]; statuses: string[]; onComplete: jest.Mock } {
  const provider = createBotChatProvider({ bot: BOT, userId: '2088', sessionId: 'sess-1' });
  const onComplete = jest.fn();
  const outcomes: string[] = [];
  const statuses: string[] = [];
  provider.onMessage = handlers.onMessage;
  provider.onComplete = handlers.onComplete ?? onComplete;
  provider.subscribeToConnectionStatus((e) => statuses.push(e.status));
  provider.subscribeToCancelOutcome((outcome) => outcomes.push(outcome.kind));
  return { provider, outcomes, statuses, onComplete };
}

beforeEach(() => {
  jest.clearAllMocks();
  calls = [];
  sseFactory = undefined;
  replayFactory = undefined;
  storage.clear();
  storage.set(A2A_SWITCH_KEY, '1');
  (globalThis as any).fetch = routingFetch;
  mockedGetConnection.mockResolvedValue({
    data: { sockets: [{ kind: 'chat', url: WS_URL }], transport_mode: 'remote' },
  });
  mockedGetIamToken.mockResolvedValue('iam-1');
});

/** 文件收尾恢复 worker 全局(桩不得活过文件边界——见 shim 处留存注) */
afterAll(() => {
  if (RAW_WINDOW === undefined) delete (globalThis as any).window;
  else (globalThis as any).window = RAW_WINDOW;
  if (RAW_FETCH === undefined) delete (globalThis as any).fetch;
  else (globalThis as any).fetch = RAW_FETCH;
});

describe('BotChatProvider A2A 接缝活体集成（B16）', () => {
  it('探测收敛 → 工厂命中 A2AProvider:card 探测连接 + fixture 全流 onComplete 全文 + RPC 头带请求级刷新的 IAM token', async () => {
    // 异步正路先决出 appChoiceCache='a2a'（与工厂同缓存、同 endpointBase 派生）
    const built = await maybeBuildA2AProvider({ url: WS_URL, xIAMToken: 'iam-1' });
    expect(built).not.toBeNull();

    sseFactory = (reqId) => sseResponse(
      snapshotEnvelopes.map((e) => ({ ...e, id: reqId })),
    );
    const { provider, statuses, onComplete } = buildProvider({});
    const onMessage = jest.fn();
    provider.onMessage = onMessage;

    await provider.connect();
    expect(provider.isConnected).toBe(true);
    expect(statuses).toContain('connected');
    // 连接面 = card 探测（无 token）；未对 RPC 面发任何请求
    expect(calls.map((c) => c.url)).toContain(CARD_URL);
    expect(calls.filter((c) => c.url === RPC_URL)).toHaveLength(0);

    // 发送前 BotChatProvider.request 刷新 IAM 并经 updateProviderIamToken 直写
    // config.xIAMToken（既有通道零新增接线）→ A2AProvider.resolveIamToken 每请求重读。
    mockedGetIamToken.mockResolvedValue('iam-refreshed');
    await provider.request({ content: '你好', sessionId: 'sess-1' } satisfies BotChatRequest);

    const rpcCall = calls.find((c) => c.url === RPC_URL);
    expect(rpcCall).toBeDefined();
    const sent = JSON.parse(String(rpcCall!.init.body));
    expect(sent.method).toBe('SendStreamingMessage');
    expect(sent.params.message.contextId).toBe('sess-1');
    expect((rpcCall!.init.headers as Headers).get('x-iam-token')).toBe('iam-refreshed');

    // fixture 终态对（COMPLETED state-change + 同 seq final marker）→ onComplete 一次，全文你好！
    expect(onComplete).toHaveBeenCalledTimes(1);
    expect(onComplete.mock.calls[0][0][0].content).toBe('你好！');
    expect(onMessage).toHaveBeenCalled();
  });

  it('abort → CancelTask 控制面：禁令乐观挂起（isSendBanned=true）→ confirmed 唯自动解禁（false）+ 四态经 subscribeToCancelOutcome 转发', async () => {
    const built = await maybeBuildA2AProvider({ url: WS_URL, xIAMToken: 'iam-1' });
    expect(built).not.toBeNull();

    const sse = deferredSse();
    sseFactory = (reqId) => {
      sse.push(`data: ${JSON.stringify({
        jsonrpc: '2.0', id: reqId,
        result: {
          task: {
            id: TASK_ID, contextId: 'sess-1',
            status: { state: 'TASK_STATE_SUBMITTED' }, metadata: {},
          },
        },
      })}\n\n`);
      return sse.response;
    };

    const { provider, outcomes, onComplete } = buildProvider({});
    await provider.connect();

    const pending = provider.request({ content: '跑一个长任务', sessionId: 'sess-1' } satisfies BotChatRequest);
    // 等 in-flight run 建立锚（首 Task 事件消费后 recoveryAnchor 就位）
    await new Promise((r) => { setTimeout(r, 0); });
    expect(provider.isSendBanned()).toBe(false);

    provider.abort();
    // §3.1 乐观禁令：CancelTask dispatch 即挂（服务端核销前）；v3 钉板 onComplete([]) 恰一次
    expect(provider.isSendBanned()).toBe(true);
    expect(onComplete).toHaveBeenCalledTimes(1);
    expect(onComplete.mock.calls[0][0]).toEqual([]);

    // 真实 abort 语义 = AbortSignal error 整流（主动取消非失败）；此处无 provider 内部
    // signal 句柄,cut() 手动等价 → 迟帧栅栏拦截,request 静默收尾。
    sse.cut();
    const settled = await Promise.race([
      pending.then(() => 'resolved'),
      new Promise((r) => { setTimeout(() => r('timeout'), 5000); }),
    ]);
    expect(settled).toBe('resolved');
    await new Promise((r) => { setTimeout(r, 0); }); // CancelTask 控制面落地：confirmed → 唯自动解禁
    expect(provider.isSendBanned()).toBe(false);
    expect(outcomes).toContain('cancelled-clean');
    const cancelBodies = calls
      .map((c) => {
        try { return JSON.parse(String(c.init.body)); } catch { return null; }
      })
      .filter((b) => b?.method === 'CancelTask');
    expect(cancelBodies).toHaveLength(1);
    expect(cancelBodies[0].params.taskId).toBe(TASK_ID);
  });

  it('会话文件引用经 BotChatProvider → A2A 链路：提交前 typed 拒绝（不静默丢引用）'
    + ' + onError 上行 + 零 wire 出站', async () => {
    // v3 buildRequest 会把 resourceReferences/promptFileRefs 随 chat.send 上 wire
    // （engine ws_server.py 消费）；A2A 面 parts 只承载文本——缝上必须显式拒绝，
    // 否则 <file-ref insert_id> 留在正文里而映射静默丢失（PR#2934 review #2）。
    const built = await maybeBuildA2AProvider({ url: WS_URL, xIAMToken: 'iam-1' });
    expect(built).not.toBeNull();

    const { provider, onComplete } = buildProvider({});
    const onError = jest.fn();
    provider.onError = onError;
    await provider.connect();

    await expect(provider.request({
      content: '看下这个文件 <file-ref insert_id="1"/>', sessionId: 'sess-1',
      resourceReferences: [{ refId: '1' } as any],
    } satisfies BotChatRequest)).rejects.toThrow(A2A_FILE_REFS_UNSUPPORTED_MESSAGE);

    expect(onError).toHaveBeenCalledTimes(1);
    expect((onError.mock.calls[0][0] as any).failure)
      .toEqual({ kind: 'rpc', reason: 'file_refs_unsupported' });
    // 未假完成、未出站：card 探测之外零 RPC POST
    expect(onComplete).not.toHaveBeenCalled();
    expect(calls.filter((c) => c.url === RPC_URL)).toHaveLength(0);
    // 状态不粘滞：后续纯文本请求照常工作（fixture 全流收口）
    sseFactory = (reqId) => sseResponse(
      snapshotEnvelopes.map((e) => ({ ...e, id: reqId })),
    );
    await provider.request({ content: '你好', sessionId: 'sess-1' } satisfies BotChatRequest);
    expect(onComplete).toHaveBeenCalledTimes(1);
  });

  it('R2-3 传输回滚：会话已用 A2A → 开关关 → 同会话新发送经发送边界重估回 v3'
    + '（新任务不得继续创建 A2A；旧实例只断连静默退役）', async () => {
    // Stage 1：异步正路先决出 appChoiceCache='a2a'（会话第一段以 A2A 建立）
    const built = await maybeBuildA2AProvider({ url: WS_URL, xIAMToken: 'iam-1' });
    expect(built).not.toBeNull();

    // SDK 工厂 stub：镜像缺省工厂的两条候选（takeCached 命中→a2a，缺席→v3），
    // 记录创建/退役/发送轨迹——边界逻辑可精确断言（v3 stub 不真连 ws）
    const creations: string[] = [];
    const disconnects: string[] = [];
    const sendings: string[] = [];
    const stubFor = (kind: string): any => ({
      isA2aProvider: kind === 'a2a',
      isConnected: true,
      connect: () => Promise.resolve(),
      request: () => { sendings.push(kind); return Promise.resolve(); },
      disconnect: () => { disconnects.push(kind); },
      subscribeToConnectionStatus: () => () => undefined,
    });
    const provider = createBotChatProvider({
      bot: BOT, userId: '2088', sessionId: 'sess-1',
      createSdkProvider: (cfg: any) => {
        const kind = takeCachedA2AProvider(cfg) ? 'a2a' : 'v3';
        creations.push(kind);
        return stubFor(kind);
      },
    } as any);

    await provider.connect();
    await provider.request({ content: '第一条', sessionId: 'sess-1' } satisfies BotChatRequest);
    expect(creations).toEqual(['a2a']);            // 会话以 A2A 建立
    expect(sendings).toEqual(['a2a']);

    // Stage 2：现场关开关（spec §7 回滚语义入口）——发送边界重估须令
    // 新任务回 v3，而不是继续复用既有 A2A inner
    storage.set(A2A_SWITCH_KEY, '0');
    await provider.request({ content: '第二条', sessionId: 'sess-1' } satisfies BotChatRequest);

    expect(creations).toEqual(['a2a', 'v3']);      // 重走工厂：当下选择落 v3
    expect(sendings).toEqual(['a2a', 'v3']);       // 新任务经 v3 发送
    expect(disconnects).toEqual(['a2a']);         // 旧 A2A 实例只断连退役一次

    // Stage 3：开关再开——缓存仍 'a2a'，当下选择回 A2A；inner 已是 v3（非 A2A
    // 面不触发回滚门）——Up-换轨不是本 finding 义务面，只钉行为不误伤
    storage.set(A2A_SWITCH_KEY, '1');
    await provider.request({ content: '第三条', sessionId: 'sess-1' } satisfies BotChatRequest);
    expect(creations).toEqual(['a2a', 'v3']);
    expect(disconnects).toEqual(['a2a']);
  });

  it('R2-3 第四轮：未终结旧任务经宿主入口驱动——parked 恢复回流真终局、'
    + '回调隔离不冒充新请求完成、释放经 §3.1 有机核销（零内部触碰）', async () => {
    // 场景（reviewer 指定）：已知 taskId → 断流 → 预算耗尽未终结 → 开关关
    // → 新发送走 v3 且进行中 → 经 BotChatProvider 生产入口恢复旧任务 →
    // 真实终局事件回流 → 旧任务释放、新任务状态不受影响。
    const built = await maybeBuildA2AProvider({ url: WS_URL, xIAMToken: 'iam-1' });
    expect(built).not.toBeNull();

    // 微预算（预算耗尽确定性复现）：1 次尝试即耗尽 → RECOVERY_STOPPED
    const TINY_RECOVERY = {
      maxAttempts: 1, backoffCapsMs: [1], perCallTimeoutMs: 500, totalBudgetMs: 2000,
    } as const;
    let oldA2a: any = null;              // 仅用于断言身份与状态——驱动一律走宿主入口
    const v3Releases: Array<() => void> = [];   // v3 stub 请求放行队列（按序放行）
    let v3Inner: any = null;
    const provider = createBotChatProvider({
      bot: BOT, userId: '2088', sessionId: 'sess-1',
      createSdkProvider: (cfg: any) => {
        const a2a = takeCachedA2AProvider({ ...cfg, recovery: TINY_RECOVERY });
        if (a2a) { oldA2a = a2a; return a2a as unknown as any; }
        v3Inner = {
          isConnected: true,
          connect: () => Promise.resolve(),
          request: () => new Promise<void>((res) => { v3Releases.push(res); }),
          disconnect: () => undefined,
          subscribeToConnectionStatus: () => () => undefined,
        };
        return v3Inner;
      },
    } as any);
    const primaryComplete = jest.fn();
    const retiredCompletes: unknown[][] = [];
    const retiredErrors: Error[] = [];
    provider.onComplete = primaryComplete;
    provider.onRetiredTaskComplete = (msgs) => retiredCompletes.push(msgs);
    provider.onRetiredTaskError = (e) => { retiredErrors.push(e); console.log('ZZZZ-ERR', (e as any)?.failure, (e as Error).message); };
    await provider.connect();
    expect(oldA2a).not.toBeNull();

    // 第 1 段：A2A 出站拿 taskId → 断流 → 恢复预算耗尽（任务未终结）
    let sse1!: DeferredSse;
    sseFactory = (reqId) => {
      sse1 = deferredSse();
      sse1.push(`data: ${JSON.stringify({
        jsonrpc: '2.0', id: reqId,
        result: {
          task: { id: TASK_ID, contextId: 'sess-1',
            status: { state: 'TASK_STATE_WORKING' }, metadata: {} },
        },
      })}

`);
      return sse1.response;
    };
    const first = provider.request({ content: '长任务', sessionId: 'sess-1' } satisfies BotChatRequest);
    await new Promise((r) => { setTimeout(r, 0); });
    sse1.cut();
    await expect(first).rejects.toThrow('恢复已停止');
    expect(oldA2a.hasUnterminatedRecovery).toBe(true);

    // 第 2 段：开关关 → 新发送落 v3 **进行中**（挂起）；旧任务处理器保留
    storage.set(A2A_SWITCH_KEY, '0');
    const send2 = provider.request({ content: '第二条', sessionId: 'sess-1' } satisfies BotChatRequest);
    for (let i = 0; i < 200
      && ((provider as any).retiredA2aHandler ?? oldA2a) !== oldA2a; i += 1) {
      await new Promise((r) => { setTimeout(r, 1); });   // 换轨到位
    }
    for (let i = 0; i < 200 && v3Releases.length < 1; i += 1) {
      await new Promise((r) => { setTimeout(r, 1); });   // v3 请求出站入队
    }
    expect((provider as any).retiredA2aHandler).toBe(oldA2a);
    expect(oldA2a.isConnected).toBe(true);              // 保留≠断连退役

    // 第 3 段：**宿主生产入口**驱动旧任务恢复——ReplayEvents 回真终局流
    // sseResponse 收 envelope 对象数组（内部自行 data: 格式化）——终局两连发
    replayFactory = (reqId) => sseResponse([
      { jsonrpc: '2.0', id: reqId, result: {
        statusUpdate: { taskId: TASK_ID, contextId: 'sess-1',
          status: { state: 'TASK_STATE_COMPLETED' },
          metadata: { seq: 2, lastSeq: 2 } },
      } },
      { jsonrpc: '2.0', id: reqId, result: {
        statusUpdate: { taskId: TASK_ID, contextId: 'sess-1',
          status: { state: 'TASK_STATE_COMPLETED' },
          metadata: { seq: 2, final: 'true', lastSeq: 2 } },
      } },
    ]);
    const outcome = await provider.recoverRetiredA2aTask();
    expect(outcome).toMatchObject({ outcome: 'terminal', state: 'TASK_STATE_COMPLETED' });
    // 回调隔离（review 第四轮 #2）：旧任务终局走专用面，不冒充新请求完成
    expect(retiredCompletes).toHaveLength(1);
    expect(retiredErrors).toHaveLength(0);
    expect(primaryComplete).not.toHaveBeenCalled();    // 主 onComplete 归新 v3 请求独占
    expect((provider as any).retiredA2aHandler).toBe(oldA2a);   // 终局虽落仍持至下一边界

    // 第 4 段：新 v3 请求收尾——主面由新任务自己完成（未受旧任务污染）
    v3Inner?.onComplete?.([{ id: 'v3-msg', role: 'assistant', content: 'v3 答' }]);
    v3Releases[0]();                       // 放行第二条（第一只挂起的 v3 请求）
    await send2;
    expect(primaryComplete).toHaveBeenCalledTimes(1);

    // 第 5 段：§3.1 有机释放——runSettled 由真终局翻位（终局消费即核销），
    // 下一发送边界惰性断连释放，零内部触碰
    const send3 = provider.request({ content: '第三条', sessionId: 'sess-1' } satisfies BotChatRequest);
    for (let i = 0; i < 200 && (provider as any).retiredA2aHandler !== null; i += 1) {
      await new Promise((r) => { setTimeout(r, 1); });   // 边界惰性释放 settled parked
    }
    expect((provider as any).retiredA2aHandler).toBeNull();
    expect(oldA2a.isConnected).toBe(false);
    for (let i = 0; i < 200 && v3Releases.length < 2; i += 1) {
      await new Promise((r) => { setTimeout(r, 1); });   // request3 推进到 stub 请求入队
    }
    expect(v3Releases.length).toBeGreaterThanOrEqual(2);
    v3Releases[1]();             // 放行第三条（第二个挂起的 v3 请求）
    await send3;
  });

  it('R2-3 第四轮（取消入口）：cancelRetiredA2aTask = §3.1 用户明确放弃——'
    + 'WireCancelTask 真发、四态核销照常、引用即时释放', async () => {
    const built = await maybeBuildA2AProvider({ url: WS_URL, xIAMToken: 'iam-1' });
    expect(built).not.toBeNull();
    const TINY_RECOVERY = {
      maxAttempts: 1, backoffCapsMs: [1], perCallTimeoutMs: 500, totalBudgetMs: 2000,
    } as const;
    let oldA2a: any = null;
    const cancelOutcomes: string[] = [];
    const provider = createBotChatProvider({
      bot: BOT, userId: '2088', sessionId: 'sess-1',
      createSdkProvider: (cfg: any) => {
        const a2a = takeCachedA2AProvider({ ...cfg, recovery: TINY_RECOVERY });
        // 自定义 factory 绕过缺省 factory 的 A2A wiring（B12 onCancelOutcome
        // 转发）——补齐等价接线；emitCancelOutcome 的转发宿主 subscribe 面不走
        if (a2a) {
          oldA2a = a2a;
          a2a.onCancelOutcome = (o: { kind: string }) => cancelOutcomes.push(o.kind);
          return a2a as unknown as any;
        }
        return ({
          isConnected: true,
          connect: () => Promise.resolve(),
          request: () => Promise.resolve(),
          disconnect: () => undefined,
          subscribeToConnectionStatus: () => () => undefined,
        });
      },
    } as any);
    const outcomes: string[] = [];
    provider.subscribeToCancelOutcome((o) => outcomes.push(o.kind));
    await provider.connect();

    let sse1!: DeferredSse;
    sseFactory = (reqId) => {
      sse1 = deferredSse();
      sse1.push(`data: ${JSON.stringify({
        jsonrpc: '2.0', id: reqId,
        result: {
          task: { id: TASK_ID, contextId: 'sess-1',
            status: { state: 'TASK_STATE_WORKING' }, metadata: {} },
        },
      })}

`);
      return sse1.response;
    };
    const first = provider.request({ content: '长任务', sessionId: 'sess-1' } satisfies BotChatRequest);
    await new Promise((r) => { setTimeout(r, 0); });
    sse1.cut();
    await expect(first).rejects.toThrow('恢复已停止');

    storage.set(A2A_SWITCH_KEY, '0');
    await provider.request({ content: '第二条', sessionId: 'sess-1' } satisfies BotChatRequest);
    expect((provider as any).retiredA2aHandler).toBe(oldA2a);

    // 宿主取消入口：明确放弃——wire CancelTask 真发 + 引用即时释放
    provider.cancelRetiredA2aTask();
    await new Promise((r) => { setTimeout(r, 0); });   // CancelTask 控制面异步出站
    const cancelBodies = calls.map((c) => {
      try { return JSON.parse(String(c.init.body)); } catch { return null; }
    }).filter((b) => b?.method === 'CancelTask');
    expect(cancelBodies).toHaveLength(1);
    expect(cancelBodies[0].params.taskId).toBe(TASK_ID);
    expect((provider as any).retiredA2aHandler).toBeNull();
    expect(oldA2a.isConnected).toBe(false);
    // 释放后核销链仍在（控制面 promise 自洽 + onCancelOutcome 闭路照播）
    for (let i = 0; i < 200 && cancelOutcomes.length === 0; i += 1) {
      await new Promise((r) => { setTimeout(r, 1); });   // CancelTask 控制面落地核销
    }
    expect(cancelOutcomes).toEqual(['cancelled-clean']);
  });

});