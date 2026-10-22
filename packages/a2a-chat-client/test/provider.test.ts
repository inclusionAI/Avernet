/**
 * B10 — A2AProvider 核心循环测试（oracle：真 OpenClawParser；零网络，fetch 全注入）。
 * ============================================================================
 *
 * 钉死的断言事实源：
 *   · abort observable 语义 = OpenClawProvider.js:291-299
 *     （`_isRequesting=false` + `onComplete([])`，无 onError）；
 *   · run 取消终态（CANCELED/REJECTED）由 transport 层补发 onComplete([])
 *     （parser 对废除帧 no-op——OpenClawParser.js:637，B9 钉板表）；
 *   · run 失败终态 → parser.onError 钩子合错误消息（OpenClawProvider.js:154-172）
 *     + request() 以 `.failure`（kind='rpc', reason='run_failed'）typed 拒绝；
 *   · 迟帧栅栏：旧迭代器逃逸帧不得触 parser（no parser poison）。
 *
 * wire 口径对拍 __tests__/fixtures/engine-wire-snapshot.json（真引擎 A2aKernel
 * 产出——真实发布序，勿手改；重生成命令见 fixture 的 _header 字段与
 * scripts/gen-engine-wire-snapshot.py）。
 * SSE 信封 id 与 provider 内部 buildRpcRequest 的自增 id 对齐：fake fetch 从
 * 请求 body 读出 id 再造 fixture（rpc.ts 的 nextId 是模块级计数，勿假设初值）。
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import {
  A2AProvider,
  A2A_APPROVAL_ANSWERS_REQUIRED_MESSAGE,
  A2A_APPROVAL_UNSUPPORTED_MESSAGE,
  A2A_ATTACHMENTS_UNSUPPORTED_MESSAGE,
  A2A_FILE_REFS_UNSUPPORTED_MESSAGE,
  A2A_INJECT_UNSUPPORTED_MESSAGE,
  RECOVERY_INCOMPLETE_MESSAGE,
  RECOVERY_STOPPED_MESSAGE,
  OUTCOME_UNKNOWN_MESSAGE,
  type A2AProviderConfig,
} from '../src/index';
import { CANCEL_UI_TEXT, type CancelOutcome } from '../src/index';
import { createSendBanRegistry } from '../src/index';
import { loadOpenClawParser, createOracleParserFactory } from './helpers/oracleLoader';
import { A2aParserFactoryMissingError, type SdkParser, type SdkParserConfig }
  from '../src/index';
import type { WireEvent } from '../src/index';
import { a2aUrlsFromWsUrl } from '../src/index';
import { makeEngineCard } from './helpers';

// ─── wire 构造件 ─────────────────────────────────────────────────────────

const enc = new TextEncoder();
const SESSION = 'agent:main:main';
const A2A_URLS = a2aUrlsFromWsUrl('ws://127.0.0.1:20003/api/a2a/ws');

function sseResponse(chunks: string[]): Response {
  const stream = new ReadableStream({
    start(c) { chunks.forEach((ch) => c.enqueue(enc.encode(ch))); c.close(); },
  });
  return new Response(stream, {
    status: 200, headers: { 'content-type': 'text/event-stream' },
  });
}

const resultEnvelope = (id: number, result: Record<string, unknown>) =>
  ({ jsonrpc: '2.0', id, result });
const toSse = (envs: any[]) => envs.map((e) => `data: ${JSON.stringify(e)}\n\n`).join('');
/** fixture 信封按实际请求 id 重打（快照固定 id=1，rpc.ts nextId 全局递增勿假设） */
const reId = (envs: any[], id: number) => envs.map((e) => ({ ...e, id }));

const snapshotEnvelopes: any[] = (JSON.parse(readFileSync(
  join(__dirname, 'fixtures', 'engine-wire-snapshot.json'), 'utf8'))).envelopes;
/**
 * fixture 真实 run id（kernel 生成的 12-hex id——每轮重生成会变，消费测试
 * 一律派生，不得硬编码；后续拼入本 run 流的行（终态/审批/增量）同 id）。
 */
const SNAPSHOT_TASK_ID = snapshotEnvelopes[0].result.task.id as string;

/** 终态 statusUpdate 信封（engine wire_terminal_marker 口径） */
function terminalEnvelope(id: number, seq: number, state: string,
  extra: Record<string, unknown> = {}): any {
  return resultEnvelope(id, {
    statusUpdate: {
      taskId: SNAPSHOT_TASK_ID, contextId: 'c1',
      status: { state },
      metadata: { seq, final: 'true', lastSeq: seq, ...extra },
    },
  });
}

/** complete_run 的 state-change statusUpdate 信封（kernel 真实两连发的前一帧：
 * 终态 state + seq = last_seq+1，**非 final**——fixture 尾部同 seq 的 marker
 * 与它构成 §3.4 共 seq 事实对） */
function stateChangeEnvelope(id: number, seq: number, state: string): any {
  return resultEnvelope(id, {
    statusUpdate: {
      taskId: SNAPSHOT_TASK_ID, contextId: 'c1',
      status: { state },
      metadata: { seq },
    },
  });
}

/** INPUT_REQUIRED 审批帧信封（wire_interaction_requested 口径：engine_frame 全量）；
 * engineFrame 覆盖：kind/interactionId 等 gateway interaction.requested 字段按测注入 */
function approvalEnvelope(
  id: number, seq: number, engineFrame: Record<string, unknown> = {},
): any {
  return resultEnvelope(id, {
    statusUpdate: {
      taskId: SNAPSHOT_TASK_ID, contextId: 'c1',
      status: { state: 'TASK_STATE_INPUT_REQUIRED' },
      metadata: {
        seq,
        engine_frame: {
          id: 'apr-1',
          request: {
            command: 'npm install', resolvedPath: '/usr/bin/npm',
            cwd: '/workspace', sessionKey: SESSION,
          },
          createdAtMs: 1760000000000, expiresAtMs: 1760003600000,
          ...engineFrame,
        },
      },
    },
  });
}

/** 单信封 JSON-RPC 响应构造（B12：CancelTask/ResolveInteraction 控制面用） */
const jrpcOk = (id: number, result: Record<string, unknown>) =>
  ({ jsonrpc: '2.0', id, result });
const jrpcErr = (
  id: number, error: { code: number; message: string; data?: Record<string, unknown> },
) => ({ jsonrpc: '2.0', id, error });
const jsonResponse = (env: any) => new Response(JSON.stringify(env), {
  status: 200, headers: { 'content-type': 'application/json' },
});
/** CancelTask 成功信封（router._cancel_task 口径：task 快照 + engineAbort 附录） */
const cancelOk = (id: number, engineAbort: string) => jrpcOk(id, {
  task: { id: SNAPSHOT_TASK_ID, contextId: SESSION, status: { state: 'TASK_STATE_CANCELED' }, metadata: {} },
  engineAbort,
});

/** 迟到可控的单信封响应（CancelTask 控制面在测中手动落地） */
function deferredRpcResponse(): { response: Response; respond(env: any): void } {
  let resolve!: (r: Response) => void;
  const promise = new Promise<Response>((r) => { resolve = r; });
  return {
    response: promise as unknown as Response,
    respond: (env: any) => resolve(jsonResponse(env)),
  };
}

// ─── fake fetch（零网络；card / rpc 双面路由 + 调用捕获） ────────────────

interface DeferredSse { response: Response; push(chunk: string): void }

/**
 * 手泵 SSE 流。signal 传 AbortSignal 时对齐真 fetch abort 语义：
 * read() reject → streamRpc 静默 return（M1：主动取消非失败）。
 * fence 测试传 null：模拟"abort 之后旧迭代器仍有交付中的帧"逃逸窗口。
 */
function deferredSse(signal?: AbortSignal | null): DeferredSse {
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const stream = new ReadableStream({ start(c) { controller = c; } });
  signal?.addEventListener('abort', () => {
    try { controller.error(new Error('aborted')); } catch { /* 已结束 */ }
  });
  return {
    response: new Response(stream, {
      status: 200, headers: { 'content-type': 'text/event-stream' },
    }),
    push: (chunk: string) => controller.enqueue(enc.encode(chunk)),
  };
}

/** B11 断流靶：可编程 SSE——push 后可 breakStream（typed transport 断流口径） */
function breakableSse(): DeferredSse & { breakStream(): void } {
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const stream = new ReadableStream({ start(c) { controller = c; } });
  return {
    response: new Response(stream, {
      status: 200, headers: { 'content-type': 'text/event-stream' },
    }),
    push: (chunk: string) => controller.enqueue(enc.encode(chunk)),
    breakStream: () => {
      try { controller.error(new TypeError('reset')); } catch { /* 已结束 */ }
    },
  };
}

/** artifactUpdate 信封（B11 恢复测试的 backlog 半截流构造件） */
function artifactEnvelope(id: number, seq: number, text: string): any {
  return resultEnvelope(id, {
    artifactUpdate: {
      taskId: SNAPSHOT_TASK_ID, contextId: 'c1',
      artifact: { artifactId: 'text-0', parts: [{ text }] },
      append: true, lastChunk: false, metadata: { seq },
    },
  });
}

function cardResponse(body: any = makeEngineCard()): Response {
  return new Response(JSON.stringify(body), {
    status: 200, headers: { 'content-type': 'application/json' },
  });
}

type CapturedCall = { url: string; init: RequestInit };

/** 双面路由 fetch：cardUrl 走 card；rpcUrl 交由 rpcHandler 产 Response。 */
function routingFetch(
  rpcHandler: (req: any, call: CapturedCall) => Response,
  opts: { card?: Response } = {},
): typeof fetch & { calls: CapturedCall[] } {
  const calls: CapturedCall[] = [];
  const impl: any = async (url: string, init: RequestInit) => {
    const call = { url, init };
    calls.push(call);
    if (String(url) === A2A_URLS.cardUrl) return opts.card ?? cardResponse();
    const req = JSON.parse(String(init.body));
    return rpcHandler(req, call);
  };
  impl.calls = calls;
  return impl;
}

const sleep = (ms = 0) => new Promise((r) => { setTimeout(r, ms); });
async function until(pred: () => boolean, timeoutMs = 2000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (!pred()) {
    if (Date.now() > deadline) throw new Error('until() timeout');
    await sleep();
  }
}

function newProvider(overrides: Partial<A2AProviderConfig> = {}) {
  const provider = new A2AProvider({
    url: 'ws://127.0.0.1:20003/api/a2a/ws',
    a2aUrls: A2A_URLS,
    sessionKey: SESSION,
    parserFactory: createOracleParserFactory(),           // 宿主注入默认 @tc-chat 适配
    ...overrides,
  });
  const spies = {
    onMessage: jest.fn(),
    onComplete: jest.fn(),
    onError: jest.fn(),
  };
  provider.onMessage = spies.onMessage;
  provider.onComplete = spies.onComplete;
  provider.onError = spies.onError;
  return { provider, spies };
}

function rpcCallOf(fetchImpl: { calls: CapturedCall[] }, index = 0): CapturedCall {
  return fetchImpl.calls.filter((c) => c.url === A2A_URLS.rpcUrl)[index];
}
const reqIdOf = (call: CapturedCall) =>
  (JSON.parse(String(call.init.body)) as { id: number }).id;

// ─── connect：card 探测门 + 分场景能力门 ────────────────────────────────

