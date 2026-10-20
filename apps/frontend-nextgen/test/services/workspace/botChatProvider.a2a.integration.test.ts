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
import { maybeBuildA2AProvider } from '@/adapters/a2a/bootstrap';
import { A2A_SWITCH_KEY } from '@/adapters/a2a/switch';
import * as ctrl from '@/services/backendApi/bots/privateBotSessionController';
import { getBotIamToken } from '@/services/backendApi/privateChat/iamTokenController';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';

jest.mock('@/services/backendApi/bots/privateBotSessionController');
jest.mock('@/services/backendApi/privateChat/iamTokenController');
// 注意:不 mock '@/adapters/a2a/*' 与 '@tc-chat/adapters'——本文件的价值就在真链路。

import { createBotChatProvider, type BotChatProvider } from '@/services/workspace/botChatProvider';
import type { BotChatRequest } from '@/services/workspace/botChatProvider';
import type { ChatBotView } from '@/services/workspace/botSessionService';

// ─── switch 所需的最小 window shim(node env 无 jsdom;switch.ts 只读 localStorage) ───

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
let calls: Captured[] = [];

async function routingFetch(input: any, init?: any): Promise<Response> {
  const call = { url: String(input), init: init as RequestInit };
  calls.push(call);
  if (call.url === CARD_URL) return jsonBody(engineCard());
  const req = JSON.parse(String((init as RequestInit).body));
  if (req.method === 'SendStreamingMessage' && sseFactory) return sseFactory(req.id);
  if (req.method === 'CancelTask') return jsonBody(cancelConfirmed(req.id));
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
  storage.clear();
  storage.set(A2A_SWITCH_KEY, '1');
  (globalThis as any).fetch = routingFetch;
  mockedGetConnection.mockResolvedValue({
    data: { sockets: [{ kind: 'chat', url: WS_URL }], transport_mode: 'remote' },
  });
  mockedGetIamToken.mockResolvedValue('iam-1');
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
});