describe('parserFactory 注入缝（公共/宿主解耦契约）', () => {
  const bareConfig = () => ({
    url: 'ws://127.0.0.1:20003/api/a2a/ws',
    a2aUrls: A2A_URLS,
    sessionKey: SESSION,
  });

  it('缺注入（js 调用方空洞）：构造不炸，首个入口飞 typed A2aParserFactoryMissingError', async () => {
    // connect() 首行即 ensureParser——缺工厂必在此炸，且不给"手搓兜底 parser"
    //（B8 钉板：真 parser 就是 oracle）
    const provider = new A2AProvider(bareConfig() as any);
    const err = await provider.connect().then(
      () => { throw new Error('expected rejection'); }, (e) => e);
    expect(err).toBeInstanceOf(A2aParserFactoryMissingError);
    expect((err as any).failure)
      .toEqual({ kind: 'protocol', reason: 'parser_factory_missing' });
    // 构造静默原则不破坏：无 unhandled rejection（构造期 catch 位），不发连接态广播
    const statuses: any[] = [];
    provider.subscribeToConnectionStatus((e) => statuses.push(e.status));
    await provider.request({ query: 'q', sessionKey: SESSION }).then(
      () => { throw new Error('expected rejection'); }, (e) => e);
    expect(statuses).toEqual([]);
  });

  it('factory 同步抛/异步拒都原样传播进 ensureParser 面（不吞不换型）', async () => {
    const provider = new A2AProvider({
      ...bareConfig(),
      parserFactory: (() => { throw new Error('boom-sync'); }) as any,
    });
    await expect(provider.connect()).rejects.toThrow('boom-sync');

    const provider2 = new A2AProvider({
      ...bareConfig(),
      parserFactory: async () => { throw new Error('boom-async'); },
    });
    await expect(provider2.connect()).rejects.toThrow('boom-async');
  });

  it('注入工厂收到 enableThinking/xIAMToken 构造参数（对位原构造面）', async () => {
    const seen: SdkParserConfig[] = [];
    const fakeParser = () => ({
      setSessionKey: () => undefined, reset: () => undefined,
      handleMessage: () => false, getResult: () =>
        ({ blocks: [], content: '', done: false, toolCalls: [], isApproval: false }),
    }) as unknown as SdkParser;
    const provider = new A2AProvider({
      ...bareConfig(),
      enableThinkingTag: true,
      xIAMToken: 'tok-1',
      parserFactory: (cfg) => {
        seen.push(cfg);
        return fakeParser();
      },
    });
    // connect 走 ensureParser → 工厂被调；card 探测必然失败，但工厂断言不受影响
    await provider.connect().then(() => undefined, () => undefined);
    expect(seen).toEqual([{ enableThinking: true, xIAMToken: 'tok-1' }]);
  });
});

describe('请求隔离度线 — generation 状态栅栏 + 出站代次绑定（review R2-1/2-2）', () => {
  it('R2-1 交错：取消A(未出站) → B启动出站 → A 延迟退出不得清 B 的在途态 → 取消 B 正常播报', async () => {
    const gates: Array<(t: string) => void> = [];
    const iamTokenGetter = () => new Promise<string>((r) => { gates.push(r); });
    let deferred!: DeferredSse;
    const fetchImpl = routingFetch((_req, call) => {
      const signal = (call.init as any).signal as AbortSignal;
      if (signal.aborted) throw new TypeError('aborted');   // 真fetch语义：预中止即拒
      deferred = deferredSse(signal);
      return deferred.response;
    });
    const { provider, spies } = newProvider({ fetchImpl, iamTokenGetter });
    const outcomes: CancelOutcome[] = [];
    provider.onCancelOutcome = (o) => outcomes.push(o);
    await provider.connect();

    // A 启动 → 停在 iam 窗口（未出站）→ abort：cancelled-clean
    const inFlightA = provider.request({ query: 'a', sessionKey: SESSION });
    await until(() => gates.length === 1);
    provider.abort();
    expect(outcomes).toEqual([{ kind: 'cancelled-clean' }]);

    // B 启动 → 出站（无事件流，未收首帧）——B 是当前在途请求
    const inFlightB = provider.request({ query: 'b', sessionKey: SESSION });
    await until(() => gates.length === 2);
    gates[1]('tok-b');
    await until(() => rpcCallOf(fetchImpl) !== undefined);      // B 已 POST

    // A 的旧请求此刻才收回 token 延迟退出——**不得**触碰 B 的 isRequesting
    gates[0]('tok-a');
    await inFlightA;

    // 取消 B：必须仍被视为在途（bug 下 A 的让位写把它清成空闲 → no-op 零播报）
    provider.abort();
    await until(() => outcomes.length === 2);
    expect(outcomes[1]).toEqual({ kind: 'task-outcome-unknown' });   // B 已出站首帧前
    await inFlightB;                                                // B 静默收尾：无 error 播报
    expect(spies.onError).not.toHaveBeenCalled();
  });

  it('R2-2 复用：上一请求曾出站后，新请求停在 iam 窗口中止 → 仍是 cancelled-clean（出站标记不跨请求残留）', async () => {
    const gates: Array<(t: string) => void> = [];
    const iamTokenGetter = () => new Promise<string>((r) => { gates.push(r); });
    // 第一请求正常收口：fixture 全流 → COMPLETED 终局
    const fetchImpl = routingFetch((req) =>
      sseResponse([toSse(reId(snapshotEnvelopes, req.id))]));
    const { provider, spies } = newProvider({ fetchImpl, iamTokenGetter });
    const outcomes: CancelOutcome[] = [];
    provider.onCancelOutcome = (o) => outcomes.push(o);
    await provider.connect();

    const first = provider.request({ query: '一梭', sessionKey: SESSION });
    await until(() => gates.length === 1);
    gates[0]('tok-1');
    await first;                                            // 出站过（rpcDispatched 曾置位）
    expect(spies.onComplete).toHaveBeenCalledTimes(1);

    // 复用同 provider：第二请求停在 iam 窗口（确定未出站）→ abort 应 clean
    const second = provider.request({ query: '二梭', sessionKey: SESSION });
    await until(() => gates.length === 2);
    provider.abort();
    expect(outcomes).toEqual([{ kind: 'cancelled-clean' }]);   // bug：残留 true → unknown
    gates[1]('tok-2');                                       // 让延迟收口不悬挂
    await second;
  });
});

describe('connect() — card probe + scenario gate before connection state', () => {
  it('探测成功：isConnected=true，状态订阅 connecting→connected；只发一次探测', async () => {
    const fetchImpl = routingFetch(() => { throw new Error('no rpc expected'); });
    const { provider } = newProvider({ fetchImpl });
    const statuses: any[] = [];
    provider.subscribeToConnectionStatus((e) => statuses.push(e));
    expect(provider.isConnected).toBe(false);

    await provider.connect();
    expect(provider.isConnected).toBe(true);
    expect(statuses.map((s) => s.status)).toEqual(['connecting', 'connected']);
    expect(statuses[1]).toMatchObject({ retryCount: 0 });
    // 探测通过后不缓存：TransportChoiceCache 归 B13；本层只过了 card 一次
    expect(fetchImpl.calls).toHaveLength(1);
  });

  it('网络失败：a2a probe failed: network（连接态不置位）', async () => {
    const fetchImpl: any = async (url: string) => {
      throw new TypeError(`fetch failed: ${url}`);
    };
    const { provider } = newProvider({ fetchImpl: fetchImpl as any });
    await expect(provider.connect()).rejects.toThrow('a2a probe failed: network');
    expect(provider.isConnected).toBe(false);
  });

  it('非 JSON body：a2a probe failed: bad_json', async () => {
    const fetchImpl = routingFetch(() => { throw new Error('no rpc'); }, {
      card: new Response('<html>not json</html>', {
        status: 200, headers: { 'content-type': 'text/html' },
      }),
    });
    const { provider } = newProvider({ fetchImpl });
    await expect(provider.connect()).rejects.toThrow('a2a probe failed: bad_json');
  });

  it('HTTP 非 200：a2a probe failed: http_503', async () => {
    const fetchImpl = routingFetch(() => { throw new Error('no rpc'); }, {
      card: new Response('nope', { status: 503 }),
    });
    const { provider } = newProvider({ fetchImpl });
    await expect(provider.connect()).rejects.toThrow('a2a probe failed: http_503');
  });

  it('探测超时（5000ms 门）：a2a probe failed: timeout', async () => {
    jest.useFakeTimers();
    try {
      const fetchImpl: any = (_url: string, init: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
          (init as any).signal?.addEventListener('abort',
            () => reject(new Error('aborted')));
        });
      const { provider } = newProvider({ fetchImpl: fetchImpl as any });
      const pending = provider.connect();
      // 先挂拒绝断言（防 timer 推进窗口内 unhandled rejection 噪音）
      // eslint-disable-next-line jest/valid-expect -- 延后 await 的挂断言模式：挂上后先推进假 timer 再 await（保证断言时 timer 窗口必已走完）
      const assertion = expect(pending).rejects.toThrow('a2a probe failed: timeout');
      await jest.advanceTimersByTimeAsync(5000);
      await assertion;
      expect(provider.isConnected).toBe(false);
    } finally {
      jest.useRealTimers();
    }
  });

  it('能力门不达标（非流式卡）：a2a probe failed: stream:not_streaming', async () => {
    const fetchImpl = routingFetch(() => { throw new Error('no rpc'); }, {
      card: cardResponse(makeEngineCard({ streaming: false })),
    });
    const { provider } = newProvider({ fetchImpl });
    await expect(provider.connect())
      .rejects.toThrow('a2a probe failed: stream:not_streaming');
    expect(provider.isConnected).toBe(false);
  });
});

// ─── request：核心消费循环 ───────────────────────────────────────────────

describe('request() — A2A wire → 真 parser 的主回路', () => {
  it('happy path（kernel 真实发布序）：state-change@N+1 → 同 seq final marker →'
    + ' onComplete 一次，全文"你好！"上钉 parse 面（B14 §3.4 钉板）', async () => {
    const fetchImpl = routingFetch((req) =>
      sseResponse([toSse(reId(snapshotEnvelopes, req.id))]));
    const { provider, spies } = newProvider({ fetchImpl });
    await provider.connect();

    const frames: any[] = [];
    provider.subscribeToParseResult((r) => frames.push(r));
    await provider.request({ query: '你好', sessionKey: SESSION }, 'msg-1');

    // parser 终态驱动（钉板 #7）：onComplete 一次，done:true，全文快照正确（#3）。
    // kernel 事实（complete_run）：fixture 尾部两行同 seq=6——state-change
    //（非 final）先消费把游标推到 6，随后 marker@6 若被 inclusive 去重吞掉，
    // settleRun 永无 final 账可收、UI 挂死（F1 的 REPO）。§3.4："同一 seq 上
    // 未应用的 final marker 必须应用"——marker 豁免去重、仍只完成一次。
    expect(spies.onComplete).toHaveBeenCalledTimes(1);
    const [message] = spies.onComplete.mock.calls[0][0] as any[];
    expect(message).toMatchObject({
      id: 'msg-1', role: 'assistant', status: 'done', content: '你好！',
    });
    expect(frames.at(-1)).toMatchObject({ done: true, content: '你好！' });
    expect(frames.at(-1).blocks).toEqual([
      expect.objectContaining({ type: 'text', content: '你好！' }),
    ]);
    // 请求体口径：SendStreamingMessage + 单 part text + contextId=sessionKey + messageId
    const req = JSON.parse(String(rpcCallOf(fetchImpl).init.body));
    expect(req).toMatchObject({
      jsonrpc: '2.0',
      method: 'SendStreamingMessage',
      params: { message: { messageId: 'msg-1', contextId: SESSION } },
    });
    expect(req.params.message.parts).toEqual([{ text: '你好' }]);
    // 恢复游标锚在终态 seq（B11 起的 applied-cursor）；会话键关联锚同刻位。
    // marker 与 state-change 共 seq=6：游标即 6（不是 5——marker 也入账）
    expect(provider.lastAppliedSeq).toBe(6);
    expect(provider.currentSessionKey).toBe(SESSION);
    // delta 流式期 onMessage 有播报
    expect(spies.onMessage).toHaveBeenCalled();
  });

  it('未连接自动 connect（对位 OpenClawProvider.js:228-234）', async () => {
    const fetchImpl = routingFetch((req) =>
      sseResponse([toSse(reId(snapshotEnvelopes, req.id))]));
    const { provider } = newProvider({ fetchImpl });
    expect(provider.isConnected).toBe(false);
    await provider.request({ query: 'hi', sessionKey: SESSION });
    expect(provider.isConnected).toBe(true);
  });

  it('在途互斥：重复 request 直接拒绝（对位 :222-226）', async () => {
    let deferred!: DeferredSse;
    const fetchImpl = routingFetch((_req, call) => {
      deferred = deferredSse((call.init as any).signal as AbortSignal);
      return deferred.response;
    });
    const { provider, spies } = newProvider({ fetchImpl });
    await provider.connect();

    const inFlight = provider.request({ query: 'a', sessionKey: SESSION });
    await until(() => rpcCallOf(fetchImpl) !== undefined);
    await expect(provider.request({ query: 'b', sessionKey: SESSION }))
      .rejects.toThrow('A request is already in progress');

    // 善后：在途项正常收尾（终帧直接 COMPLETED，空正文也算完成）
    deferred.push(toSse([terminalEnvelope(reqIdOf(rpcCallOf(fetchImpl)), 1,
      'TASK_STATE_COMPLETED')]));
    await inFlight;
    expect(spies.onComplete).toHaveBeenCalledTimes(1);
  });

  it('会话文件引用：A2A wire 无承载契约——提交前 typed 拒绝（不静默丢引用），状态不粘滞',
    async () => {
      const fetchImpl = routingFetch(() => { throw new Error('no rpc expected'); });
      const { provider, spies } = newProvider({ fetchImpl });
      await provider.connect();

      // v3 buildRequest 会把两字段发上 wire（engine ws_server.py 消费）；
      // A2A 面 parts 只承载文本——必须在出站前显式拒绝，不得静默丢
      await expect(provider.request({
        query: 'q <file-ref insert_id="1">', sessionKey: SESSION,
        resourceReferences: [{ refId: '1' }] as any,
      })).rejects.toThrow(A2A_FILE_REFS_UNSUPPORTED_MESSAGE);
      await expect(provider.request({
        query: 'q', sessionKey: SESSION, promptFileRefs: [{ no: 1 }] as any,
      })).rejects.toThrow(A2A_FILE_REFS_UNSUPPORTED_MESSAGE);
      // onError 上行 + typed failure（宿主 toast 消费面）；无 onComplete 假完成
      expect(spies.onError).toHaveBeenCalledTimes(2);
      expect((spies.onError.mock.calls[0][0] as any).failure)
        .toEqual({ kind: 'rpc', reason: 'file_refs_unsupported' });
      expect(spies.onComplete).not.toHaveBeenCalled();
      // 零 wire 出站：card 探测之外无任何 POST
      expect(fetchImpl.calls.filter((c) => c.url === A2A_URLS.rpcUrl)).toHaveLength(0);
      // 状态不粘滞：isRequesting 未占用——纯文本请求照常工作
      expect(provider.isConnected).toBe(true);
    });

  it('附件与 inject：A2A 面 parts 只承载文本——提交前 typed 拒绝（不静默丢附件/不换执行语义），状态不粘滞',
    async () => {
      const fetchImpl = routingFetch(() => { throw new Error('no rpc expected'); });
      const { provider, spies } = newProvider({ fetchImpl });
      await provider.connect();

      // v3 SDK 把附件写上 wire（chat.send 携带 attachments）、inject 走
      // chat.inject（注入语义，非任务执行）；A2A 面两者的 wire 契约都缺——
      // 必须出站前拒绝，不得把图片请求降级成纯文本、不得把注入当任务发
      await expect(provider.request({
        query: '帮我分析这张图', sessionKey: SESSION,
        attachments: [{ id: 'file-1', kind: 'image' }] as any,
      })).rejects.toThrow(A2A_ATTACHMENTS_UNSUPPORTED_MESSAGE);
      await expect(provider.request({
        query: '[注入] 上下文材料', sessionKey: SESSION, isInject: true as any,
      })).rejects.toThrow(A2A_INJECT_UNSUPPORTED_MESSAGE);
      // onError 上行 + typed failure（宿主 toast 消费面，与 fileRefs 门同构）
      expect(spies.onError).toHaveBeenCalledTimes(2);
      expect((spies.onError.mock.calls[0][0] as any).failure)
        .toEqual({ kind: 'rpc', reason: 'attachments_unsupported' });
      expect((spies.onError.mock.calls[1][0] as any).failure)
        .toEqual({ kind: 'rpc', reason: 'inject_unsupported' });
      expect(spies.onComplete).not.toHaveBeenCalled();
      // 零 wire 出站 + 状态不粘滞
      expect(fetchImpl.calls.filter((c) => c.url === A2A_URLS.rpcUrl)).toHaveLength(0);
      expect(provider.isConnected).toBe(true);
    });

  it('取消终态（CANCELED）：request 正常 resolve + transport 层补发 onComplete([])；无 onError',
    async () => {
      const fetchImpl = routingFetch((req) => sseResponse([toSse(reId([
        ...snapshotEnvelopes.slice(0, 4),                     // task+WORKING+两段正文
        terminalEnvelope(req.id, 5, 'TASK_STATE_CANCELED'),   // 终态换 CANCELED（游标4之后）
      ], req.id))]));
      const { provider, spies } = newProvider({ fetchImpl });
      await provider.connect();

      await provider.request({ query: 'q', sessionKey: SESSION });

      // v3 abort observable 钉板（OpenClawProvider.js:291-299）：onComplete([])
      expect(spies.onComplete).toHaveBeenCalledTimes(1);
      expect(spies.onComplete).toHaveBeenCalledWith([]);
      // parser 对废除帧纯 no-op（B9 表）：无 onError，无 parser 侧终态 onComplete
      expect(spies.onError).not.toHaveBeenCalled();
      expect(provider.getParseResult().blocks).toEqual([
        expect.objectContaining({ type: 'text', content: '你好' }),  // 流过正文留存
      ]);
    });

  it('失败终态（FAILED+errorCode）：parser.onError 钩子驱动 UI，request 以 typed failure 拒绝',
    async () => {
      const fetchImpl = routingFetch((req) => sseResponse([toSse(reId([
        ...snapshotEnvelopes.slice(0, 4),
        terminalEnvelope(req.id, 5, 'TASK_STATE_FAILED', {
          errorCode: 'E_PROVIDER_TIMEOUT', errorMessage: 'model provider timeout',
        }),
      ], req.id))]));
      const { provider, spies } = newProvider({ fetchImpl });
      await provider.connect();

      await expect(provider.request({ query: 'q', sessionKey: SESSION }))
        .rejects.toMatchObject({
          message: 'model provider timeout',
          failure: {
            kind: 'rpc',           // 业务失败桶（rpc.ts 头注口径）
            reason: 'run_failed',  // A2aRunFailure 标注
            errorCode: 'E_PROVIDER_TIMEOUT',
          },
        });

      // OpenClawProvider.js:154-172 镜像：错误消息先 onMessage(status='error')再 onError
      expect(spies.onError).toHaveBeenCalledTimes(1);
      expect(spies.onError.mock.calls[0][0].message).toBe('model provider timeout');
      const lastMsg = spies.onMessage.mock.calls.at(-1)![0] as any;
      expect(lastMsg).toMatchObject({
        role: 'assistant', content: 'model provider timeout', status: 'error',
      });
      expect(spies.onComplete).not.toHaveBeenCalled();
    });

  it('首 Task 事件前断流（sse_eof_no_final 零事件）：结果未知止损——typed prior_to_task_event、'
      + '零后续引擎调用、连接面不掉线（B11 §3#2）', async () => {
    const fetchImpl = routingFetch(() => sseResponse([]));   // 零事件即关流
    const { provider, spies } = newProvider({ fetchImpl });
    await provider.connect();

    await expect(provider.request({ query: 'q', sessionKey: SESSION }))
      .rejects.toMatchObject({
        message: OUTCOME_UNKNOWN_MESSAGE,
        failure: { kind: 'transport', reason: 'prior_to_task_event' },
      });
    expect(spies.onError).toHaveBeenCalledTimes(1);
    expect(spies.onError.mock.calls[0][0].message).toBe(OUTCOME_UNKNOWN_MESSAGE);
    // spec §3 #2 no-auto-fetch promise：零后续引擎调用（无 Replay/GetTask/Subscribe）
    expect(fetchImpl.calls.filter((c) => c.url === A2A_URLS.rpcUrl)).toHaveLength(1);
    // v3 transport.onError 对位（:98-107）：card 门不受单次断流影响
    expect(provider.isConnected).toBe(true);
  });

  it('iamToken：getter 解析值进 header；getter 缺密回落 xIAMToken；无凭证不带 header',
    async () => {
      // 1) getter 动态解析值进 x-iam-token
      const fetchA = routingFetch((req) =>
        sseResponse([toSse(reId(snapshotEnvelopes, req.id))]));
      const a = newProvider({
        fetchImpl: fetchA, iamTokenGetter: async () => 'tok-dynamic',
      });
      await a.provider.connect();
      await a.provider.request({ query: 'q', sessionKey: SESSION });
      expect((rpcCallOf(fetchA).init.headers as Headers).get('x-iam-token'))
        .toBe('tok-dynamic');

      // 2) getter 返回 undefined → 回落静态 xIAMToken
      const fetchB = routingFetch((req) =>
        sseResponse([toSse(reId(snapshotEnvelopes, req.id))]));
      const b = newProvider({
        fetchImpl: fetchB, xIAMToken: 'tok-static',
        iamTokenGetter: async () => undefined,
      });
      await b.provider.connect();
      await b.provider.request({ query: 'q', sessionKey: SESSION });
      expect((rpcCallOf(fetchB).init.headers as Headers).get('x-iam-token'))
        .toBe('tok-static');

      // 3) 无任何凭证：不带 header
      const fetchC = routingFetch((req) =>
        sseResponse([toSse(reId(snapshotEnvelopes, req.id))]));
      const c = newProvider({ fetchImpl: fetchC });
      await c.provider.connect();
      await c.provider.request({ query: 'q', sessionKey: SESSION });
      expect((rpcCallOf(fetchC).init.headers as Headers).get('x-iam-token'))
        .toBeNull();
    });

  it('审批：HITL 帧建 pending approval（透传 parser 公共机制），resolveApproval wire 成功→两步释放+按序重喂阻塞帧',
    async () => {
      const fetchImpl = routingFetch((req) => {
        if (req.method === 'ResolveInteraction') {
          return jsonResponse(jrpcOk(req.id, { interaction: { resolved: true, runId: SNAPSHOT_TASK_ID } }));
        }
        // 审批 seq5 之后的两段正文按序阻塞（shouldBlockMessage：pending 期 chat 全拦）
        return sseResponse([toSse(reId([
          ...snapshotEnvelopes.slice(0, 4),
          approvalEnvelope(req.id, 5),
          artifactEnvelope(req.id, 6, '吗'),
          terminalEnvelope(req.id, 7, 'TASK_STATE_COMPLETED'),
        ], req.id))]);
      });
      const { provider, spies } = newProvider({ fetchImpl });
      await provider.connect();
      expect(provider.getPendingApproval()).toBeNull();

      await provider.request({ query: 'q', sessionKey: SESSION });

      const pending = provider.getPendingApproval();
      expect(pending?.approvalId).toBe('apr-1');
      expect(pending?.approvalData).toMatchObject({
        request: { command: 'npm install' },
      });
      // 终帧被审批阻塞拦下（shouldBlockMessage 语义）：释放前无 onComplete
      expect(spies.onComplete).not.toHaveBeenCalled();

      await provider.resolveApproval('apr-1', 'allow-once');

      // wire 请求体钉精确（engine E4 契约：interactionId 优先 + exec 直传决策）
      const resolveCall = fetchImpl.calls
        .filter((c) => c.url === A2A_URLS.rpcUrl)
        .map((c) => JSON.parse(String(c.init.body)))
        .find((b) => b.method === 'ResolveInteraction');
      expect(resolveCall).toMatchObject({
        jsonrpc: '2.0',
        method: 'ResolveInteraction',
        params: {
          interactionId: 'apr-1',
          resolution: { kind: 'exec', decision: 'allow-once' },
        },
      });
      // 两步释放（B8 钉板）：pending 清空 + allow 面按序重喂被阻塞帧
      //（段序打乱的话 final 先到，终稿内容即缺尾）→ onComplete 补播一次
      expect(provider.getPendingApproval()).toBeNull();
      expect(spies.onComplete).toHaveBeenCalledTimes(1);
      expect((spies.onComplete.mock.calls[0][0] as any[])[0])
        .toMatchObject({ content: '你好吗', status: 'done' });
    });

  it('审批映射表 pin：v3 decision × engine kind → resolution（engine gateway 合法词汇表）', async () => {
    // 每轮：全文流（段→审批→被阻塞终态）→ pending 建立 → resolveApproval
    // → 逐轮核销 ResolveInteraction body（终态随审批释放按序重喂收尾）
    const bodies: any[] = [];
    const round = async (frame: Record<string, unknown>,
      id: string, decision: 'allow-once' | 'deny', wire: Record<string, unknown>) => {
      const impl = routingFetch((r) => {
        if (r.method === 'ResolveInteraction') {
          bodies.push(r);
          return jsonResponse(jrpcOk(r.id, { interaction: { resolved: true, runId: SNAPSHOT_TASK_ID } }));
        }
        return sseResponse([toSse(reId([
          ...snapshotEnvelopes.slice(0, 4),
          approvalEnvelope(r.id, 5, frame),
          terminalEnvelope(r.id, 6, 'TASK_STATE_COMPLETED'),
        ], r.id))]);
      });
      const p = newProvider({ fetchImpl: impl });
      await p.provider.connect();
      await p.provider.request({ query: 'q', sessionKey: SESSION });
      expect(p.provider.getPendingApproval()).not.toBeNull();
      await p.provider.resolveApproval(id, decision);
      expect(bodies.at(-1).params).toEqual(wire);
    };

    // exec（含 kind 缺省=v3 形 exec.approval.requested）：decision 直传（gateway 合法表）
    await round({}, 'apr-1', 'allow-once', { interactionId: 'apr-1', resolution: { kind: 'exec', decision: 'allow-once' } });
    await round({ kind: 'exec' }, 'apr-1', 'deny', { interactionId: 'apr-1', resolution: { kind: 'exec', decision: 'deny' } });
    // mode_switch：allow-once→proceed / deny→stay（gateway mode_transition.resolve 词汇）
    await round({ kind: 'mode_switch' }, 'apr-1', 'allow-once', { interactionId: 'apr-1', resolution: { kind: 'mode_switch', decision: 'proceed' } });
    await round({ kind: 'mode_switch' }, 'apr-1', 'deny', { interactionId: 'apr-1', resolution: { kind: 'mode_switch', decision: 'stay' } });
    // ask_user：deny→cancel（无载荷正当路径）；interactionId 优先于 parser approvalId
    await round({ kind: 'ask_user', interactionId: 'int:9' }, 'int:9', 'deny',
      { interactionId: 'int:9', resolution: { kind: 'ask_user', decision: 'cancel' } });
    // ask_user + allow-once：typed 拒绝（v3 decision 枚举无答案载荷面）——零 wire POST
    const before = bodies.length;
    const implAsk = routingFetch((r) => sseResponse([toSse(reId([
      ...snapshotEnvelopes.slice(0, 4),
      approvalEnvelope(r.id, 5, { kind: 'ask_user', interactionId: 'int:ask' }),
      terminalEnvelope(r.id, 6, 'TASK_STATE_COMPLETED'),
    ], r.id))]));
    const askP = newProvider({ fetchImpl: implAsk });
    await askP.provider.connect();
    await askP.provider.request({ query: 'q', sessionKey: SESSION });
    await expect(askP.provider.resolveApproval('int:ask', 'allow-once'))
      .rejects.toMatchObject({
        message: A2A_APPROVAL_ANSWERS_REQUIRED_MESSAGE,
        failure: { kind: 'protocol', reason: 'approval_answers_required' },
      });
    expect(bodies.length).toBe(before);          // 未发 ResolveInteraction
    expect(askP.provider.getPendingApproval()).not.toBeNull();   // pending 保持
  });

  it('§5 resolve_unsupported：声明≠实现——typed 拒绝、pending 保持、零假完成', async () => {
    const fetchImpl = routingFetch((req) => {
      if (req.method === 'ResolveInteraction') {
        return jsonResponse(jrpcErr(req.id, {
          code: -32000,
          message: 'active engine ChatService has no resolve_exec_approval',
          data: { reason: 'resolve_unsupported' },
        }));
      }
      return sseResponse([toSse(reId([
        ...snapshotEnvelopes.slice(0, 4),
        approvalEnvelope(req.id, 5),
        terminalEnvelope(req.id, 6, 'TASK_STATE_COMPLETED'),
      ], req.id))]);
    });
    const { provider, spies } = newProvider({ fetchImpl });
    await provider.connect();
    await provider.request({ query: 'q', sessionKey: SESSION });

    await expect(provider.resolveApproval('apr-1', 'allow-once'))
      .rejects.toMatchObject({
        message: A2A_APPROVAL_UNSUPPORTED_MESSAGE,
        failure: { kind: 'protocol', reason: 'approval_unsupported' },
      });
    // 引擎未接受决策：pending 保持（不假完成、不挂死——UI 侧 'unresolvable' 分诊）
    const pending = provider.getPendingApproval();
    expect(pending?.approvalId).toBe('apr-1');
    // 两步释放未执行：被阻塞帧未重喂 → 无 onComplete，内容无追加
    expect(spies.onComplete).not.toHaveBeenCalled();
    expect(provider.getParseResult().content).toBe('你好');
  });
});

// ─── abort / disconnect / 迟帧栅栏 ──────────────────────────────────────

describe('abort() / disconnect() / 迟帧栅栏', () => {
  it('abort 中断流：onComplete([]) 一次（双 abort 幂等）；后续 request 全量工作（no poison）',
    async () => {
      let deferred!: DeferredSse;
      const fetchImpl = routingFetch((req, call) => {
        if (!deferred) {
          deferred = deferredSse((call.init as any).signal as AbortSignal);
          return deferred.response;
        }
        return sseResponse([toSse(reId(snapshotEnvelopes, req.id))]);
      });
      const { provider, spies } = newProvider({ fetchImpl });
      await provider.connect();

      const inFlight = provider.request({ query: 'q', sessionKey: SESSION });
      await until(() => rpcCallOf(fetchImpl) !== undefined);
      const idA = reqIdOf(rpcCallOf(fetchImpl));
      deferred.push(toSse(reId(snapshotEnvelopes.slice(2, 3), idA)));   // 首段 delta 你（首块 append=false 落地）
      await until(() => provider.getParseResult().content === '你');

      provider.abort();
      provider.abort();                       // 幂等：no-throw + 不重复播报
      await inFlight;                          // request 静默 resolve（非拒绝）
      expect(spies.onComplete).toHaveBeenCalledTimes(1);
      expect(spies.onComplete).toHaveBeenCalledWith([]);
      expect(spies.onError).not.toHaveBeenCalled();

      // no poison：紧随其后的新 request（新代）全量可工作，parser 重建正确
      await provider.request({ query: 'q2', sessionKey: SESSION });
      expect(spies.onComplete).toHaveBeenCalledTimes(2);
      expect((spies.onComplete.mock.calls[1][0] as any[])[0])
        .toMatchObject({ content: '你好！', status: 'done' });
      expect(provider.getParseResult().content).toBe('你好！');
    });

  it('空闲 abort 纯 no-op：无在途时不自发 onComplete（v3 重复调用缺陷的收紧，注记偏差）',
    async () => {
      const fetchImpl = routingFetch((req) =>
        sseResponse([toSse(reId(snapshotEnvelopes, req.id))]));
      const { provider, spies } = newProvider({ fetchImpl });
      await provider.connect();

      await provider.request({ query: 'a', sessionKey: SESSION });
      expect(spies.onComplete).toHaveBeenCalledTimes(1);
      provider.abort();                        // 无在途：onComplete 不二发
      expect(spies.onComplete).toHaveBeenCalledTimes(1);
      await provider.request({ query: 'b', sessionKey: SESSION });
      expect(spies.onComplete).toHaveBeenCalledTimes(2);
    });

  it('disconnect 中断流：disconnected 广播一次；在途 request 静默 resolve；不给 onComplete',
    async () => {
      let deferred!: DeferredSse;
      const fetchImpl = routingFetch((_req, call) => {
        deferred = deferredSse((call.init as any).signal as AbortSignal);
        return deferred.response;
      });
      const { provider, spies } = newProvider({ fetchImpl });
      const statuses: any[] = [];
      provider.subscribeToConnectionStatus((e) => statuses.push(e));
      await provider.connect();

      const inFlight = provider.request({ query: 'q', sessionKey: SESSION });
      await until(() => rpcCallOf(fetchImpl) !== undefined);
      const idA = reqIdOf(rpcCallOf(fetchImpl));
      deferred.push(toSse(reId(snapshotEnvelopes.slice(2, 3), idA)));
      await until(() => provider.getParseResult().content === '你');

      provider.disconnect();
      provider.disconnect();                   // 幂等：不重复广播
      await inFlight;
      expect(provider.isConnected).toBe(false);
      // v3 transport.onClose 对位（:108-117）：断开发 disconnected 状态，不发 onComplete
      expect(spies.onComplete).not.toHaveBeenCalled();
      expect(statuses.map((s) => s.status))
        .toEqual(['connecting', 'connected', 'disconnected']);
      const dis = statuses.filter((s) => s.status === 'disconnected');
      expect(dis).toEqual([{ status: 'disconnected', retryCount: 0 }]);
    });

  it('迟帧栅栏：旧迭代器逃逸帧不进 parser——新代内容不被旧 run 串台', async () => {
    const staleSse: DeferredSse[] = [];
    const fetchImpl = routingFetch((req) => {
      if (staleSse.length === 0) {
        // 旧代流：不接 abort 线——模拟"abort 后旧迭代器仍有交付中的帧"逃逸窗口
        const d = deferredSse(null);
        staleSse.push(d);
        return d.response;
      }
      return sseResponse([toSse(reId(snapshotEnvelopes, req.id))]);
    });
    const { provider, spies } = newProvider({ fetchImpl });
    await provider.connect();

    const runA = provider.request({ query: 'A', sessionKey: SESSION });
    await until(() => rpcCallOf(fetchImpl) !== undefined);
    const idA = reqIdOf(rpcCallOf(fetchImpl));
    await sleep();                              // A 循环就位（阻塞在 read）

    provider.abort();                           // A 让位：onComplete([]) 第 1 次播报
    const runB = provider.request({ query: 'B', sessionKey: SESSION });
    await runB;                                 // 新代完整快照：parser 终态 onComplete 第 2 次
    expect(provider.getParseResult().content).toBe('你好！');

    // A 的逃逸帧（abort 之后才从 wire 到达）：id 关联核对可过，但代不符 → 栅栏拦截
    staleSse[0].push(toSse(reId(snapshotEnvelopes.slice(2, 3), idA)));  // 你 delta
    await runA;                                 // A 静默 resolve，帧未触 parser
    expect(provider.getParseResult().content).toBe('你好！');  // 无 '你你' 串写
    expect(spies.onComplete).toHaveBeenCalledTimes(2);
    expect(spies.onComplete.mock.calls[0][0]).toEqual([]);
    expect((spies.onComplete.mock.calls[1][0] as any[])[0])
      .toMatchObject({ content: '你好！' });
    expect(spies.onError).not.toHaveBeenCalled();
  });
});

// ─── B11 断流恢复：ReplayEvents 自动重放 + 预算 + GetTask 分诊 ──────────

describe('B11 request() 断流恢复', () => {
  it('mid-stream 断流（known taskId）：按已消费游标 ReplayEvents 自动重放；恢复事件同管线、终态一次 onComplete', async () => {
    const rpcMethods: string[] = [];
    let main!: ReturnType<typeof breakableSse>;
    const fetchImpl = routingFetch((req) => {
      rpcMethods.push(req.method);
      if (req.method === 'ReplayEvents') {
        // backlog 的下半截 + 终态（游标 seq3 已消费，重放自 seq4 起）
        return sseResponse([toSse([artifactEnvelope(req.id, 4, '好'),
          terminalEnvelope(req.id, 5, 'TASK_STATE_COMPLETED')])]);
      }
      main = breakableSse();
      return main.response;
    });
    const { provider, spies } = newProvider({ fetchImpl });
    await provider.connect();

    const pending = provider.request({ query: 'q', sessionKey: SESSION });
    await until(() => rpcMethods.length >= 1);
    main.push(toSse(reId(snapshotEnvelopes.slice(0, 3),
      reqIdOf(rpcCallOf(fetchImpl)))));          // task + WORKING + '你'（seq3）
    await until(() => provider.getParseResult().content === '你');
    main.breakStream();                          // typed transport 断流（reset）

    await pending;                               // 恢复轮接管 → 正常完成路径
    // replay 调用参数钉精确：known taskId + 已消费游标（B10 锚位）透传
    const bodyOf = (call: CapturedCall) => JSON.parse(String(call.init.body));
    expect(bodyOf(rpcCallOf(fetchImpl, 0)).method).toBe('SendStreamingMessage');
    const replayBody = bodyOf(rpcCallOf(fetchImpl, 1));
    expect(replayBody).toMatchObject({ method: 'ReplayEvents' });
    expect(replayBody.params).toEqual({ taskId: SNAPSHOT_TASK_ID, sinceSeq: 3 });
    // 恢复事件走同管线：全文拼接续接（'你'+'好'），终态 onComplete 恰一次
    expect(spies.onComplete).toHaveBeenCalledTimes(1);
    expect((spies.onComplete.mock.calls[0][0] as any[])[0])
      .toMatchObject({ content: '你好', status: 'done' });
    expect(spies.onError).not.toHaveBeenCalled();
    expect(provider.getParseResult().content).toBe('你好');
    expect(provider.lastAppliedSeq).toBe(5);      // 游标推进到终态
  });

  it('恢复期 abort（generation 栅栏）：runner 取消、恢复流解绑；parser 面 freeze；'
    + 'B12 起：恢复中 run 也发 wire CancelTask + 挂禁→按结局核销', async () => {
    const rpcMethods: string[] = [];
    const registry = createSendBanRegistry();
    const outcomes: CancelOutcome[] = [];
    let main!: ReturnType<typeof breakableSse>;
    const fetchImpl = routingFetch((req, call) => {
      rpcMethods.push(req.method);
      if (req.method === 'ReplayEvents') {
        // 恢复流挂死（不 push 不 close）：恢复期阻塞靶
        return deferredSse((call.init as any).signal as AbortSignal).response;
      }
      if (req.method === 'CancelTask') {
        // 引擎 cancel 落地即答（成功面延迟无必要——本测试垄断结局核销断言）
        return jsonResponse(cancelOk(req.id, 'confirmed'));
      }
      main = breakableSse();
      return main.response;
    });
    const { provider, spies } = newProvider({ fetchImpl, sendBan: registry });
    provider.onCancelOutcome = (o) => outcomes.push(o);
    await provider.connect();

    const pending = provider.request({ query: 'q', sessionKey: SESSION });
    await until(() => rpcMethods.length >= 1);
    main.push(toSse(reId(snapshotEnvelopes.slice(0, 3),
      reqIdOf(rpcCallOf(fetchImpl)))));
    await until(() => provider.getParseResult().content === '你');
    main.breakStream();
    await until(() => rpcMethods.includes('ReplayEvents'));   // 恢复已进入并阻塞

    // dispatch 即挂禁（乐观位）——恢复期 in-flight run 的取消面已开
    provider.abort();
    await until(() => registry.isBanned(A2A_URLS.rpcUrl, SESSION));   // §3.1 乐观挂禁
    await pending;                                            // 静默 resolve
    // abort observable（v3 钉板）：onComplete([]) 一次、无 onError
    expect(spies.onComplete).toHaveBeenCalledTimes(1);
    expect(spies.onComplete).toHaveBeenCalledWith([]);
    expect(spies.onError).not.toHaveBeenCalled();
    // parser 面 freeze 在断流前内容；无恢复期写入
    expect(provider.getParseResult().content).toBe('你');
    expect(provider.lastAppliedSeq).toBe(3);
    // B12：恢复中的 run 也发 CancelTask（known-taskId 在途面）；
    // engineAbort=confirmed 落地 → 自动解禁
    await until(() => !registry.isBanned(A2A_URLS.rpcUrl, SESSION));
    expect(rpcMethods).toEqual(['SendStreamingMessage', 'ReplayEvents', 'CancelTask']);
    expect(outcomes).toEqual([{ kind: 'cancelled-clean', engineAbort: 'confirmed' }]);
  });

  it('replay 拒绝（cache_limit）+ GetTask COMPLETED：结果取回（synthetic final）+ 标不完整', async () => {
    let main!: ReturnType<typeof breakableSse>;
    const latestText = '你好（引擎取回的截断稿）';
    const fetchImpl = routingFetch((req) => {
      if (req.method === 'ReplayEvents') {
        // 引擎拒绝走 200 + JSON error 信封（streamRpc 在流外分类为 typed rpc）
        return new Response(JSON.stringify({
          jsonrpc: '2.0', id: req.id,
          error: { code: -32000, message: 'cache evicted', data: { reason: 'cache_limit' } },
        }), { status: 200, headers: { 'content-type': 'application/json' } });
      }
      if (req.method === 'GetTask') {
        return new Response(JSON.stringify({
          jsonrpc: '2.0', id: req.id,
          result: { task: { id: SNAPSHOT_TASK_ID, status: { state: 'TASK_STATE_COMPLETED' },
            artifacts: [{ parts: [{ text: latestText }] }] } },
        }), { status: 200, headers: { 'content-type': 'application/json' } });
      }
      main = breakableSse();
      return main.response;
    });
    const { provider, spies } = newProvider({ fetchImpl });
    await provider.connect();

    const pending = provider.request({ query: 'q', sessionKey: SESSION });
    await until(() => rpcCallOf(fetchImpl) !== undefined);
    main.push(toSse(reId(snapshotEnvelopes.slice(0, 3),
      reqIdOf(rpcCallOf(fetchImpl)))));
    await until(() => provider.getParseResult().content === '你');
    main.breakStream();

    await expect(pending).rejects.toMatchObject({
      message: RECOVERY_INCOMPLETE_MESSAGE,
      failure: { kind: 'rpc', reason: 'recovery_incomplete' },
    });
    // 结果可取回：GetTask latestText → 合成 final 帧 → parser onComplete（截断稿上钉）
    expect(spies.onComplete).toHaveBeenCalledTimes(1);
    expect((spies.onComplete.mock.calls[0][0] as any[])[0])
      .toMatchObject({ content: latestText, status: 'done' });
    // 标"不完整"：恢复信号的 onError 面
    expect(spies.onError).toHaveBeenCalledTimes(1);
    expect(spies.onError.mock.calls[0][0].message).toBe(RECOVERY_INCOMPLETE_MESSAGE);
  });

  it('预算耗尽：RECOVERY_STOPPED 信号 + 手工恢复开启新预算只读轮；终局后 manualRecover=null', async () => {
    let main!: ReturnType<typeof breakableSse>;
    let replaySucceeds = false;
    let brokenReplays = 0;
    const fetchImpl = routingFetch((req) => {
      if (req.method === 'ReplayEvents') {
        if (!replaySucceeds) {
          brokenReplays += 1;
          return sseResponse([]);   // 零事件关流：typed sse_eof_no_final（可重试面）
        }
        return sseResponse([toSse([artifactEnvelope(req.id, 4, '好'),
          terminalEnvelope(req.id, 5, 'TASK_STATE_COMPLETED')])]);
      }
      main = breakableSse();
      return main.response;
    });
    const recovery = { maxAttempts: 2, backoffCapsMs: [1], perCallTimeoutMs: 50,
      totalBudgetMs: 10_000 };
    const { provider, spies } = newProvider({ fetchImpl, recovery });
    await provider.connect();

    // 无事可恢复：还没有任何 run
    expect(await provider.manualRecover()).toBeNull();

    const pending = provider.request({ query: 'q', sessionKey: SESSION });
    await until(() => rpcCallOf(fetchImpl) !== undefined);
    main.push(toSse(reId(snapshotEnvelopes.slice(0, 3),
      reqIdOf(rpcCallOf(fetchImpl)))));
    await until(() => provider.getParseResult().content === '你');
    main.breakStream();

    await expect(pending).rejects.toMatchObject({
      message: RECOVERY_STOPPED_MESSAGE,
      failure: { kind: 'rpc', reason: 'recovery_stopped' },
    });
    expect(spies.onError).toHaveBeenCalledTimes(1);
    expect(spies.onError.mock.calls[0][0].message).toBe(RECOVERY_STOPPED_MESSAGE);
    expect(brokenReplays).toBe(2);              // 小预算覆盖：两次重放均破 → 预算耗尽

    // 手工轮（spec：自成新预算）→ 只读重放这次成功：parser 补完、onComplete 一次
    replaySucceeds = true;
    const outcome = await provider.manualRecover();
    expect(outcome).toEqual({ outcome: 'terminal', state: 'TASK_STATE_COMPLETED' });
    expect(provider.getParseResult().content).toBe('你好');
    expect(spies.onComplete).toHaveBeenCalledTimes(1);
    expect((spies.onComplete.mock.calls[0][0] as any[])[0])
      .toMatchObject({ content: '你好', status: 'done' });

    // 终局已入账：无事可恢复
    expect(await provider.manualRecover()).toBeNull();
  });
});

// ─── B12 abort 四态机：CancelTask 控制面 + context 级 send-ban ─────────────

describe('B12 abort() — CancelTask wire 面 + 四态禁令核销（§4.3/§3.1）', () => {
  it('(a) mid-stream abort with taskId：CancelTask 体钉精确；confirmed clean →'
    + ' 唯自动解禁、onComplete([]) 恰一次、request 静默收尾', async () => {
    const registry = createSendBanRegistry();
    const outcomes: CancelOutcome[] = [];
    let deferred!: DeferredSse;
    let cancelResp!: ReturnType<typeof deferredRpcResponse>;
    let cancelReqId = -1;
    const fetchImpl = routingFetch((req, call) => {
      if (req.method === 'CancelTask') {
        cancelReqId = req.id;
        cancelResp = deferredRpcResponse();
        return cancelResp.response;
      }
      deferred = deferredSse((call.init as any).signal as AbortSignal);
      return deferred.response;
    });
    const { provider, spies } = newProvider({ fetchImpl, sendBan: registry });
    provider.onCancelOutcome = (o) => outcomes.push(o);
    await provider.connect();

    const inFlight = provider.request({ query: 'q', sessionKey: SESSION });
    await until(() => rpcCallOf(fetchImpl) !== undefined);
    const idA = reqIdOf(rpcCallOf(fetchImpl));
    deferred.push(toSse(reId(snapshotEnvelopes.slice(0, 3), idA)));  // task+'你'：taskId 锚生效
    await until(() => provider.getParseResult().content === '你');

    provider.abort();
    await inFlight;                          // request 静默收尾（无 onError）
    expect(spies.onComplete).toHaveBeenCalledTimes(1);
    expect(spies.onComplete).toHaveBeenCalledWith([]);
    expect(spies.onError).not.toHaveBeenCalled();
    // §3.1 乐观挂禁：dispatch 即可见
    await until(() => registry.isBanned(A2A_URLS.rpcUrl, SESSION));
    const cancelCall = fetchImpl.calls
      .filter((c) => c.url === A2A_URLS.rpcUrl)
      .map((c) => JSON.parse(String(c.init.body)))
      .find((b) => b.method === 'CancelTask');
    expect(cancelCall).toMatchObject({
      jsonrpc: '2.0', method: 'CancelTask', params: { taskId: SNAPSHOT_TASK_ID },
    });
    // 引擎答：CANCELED + engineAbort=confirmed → cancelled-clean → 唯自动解禁
    cancelResp.respond(cancelOk(cancelReqId, 'confirmed'));
    await until(() => !registry.isBanned(A2A_URLS.rpcUrl, SESSION));
    expect(outcomes).toEqual([{ kind: 'cancelled-clean', engineAbort: 'confirmed' }]);
    expect(CANCEL_UI_TEXT['cancelled-clean']).toBe('已取消');   // UI 文案单源
  });

  it('(b) unconfirmed：CANCELED + engineAbort 非 confirmed → 四态 unconfirmed、禁令保持', async () => {
    const registry = createSendBanRegistry();
    const outcomes: CancelOutcome[] = [];
    let deferred!: DeferredSse;
    let cancelResp!: ReturnType<typeof deferredRpcResponse>;
    let cancelReqId = -1;
    const fetchImpl = routingFetch((req, call) => {
      if (req.method === 'CancelTask') {
        cancelReqId = req.id;
        cancelResp = deferredRpcResponse();
        return cancelResp.response;
      }
      deferred = deferredSse((call.init as any).signal as AbortSignal);
      return deferred.response;
    });
    const { provider, spies } = newProvider({ fetchImpl, sendBan: registry });
    provider.onCancelOutcome = (o) => outcomes.push(o);
    await provider.connect();

    const inFlight = provider.request({ query: 'q', sessionKey: SESSION });
    await until(() => rpcCallOf(fetchImpl) !== undefined);
    deferred.push(toSse(reId(snapshotEnvelopes.slice(0, 3), reqIdOf(rpcCallOf(fetchImpl)))));
    await until(() => provider.getParseResult().content === '你');
    provider.abort();
    await inFlight;
    // engineAbort=refused：传输取消成功但引擎未确认停——unconfirmed
    cancelResp.respond(cancelOk(cancelReqId, 'refused'));
    await until(() => outcomes.length === 1);
    expect(outcomes).toEqual([{ kind: 'cancelled-unconfirmed', engineAbort: 'refused' }]);
    expect(registry.isBanned(A2A_URLS.rpcUrl, SESSION)).toBe(true);   // 禁令保持
    // parser 面 freeze：abort 后内容不被 CANCELED 终帧追写
    expect(provider.getParseResult().content).toBe('你');
    expect(spies.onError).not.toHaveBeenCalled();
    expect(CANCEL_UI_TEXT['cancelled-unconfirmed']).toBe('已取消，底层执行停止未确认');
  });

  it('(c) terminal-then-cancel + mid-cancel teardown：流终态 COMPLETED 先到（栅栏拦截）；'
    + '迟到 CancelTask 答 task_terminal(COMPLETED) → rejected 行解禁；断连后核销仍落（§3.1 迟到法则）',
    async () => {
      const registry = createSendBanRegistry();
      const outcomes: CancelOutcome[] = [];
      let stream!: DeferredSse;
      let cancelResp!: ReturnType<typeof deferredRpcResponse>;
      let cancelReqId = -1;
      const fetchImpl = routingFetch((req) => {
        if (req.method === 'CancelTask') {
          cancelReqId = req.id;
          cancelResp = deferredRpcResponse();
          return cancelResp.response;
        }
        stream = deferredSse(null);   // 不接 abort 线：模拟 abort 后旧迭代器仍有交付窗口
        return stream.response;
      });
      const { provider, spies } = newProvider({ fetchImpl, sendBan: registry });
      provider.onCancelOutcome = (o) => outcomes.push(o);
      await provider.connect();

      const inFlight = provider.request({ query: 'q', sessionKey: SESSION });
      await until(() => rpcCallOf(fetchImpl) !== undefined);
      const idA = reqIdOf(rpcCallOf(fetchImpl));
      stream.push(toSse(reId(snapshotEnvelopes.slice(0, 3), idA)));
      await until(() => provider.getParseResult().content === '你');

      provider.abort();                 // onComplete([]) 1 + CancelTask 派发 + 乐观挂禁
      provider.disconnect();            // §3.1 断连善后 ≠ 清禁令：核销余程交迟到响应
      // 逃逸窗口：abort 之后流终态 COMPLETED 才到——代际不符不入 parser、
      // 也不触非 cancel 终态解禁钩（禁令仍在）
      stream.push(toSse(reId([terminalEnvelope(idA, 5, 'TASK_STATE_COMPLETED')], idA)));
      await inFlight;                   // request 静默收尾：无二次 onComplete、无 error
      expect(spies.onComplete).toHaveBeenCalledTimes(1);
      expect(spies.onComplete).toHaveBeenCalledWith([]);
      expect(spies.onError).not.toHaveBeenCalled();
      expect(provider.getParseResult().content).toBe('你');
      expect(registry.isBanned(A2A_URLS.rpcUrl, SESSION)).toBe(true);   // 迟到帧未动禁令

      // 引擎迟到作答：cancel 抵达时 run 已 COMPLETED → task_terminal 拒绝（状态已知）
      cancelResp.respond(jrpcErr(cancelReqId, {
        code: -32000,
        message: 'task already terminal (TASK_STATE_COMPLETED)',
        data: { reason: 'task_terminal', state: 'TASK_STATE_COMPLETED', already_terminal: true },
      }));
      await until(() => outcomes.length === 1);
      expect(outcomes[0])
        .toMatchObject({ kind: 'cancel-rejected', terminalState: 'TASK_STATE_COMPLETED' });
      expect(registry.isBanned(A2A_URLS.rpcUrl, SESSION)).toBe(false);   // rejected 行：解禁
      expect(CANCEL_UI_TEXT['cancel-rejected']).toBe('取消未成功，保留已知任务状态');
    });

  it('(d) cancel-unknown：CancelTask 响应丢失（控制面流断）→ 四态 unknown、'
    + '禁令保持（无停止证据不释放）', async () => {
    const registry = createSendBanRegistry();
    const outcomes: CancelOutcome[] = [];
    const rpcMethods: string[] = [];
    let deferred!: DeferredSse;
    const fetchImpl = routingFetch((req, call) => {
      rpcMethods.push(req.method);
      if (req.method === 'CancelTask') {
        throw new TypeError('reset');          // 控制面请求断——取消结局不可知
      }
      if (req.method === 'GetTask') {
        // F8 探测的确定性应答：任务仍活跃（WORKING）——无新证据 → 不解禁
        return jsonResponse(jrpcOk(req.id, {
          task: { id: (req.params as { taskId?: string }).taskId,
            status: { state: 'TASK_STATE_WORKING' }, metadata: {} },
        }));
      }
      deferred = deferredSse((call.init as any).signal as AbortSignal);
      return deferred.response;
    });
    const { provider, spies } = newProvider({ fetchImpl, sendBan: registry });
    provider.onCancelOutcome = (o) => outcomes.push(o);
    await provider.connect();

    const inFlight = provider.request({ query: 'q', sessionKey: SESSION });
    await until(() => rpcCallOf(fetchImpl) !== undefined);
    deferred.push(toSse(reId(snapshotEnvelopes.slice(0, 3), reqIdOf(rpcCallOf(fetchImpl)))));
    await until(() => provider.getParseResult().content === '你');
    provider.abort();
    await inFlight;
    expect(spies.onComplete).toHaveBeenCalledTimes(1);

    await until(() => outcomes.length === 1);
    expect(outcomes).toEqual([{ kind: 'cancel-unknown' }]);
    // §3.1 第 3 行：unknown 结局禁令续挂——放弃恢复/重派 CancelTask 均不释放
    expect(registry.isBanned(A2A_URLS.rpcUrl, SESSION)).toBe(true);
    // 重复 CancelTask 不是 workaround：后续无第二次 CancelTask POST
    const cancelCalls = fetchImpl.calls
      .filter((c) => c.url === A2A_URLS.rpcUrl)
      .map((c) => JSON.parse(String(c.init.body)))
      .filter((b) => b.method === 'CancelTask');
    expect(cancelCalls).toHaveLength(1);
    // F8：row 4 文案承诺的 GetTask 探测单发（探得活跃任务 = 无新证据 → 禁令
    // 保持；探测不追加 UI 事件——onCancelOutcome 仍只一次 cancel-unknown）
    await until(() => rpcMethods.includes('GetTask'));
    expect(rpcMethods.filter((m) => m === 'GetTask')).toHaveLength(1);
    expect(outcomes).toHaveLength(1);
    expect(registry.isBanned(A2A_URLS.rpcUrl, SESSION)).toBe(true);
    expect(spies.onError).not.toHaveBeenCalled();
    expect(CANCEL_UI_TEXT['cancel-unknown']).toBe('取消结果未知，尝试查询任务状态');
  });

  it('(e) 首帧前 abort：请求已出站、首 Task 未收 —— 服务端可能已 dispatch（router.py'
    + ' open_run 先于订阅回包），无 taskId 无法 wire 取消 → task-outcome-unknown（非 clean）',
    async () => {
      const registry = createSendBanRegistry();
      const outcomes: CancelOutcome[] = [];
      let deferred!: DeferredSse;
      const fetchImpl = routingFetch((_req, call) => {
        deferred = deferredSse((call.init as any).signal as AbortSignal);
        return deferred.response;
      });
      const { provider, spies } = newProvider({ fetchImpl, sendBan: registry });
      provider.onCancelOutcome = (o) => outcomes.push(o);
      await provider.connect();

      const inFlight = provider.request({ query: 'q', sessionKey: SESSION });
      await until(() => rpcCallOf(fetchImpl) !== undefined);   // POST 已出站、零事件（无 Task 锚）
      provider.abort();
      await inFlight;

      expect(spies.onComplete).toHaveBeenCalledTimes(1);
      expect(spies.onComplete).toHaveBeenCalledWith([]);
      expect(spies.onError).not.toHaveBeenCalled();
      // 零 wire 取消面：唯一 POST 就是 SendStreamingMessage（无 CancelTask——
      // 无 taskId，CancelTask 无从构造）
      const rpcCalls = fetchImpl.calls.filter((c) => c.url === A2A_URLS.rpcUrl);
      expect(rpcCalls).toHaveLength(1);
      expect(JSON.parse(String(rpcCalls[0].init.body)).method).toBe('SendStreamingMessage');
      // §3#2：结果未知不挂禁令（无核销锚——GetTask 也要 taskId；挂禁即死锁。
      // 用户显式重发 = 新幂等键，D14 合规路径）
      expect(registry.isBanned(A2A_URLS.rpcUrl, SESSION)).toBe(false);
      expect(outcomes).toEqual([{ kind: 'task-outcome-unknown' }]);
      expect(CANCEL_UI_TEXT['task-outcome-unknown'])
        .toBe('已停止，任务结果未知，服务端可能仍在执行');
    });

  it('(e2) 出站前 abort（iam 签发窗口）：确定未发送——服务端零 run，cancelled-clean 成立',
    async () => {
      const registry = createSendBanRegistry();
      const outcomes: CancelOutcome[] = [];
      // 真 fetch 语义：signal 已 aborted → 请求拒绝（并未出站）；未 aborted 才回流
      const fetchImpl = routingFetch((_req, call) => {
        const signal = (call.init as any).signal as AbortSignal;
        if (signal.aborted) throw new TypeError('aborted');
        return deferredSse(signal).response;
      });
      // iam 签发窗口受控：request() 在 resolveIamToken 处停摆，fetch 尚未出站
      let releaseToken!: (v: string) => void;
      const iamTokenGetter = () => new Promise<string>((r) => { releaseToken = r; });
      const { provider, spies } = newProvider({ fetchImpl, sendBan: registry, iamTokenGetter });
      provider.onCancelOutcome = (o) => outcomes.push(o);
      await provider.connect();

      const inFlight = provider.request({ query: 'q', sessionKey: SESSION });
      // request 协程推进到 iam 窗口（此时 isRequesting=true、abort 生效）后停摆
      await until(() => releaseToken !== undefined);
      provider.abort();                     // 停在 iam 窗口：RPC 未出站
      releaseToken('tok');

      // 出站即拒（abort signal）→ streamRpc 判定主动取消静默收尾（rpc.ts
      // classifyFailure 前的 aborted 检查）——不 onError、无 typed 拒绝
      await inFlight;
      expect(spies.onComplete).toHaveBeenCalledTimes(1);
      expect(spies.onComplete).toHaveBeenCalledWith([]);
      expect(spies.onError).not.toHaveBeenCalled();
      const rpcCalls = fetchImpl.calls.filter((c) => c.url === A2A_URLS.rpcUrl);
      expect(rpcCalls).toHaveLength(1);
      expect((rpcCalls[0].init as any).signal.aborted).toBe(true);
      expect(registry.isBanned(A2A_URLS.rpcUrl, SESSION)).toBe(false);
      expect(outcomes).toEqual([{ kind: 'cancelled-clean' }]);   // 真未发送：零 wire 面
    });

  it('§3.1 禁令生命周期：context 级横跨 provider 实例；disconnect 不清禁令；'
    + '同 context 正常完成（COMPLETED 非 cancel 终态）解禁', async () => {
    const registry = createSendBanRegistry();
    // 模拟 unconfirmed 取消遗留的挂禁（前一个 provider 已废弃）
    registry.begin(A2A_URLS.rpcUrl, SESSION);

    // 另一个 provider 实例同 context 观察：禁令状态与实例无关（context 级键）
    const implProbe = routingFetch(() => { throw new Error('no rpc expected'); });
    newProvider({ fetchImpl: implProbe, sendBan: registry });
    expect(registry.isBanned(A2A_URLS.rpcUrl, SESSION)).toBe(true);

    // 断连善后不清禁令
    const implA = routingFetch((req) =>
      sseResponse([toSse(reId(snapshotEnvelopes, req.id))]));
    const a = newProvider({ fetchImpl: implA, sendBan: registry });
    await a.provider.connect();
    a.provider.disconnect();
    expect(registry.isBanned(A2A_URLS.rpcUrl, SESSION)).toBe(true);

    // 同 context 的新请求正常完成（COMPLETED 终态）→ 非 cancel 终态解禁流
    const implB = routingFetch((req) =>
      sseResponse([toSse(reId(snapshotEnvelopes, req.id))]));
    const b = newProvider({ fetchImpl: implB, sendBan: registry });
    await b.provider.request({ query: 'q', sessionKey: SESSION });
    expect(b.spies.onComplete).toHaveBeenCalledTimes(1);
    expect(registry.isBanned(A2A_URLS.rpcUrl, SESSION)).toBe(false);
  });
});

describe('subscribeToParseResult / getParseResult（对位 v3 :380-447）', () => {
  it('订阅收累计快照增量，退订后不再收；parser 未就绪 getParseResult 返回空快照', async () => {
    // parser 就绪前：空快照（不炸、不吊）——工厂给永不决议的 pending，
    // "未就绪"语义比原裸构造（当场开始真加载）更精确
    const early = new A2AProvider({
      url: 'wss://x/ws', a2aUrls: A2A_URLS,
      parserFactory: (() => { /* 永不决议：钉"未就绪"语义 */ }) as any,
    });
    expect(early.getParseResult()).toEqual({
      blocks: [], content: '', done: false, toolCalls: [], isApproval: false,
    });

    const fetchImpl = routingFetch((req) =>
      sseResponse([toSse(reId(snapshotEnvelopes, req.id))]));
    const { provider } = newProvider({ fetchImpl });
    await provider.connect();

    const seen: any[] = [];
    const unsub = provider.subscribeToParseResult((r) => seen.push(r));
    await provider.request({ query: 'q', sessionKey: SESSION });
    expect(seen.length).toBeGreaterThan(2);      // 多帧流式
    expect(seen[0].content).toBe('你');          // 累计快照（钉板 #3）
    expect(seen.at(-1)!.content).toBe('你好！');

    unsub();
    const frozen = seen.length;
    await provider.request({ query: 'q2', sessionKey: SESSION });
    expect(seen.length).toBe(frozen);           // 退订生效
  });
});
// ─── B14 M2 终审钉板：§3.4 共 seq 终态 / §3.1 not_found 禁令 / F4 恢复硬失败 ──

describe('B14 M2 终审修复钉板（F1/F1b 共-seq、F2 not_found、F4 恢复硬失败、F8 探测释放）', () => {
  it('§6.2 共-seq：final marker 与已消费的前序内容事件同 seq——内容已应用且 '
    + 'final 也应用（onComplete 恰一次）', async () => {
    const fetchImpl = routingFetch((req) => sseResponse([toSse(reId([
      snapshotEnvelopes[0],                       // task（taskId 锚）
      artifactEnvelope(req.id, 1, '你'),           // 内容事件@1（先消费）
      stateChangeEnvelope(req.id, 2, 'TASK_STATE_COMPLETED'),  // state-change@2 → 游标 2
      terminalEnvelope(req.id, 2, 'TASK_STATE_COMPLETED'),     // marker@2：与游标撞 seq
    ], req.id))]));
    const { provider, spies } = newProvider({ fetchImpl });
    await provider.connect();

    await provider.request({ query: 'q', sessionKey: SESSION });

    // 内容已应用（不被共 seq 语义吞）：正文上钉 parse 面
    expect(provider.getParseResult().content).toBe('你');
    // final 也应用：onComplete 恰一次、done、全文完整（spec §3.4 豁免面）
    expect(spies.onComplete).toHaveBeenCalledTimes(1);
    expect((spies.onComplete.mock.calls[0][0] as any[])[0])
      .toMatchObject({ content: '你', status: 'done' });
    expect(spies.onError).not.toHaveBeenCalled();
    expect(provider.lastAppliedSeq).toBe(2);
  });

  it('恢复面共-seq end-to-end（F1）：主回路已消费 state-change@N+1（游标=6、未终账）'
    + '时断流 → replay 只回同 seq 的 marker——仍完成恰一次，UI 不挂死', async () => {
    // 编排对齐 kernel 真实行为：complete_run 的 state-change 经 _publish 后只存于
    // events buffer（marker 不入 buffer）；subscribe(since_seq=6) 对终态 run 的
    // replay = 空 backlog + 现造 marker@r.last_seq=6——与已消费游标同 seq。
    const rpcMethods: string[] = [];
    let main!: ReturnType<typeof breakableSse>;
    const fetchImpl = routingFetch((req) => {
      rpcMethods.push(req.method);
      if (req.method === 'ReplayEvents') {
        return sseResponse([toSse([terminalEnvelope(req.id, 6, 'TASK_STATE_COMPLETED')])]);
      }
      main = breakableSse();
      return main.response;
    });
    const { provider, spies } = newProvider({ fetchImpl });
    await provider.connect();

    const pending = provider.request({ query: 'q', sessionKey: SESSION });
    await until(() => rpcCallOf(fetchImpl) !== undefined);
    main.push(toSse(reId(snapshotEnvelopes.slice(0, 6),   // task..final + state-change@6
      reqIdOf(rpcCallOf(fetchImpl)))));
    await until(() => provider.lastAppliedSeq === 6);     // state-change 已消费
    main.breakStream();                                    // marker 未到即断流

    await pending;
    const bodyOf = (call: CapturedCall) => JSON.parse(String(call.init.body));
    expect(bodyOf(rpcCallOf(fetchImpl, 1)).params)
      .toEqual({ taskId: SNAPSHOT_TASK_ID, sinceSeq: 6 });
    // marker@6 在 `<=` 去重面前仍应用——真完成（旧代码此处 marker 被吞：
    // runner 报 terminal 而 parser 永无 final 账，UI 挂死）
    expect(spies.onComplete).toHaveBeenCalledTimes(1);
    expect((spies.onComplete.mock.calls[0][0] as any[])[0])
      .toMatchObject({ content: '你好！', status: 'done' });
    expect(spies.onError).not.toHaveBeenCalled();
    expect(provider.lastAppliedSeq).toBe(6);
  });

  it('F2/§3.1：CancelTask 答 not_found_or_expired → cancel-rejected-unknown 行——'
    + 'UI 文案同 rejected 但禁令保持（查无任务不构成底层已停证明）', async () => {
    const registry = createSendBanRegistry();
    const outcomes: CancelOutcome[] = [];
    let deferred!: DeferredSse;
    let cancelResp!: ReturnType<typeof deferredRpcResponse>;
    let cancelReqId = -1;
    const fetchImpl = routingFetch((req, call) => {
      if (req.method === 'CancelTask') {
        cancelReqId = req.id;
        cancelResp = deferredRpcResponse();
        return cancelResp.response;
      }
      deferred = deferredSse((call.init as any).signal as AbortSignal);
      return deferred.response;
    });
    const { provider } = newProvider({ fetchImpl, sendBan: registry });
    provider.onCancelOutcome = (o) => outcomes.push(o);
    await provider.connect();

    const inFlight = provider.request({ query: 'q', sessionKey: SESSION });
    await until(() => rpcCallOf(fetchImpl) !== undefined);
    deferred.push(toSse(reId(snapshotEnvelopes.slice(0, 3), reqIdOf(rpcCallOf(fetchImpl)))));
    await until(() => provider.getParseResult().content === '你');
    provider.abort();
    await inFlight;                          // 乐观挂禁已开
    expect(registry.isBanned(A2A_URLS.rpcUrl, SESSION)).toBe(true);

    cancelResp.respond(jrpcErr(cancelReqId, {
      code: -32000, message: 'task not found or expired',
      data: { reason: 'not_found_or_expired' },
    }));
    await until(() => outcomes.length === 1);
    expect(outcomes).toEqual([{ kind: 'cancel-rejected-unknown' }]);
    // F2 REPO 钉板（旧行为=归 rejected 行解禁）：查无任务 ≠ 底层已停——禁令保持
    expect(registry.isBanned(A2A_URLS.rpcUrl, SESSION)).toBe(true);
    expect(CANCEL_UI_TEXT['cancel-rejected-unknown'])
      .toBe('取消未成功，保留已知任务状态');   // 与 rejected 同文案（用户面无差）
  });

  it('F4：恢复期 auth 硬拒绝 → 锁面播报恰一次 + 预算即止（1 attempt << 5）+ '
    + 'RECOVERY_STOPPED 信号语携带原始 auth failure（cause）', async () => {
    const rpcMethods: string[] = [];
    let main!: ReturnType<typeof breakableSse>;
    const fetchImpl = routingFetch((req) => {
      rpcMethods.push(req.method);
      if (req.method === 'ReplayEvents') {
        // 引擎鉴权门口径：200 + JSON-RPC error 信封（无 HTTP 401/403）
        return new Response(JSON.stringify({
          jsonrpc: '2.0', id: req.id,
          error: { code: -32000, message: 'Missing x-iam-token',
            data: { reason: 'auth_required' } },
        }), { status: 200, headers: { 'content-type': 'application/json' } });
      }
      main = breakableSse();
      return main.response;
    });
    const { provider, spies } = newProvider({ fetchImpl });
    const statuses: any[] = [];
    provider.subscribeToConnectionStatus((e) => statuses.push(e));
    await provider.connect();

    const pending = provider.request({ query: 'q', sessionKey: SESSION });
    await until(() => rpcCallOf(fetchImpl) !== undefined);
    main.push(toSse(reId(snapshotEnvelopes.slice(0, 3), reqIdOf(rpcCallOf(fetchImpl)))));
    await until(() => provider.getParseResult().content === '你');
    main.breakStream();                      // known taskId → 恢复轮接管

    await expect(pending).rejects.toMatchObject({
      message: RECOVERY_STOPPED_MESSAGE,
      failure: {
        kind: 'rpc', reason: 'recovery_stopped',
        cause: { kind: 'auth' },             // 原始失败附录随行
      },
    });
    // 锁面（B13 markLocked 的唯一来源）：status:'error' 播报恰一次——恢复分支
    // 此前缺 notifyHardFailure 面（B14 F4 REPO），auth 拒绝烧满预算还锁不上
    const errorStatuses = statuses.filter((s) => s.status === 'error');
    expect(errorStatuses).toHaveLength(1);
    expect(errorStatuses[0].error.failure).toMatchObject({ kind: 'auth' });
    // 预算即止：首个 auth 拒绝即 fatal 早退（attempts 1 << maxAttempts 5）、
    // 不进 GetTask 分诊、不再退避重放
    expect(rpcMethods.filter((m) => m === 'ReplayEvents')).toHaveLength(1);
    expect(rpcMethods).not.toContain('GetTask');
    expect(rpcMethods.filter((m) => m === 'SubscribeToTask')).toHaveLength(0);
    expect(spies.onError).toHaveBeenCalledTimes(1);
    expect(spies.onError.mock.calls[0][0].message).toBe(RECOVERY_STOPPED_MESSAGE);
  });

  it('F8（§4.3 row 4 文案落地）：cancel-unknown 后单发 best-effort GetTask 探测——'
    + '探得 COMPLETED 终态按 releaseOnTerminal 解禁；toast 文案不变', async () => {
    const registry = createSendBanRegistry();
    const outcomes: CancelOutcome[] = [];
    const rpcMethods: string[] = [];
    let deferred!: DeferredSse;
    const fetchImpl = routingFetch((req, call) => {
      rpcMethods.push(req.method);
      if (req.method === 'CancelTask') {
        throw new TypeError('reset');          // 控制面流断 → cancel-unknown
      }
      if (req.method === 'GetTask') {
        return jsonResponse(jrpcOk(req.id, {
          task: { id: (req.params as { taskId?: string }).taskId,
            status: { state: 'TASK_STATE_COMPLETED' }, metadata: {} },
        }));
      }
      deferred = deferredSse((call.init as any).signal as AbortSignal);
      return deferred.response;
    });
    const { provider, spies } = newProvider({ fetchImpl, sendBan: registry });
    provider.onCancelOutcome = (o) => outcomes.push(o);
    await provider.connect();

    const inFlight = provider.request({ query: 'q', sessionKey: SESSION });
    await until(() => rpcCallOf(fetchImpl) !== undefined);
    deferred.push(toSse(reId(snapshotEnvelopes.slice(0, 3), reqIdOf(rpcCallOf(fetchImpl)))));
    await until(() => provider.getParseResult().content === '你');
    provider.abort();
    await inFlight;

    await until(() => outcomes.length === 1);
    expect(outcomes).toEqual([{ kind: 'cancel-unknown' }]);   // toast 文案面不变
    expect(registry.isBanned(A2A_URLS.rpcUrl, SESSION)).toBe(true);
    // 探测单发、taskId 锚正确；探得非 CANCELED 终态 → releaseOnTerminal 解禁
    await until(() => !registry.isBanned(A2A_URLS.rpcUrl, SESSION));
    expect(rpcMethods.filter((m) => m === 'GetTask')).toHaveLength(1);
    const probe = fetchImpl.calls
      .filter((c) => c.url === A2A_URLS.rpcUrl && c.init.body)
      .map((c) => JSON.parse(String(c.init.body)))
      .find((b) => b.method === 'GetTask');
    expect(probe).toMatchObject({
      method: 'GetTask', params: { taskId: SNAPSHOT_TASK_ID },
    });
    // 探测不追加 UI 播报：onCancelOutcome 仍只一次
    expect(outcomes).toHaveLength(1);
    expect(spies.onError).not.toHaveBeenCalled();
  });

  it('F1/replay backlog 双帧共撞：断流时游标=5，backlog 自带 state-change@6'
    + '（先应用、游标→6）+ 同 seq marker@6——marker 须越过刚推出的游标仍应用'
    + '（旧行为=marker 被 `<=` 去重吞掉：runner 报 terminal 而 parser 永无'
    + ' final 账 = 静默终态）', async () => {
    const rpcMethods: string[] = [];
    let main!: ReturnType<typeof breakableSse>;
    const fetchImpl = routingFetch((req) => {
      rpcMethods.push(req.method);
      if (req.method === 'ReplayEvents') {
        // adversarial 编排的两帧都在 backlog 里（不靠主回路预喂 state-change）
        return sseResponse([toSse([
          stateChangeEnvelope(req.id, 6, 'TASK_STATE_COMPLETED'),
          terminalEnvelope(req.id, 6, 'TASK_STATE_COMPLETED'),
        ])]);
      }
      main = breakableSse();
      return main.response;
    });
    const { provider, spies } = newProvider({ fetchImpl });
    await provider.connect();

    const pending = provider.request({ query: 'q', sessionKey: SESSION });
    await until(() => rpcCallOf(fetchImpl) !== undefined);
    main.push(toSse(reId(snapshotEnvelopes.slice(0, 5),   // task..你好！@5（未终态）
      reqIdOf(rpcCallOf(fetchImpl)))));
    await until(() => provider.getParseResult().content === '你好！');
    main.breakStream();

    await pending;
    const bodyOf = (call: CapturedCall) => JSON.parse(String(call.init.body));
    expect(bodyOf(rpcCallOf(fetchImpl, 1)).params)
      .toEqual({ taskId: SNAPSHOT_TASK_ID, sinceSeq: 5 });
    // backlog 双帧：state-change@6 先应用（游标→6），同步撞的 marker@6 仍应用
    // → 完成恰一次、全文完整、无静默终态
    expect(spies.onComplete).toHaveBeenCalledTimes(1);
    expect((spies.onComplete.mock.calls[0][0] as any[])[0])
      .toMatchObject({ content: '你好！', status: 'done' });
    expect(spies.onError).not.toHaveBeenCalled();
    expect(provider.lastAppliedSeq).toBe(6);
  });

  it('F1 对偶栅栏 adversarial 钉板（consumeWireEvent 单元直驱）：真内核全序'
    + ' 先行——marker 与已消费 state-change 共 seq 仍应用恰一次；随后 marker'
    + ' 双投（重发乱序）/低于游标的陈旧 marker@4/陈旧内容事件@2 一律不二发、'
    + '游标不回退（§3.4 豁免后 terminalApplied 栅栏是唯一防线，此处逐帧直钉;'
    + ' 现行 wiring 主回路/replay 轮均在首 final 截流，本钉板锁的是防线本体）',
    async () => {
      const OpenClawParser = await loadOpenClawParser();
      const parser = new (OpenClawParser as any)({
        enableThinking: true, generateId: () => 'adv-marker',
      }) as any;
      parser.setSessionKey(SESSION);
      const onFinal = jest.fn();
      const onErr = jest.fn();
      parser.onComplete = onFinal;
      parser.onError = onErr;
      const { provider } = newProvider({
        fetchImpl: routingFetch(() => { throw new Error('no rpc expected'); }),
      });

      /** 信封 → WireEvent（stream.ts extractMemberEvent 同口径） */
      const wireOf = (env: any): WireEvent => {
        const result = env.result;
        const type = ['task', 'message', 'statusUpdate', 'artifactUpdate']
          .find((k) => Object.prototype.hasOwnProperty.call(result, k))!;
        const member = result[type] as Record<string, unknown>;
        const metadata = (member.metadata ?? {}) as Record<string, unknown>;
        return {
          seq: typeof metadata.seq === 'number' ? metadata.seq : null,
          type, member, metadata,
        };
      };
      const bag: any = {
        parser,
        ctx: { sessionKey: SESSION, taskRunId: SNAPSHOT_TASK_ID },
        state: { fullText: '' },
        outcome: null, runFailure: null, terminalApplied: false,
      };
      const consume = (env: any) =>
        (provider as any).run.consumeWireEvent(wireOf(env), bag) as string;

      // 真内核全序：task、WORKING@2、你@3、好@4、你好！@5、state-change@6、
      // marker@6（与 state-change 共 seq——豁免面：恰一次应用）
      const heads = snapshotEnvelopes.map(consume);
      expect(heads).toEqual([
        'cont', 'cont', 'cont', 'cont', 'cont', 'cont', 'final',
      ]);
      expect(provider.lastAppliedSeq).toBe(6);

      // adversarial 尾列（重发乱序面）：marker@6 双投、陈旧 marker@4（豁免
      // 使其越过 `<=` 去重——栅栏须拦）、陈旧内容事件@2（非 marker：inclusive
      // 去重须照吞）
      const dup = consume(terminalEnvelope(1, 6, 'TASK_STATE_COMPLETED'));
      const staleMarker = consume(terminalEnvelope(1, 4, 'TASK_STATE_COMPLETED'));
      const staleContent = consume(artifactEnvelope(1, 2, '串'));
      expect([dup, staleMarker, staleContent]).toEqual(['cont', 'cont', 'cont']);

      // 完成恰一次（防线失守则 dup/stale 必把第二 final 喂 parser）；无错误
      expect(onFinal).toHaveBeenCalledTimes(1);
      expect(onFinal.mock.calls[0][0]).toMatchObject({ done: true, content: '你好！' });
      expect(onErr).not.toHaveBeenCalled();
      // 游标不因陈旧 marker@4 / 陈旧内容@2 回退（栅栏在游标写入之前收口）
      expect(provider.lastAppliedSeq).toBe(6);
    });
});
