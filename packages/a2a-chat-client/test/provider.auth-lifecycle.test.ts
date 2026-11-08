/**
 * IAM getter 异常的请求生命周期（review 钉板）。
 * ============================================================================
 *
 * token 获取发生在 isRequesting 置位之后、rpc 出站之前。此前 getter 抛错/
 * 拒绝曾绕过清理——同 Provider 后续请求恒 "A request is already in
 * progress"。修复结构（request() 内独立边界，src/A2AProvider.ts 头注）：
 *   · getter 失败如实上抛并归还请求槽（不走发送阶段 catch——那会把
 *     "首个 Task 事件前的失败"误当出站后断流，转成任务结果未知/进恢复）；
 *   · 让位代次的迟到异常静默回归（不清新请求的槽、不触新请求的 UI 面）；
 *   · 等待 token 期间被取消/让位：不再启动发送。
 *
 * 断言边界（review 明确约束）：
 *   ① throw/reject 不吞、不"继续无 token 发送"；
 *   ② 带 .failure 的错误保留原分类，不误标统一的鉴权失败；
 *   ③ 不因 getter 异常自动重发/重试/触发恢复；
 *   ④ finally 无条件清理是禁手（A 迟到异常不得清 B 的在途槽）；
 *   ⑤ getter 返回 undefined 回落静态 xIAMToken 的既有行为零变化。
 */
import { A2AProvider, type A2AProviderConfig, a2aUrlsFromWsUrl }
  from '../src/index';
import { createOracleParserFactory } from './helpers/oracleLoader';
import { makeEngineCard } from './helpers';

const SESSION = 'agent:main:main';
const A2A_URLS = a2aUrlsFromWsUrl('ws://127.0.0.1:20003/api/a2a/ws');

interface CapturedCall { url: string; init: RequestInit }
type FetchStub = typeof fetch & { calls: CapturedCall[] };

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/**
 * rpc 响应永不主动关流（发送阶段的动作只靠 rpc POST 计数/头断言，无需解析
 * 帧）；但对齐真 fetch 的 abort 语义——signal 触发即打断 body 读取
 * （真实链路里这是 M1 静默收尾的前提，stub 不接 signal 会让 for-await
 * 永挂，abort 后 request() 永不结算）。
 */
const hangingRpc = (call: CapturedCall) => new Response(
  new ReadableStream({
    start(c) {
      const signal = (call.init as { signal?: AbortSignal }).signal;
      signal?.addEventListener('abort', () => {
        c.error(new DOMException('This operation was aborted', 'AbortError'));
      }, { once: true });
    },
  }),
  { status: 200, headers: { 'content-type': 'text/event-stream' } },
);

/** card 直通 + rpc 全捕获（token 阶段的每一条出站路由都可见） */
function routingFetch(): FetchStub {
  const calls: CapturedCall[] = [];
  const impl = (async (url: any, init: any = {}) => {
    const call = { url: String(url), init };
    calls.push(call);
    if (String(url) === A2A_URLS.cardUrl) {
      return new Response(JSON.stringify(makeEngineCard()), {
        status: 200, headers: { 'content-type': 'application/json' },
      });
    }
    return hangingRpc(call);
  }) as FetchStub;
  return Object.assign(impl, { calls }) as FetchStub;
}

function newProvider(config: Partial<A2AProviderConfig> = {}) {
  const fetchImpl = routingFetch();
  const provider = new A2AProvider({
    url: 'ws://127.0.0.1:20003/api/a2a/ws',
    a2aUrls: A2A_URLS,
    sessionKey: SESSION,
    parserFactory: createOracleParserFactory(),
    fetchImpl: fetchImpl as unknown as typeof fetch,
    ...config,
  });
  const spies = {
    onMessage: jest.fn(),
    onComplete: jest.fn(),
    onError: jest.fn(),
  };
  provider.onMessage = spies.onMessage;
  provider.onComplete = spies.onComplete;
  provider.onError = spies.onError;
  return { provider, spies, fetchImpl };
}

const rpcPostsOf = (f: FetchStub) => f.calls.filter((c) => c.url === A2A_URLS.rpcUrl);
/** streamRpc 以 Headers 实例出站（src/stream.ts:106）；两形态兼容读取 */
const tokenOf = (call: CapturedCall) => {
  const h = call.init.headers as Record<string, unknown>;
  return typeof h?.get === 'function' ? h.get('x-iam-token') : h?.['x-iam-token'];
};

// ────────────────────────────────────────────────────────────────────────

describe('IAM getter 异常生命周期（token 阶段独立边界）', () => {
  beforeEach(() => { jest.useRealTimers(); });

  it('getter 同步抛错：请求拒绝 + onError 一次 + 零 rpc 出站；getter 恢复后同槽立即可用',
    async () => {
      let fail = true;
      const { provider, spies, fetchImpl } = newProvider({
        iamTokenGetter: () => {
          if (fail) throw new Error('token boom');       // getter 同步抛错形态
          return Promise.resolve('healthy-token');
        },
      });
      await provider.connect();

      await expect(
        provider.request({ query: 'q', sessionKey: SESSION }),
      ).rejects.toThrow('token boom');

      expect(spies.onError).toHaveBeenCalledTimes(1);
      // token 阶段失败 ≠ 鉴权失败误标（无 .failure 也不造一个）
      expect((spies.onError.mock.calls[0][0] as { failure?: unknown }).failure)
        .toBeUndefined();
      expect(rpcPostsOf(fetchImpl)).toHaveLength(0);   // 零 SendStreamingMessage

      // 请求槽归还：getter 恢复后同 Provider 直接再请求——不被"A request is
      // already in progress"卡死（修复前此请求恒被拒）
      fail = false;
      const again = provider.request({ query: 'q2', sessionKey: SESSION });
      await sleep(50);
      expect(rpcPostsOf(fetchImpl)).toHaveLength(1);   // 已出站 = 槽可用实证
      expect(tokenOf(rpcPostsOf(fetchImpl)[0])).toBe('healthy-token');
      provider.abort();
      await again;                                     // M1：中断后 request 静默收尾
    });

  it('getter 异步 reject：同边界（onError 一次、零出站、槽归还）', async () => {
    let fail = true;
    const { provider, spies, fetchImpl } = newProvider({
      iamTokenGetter: () => (fail
        ? Promise.reject(new Error('async boom'))
        : Promise.resolve('healthy-token')),
    });
    await provider.connect();

    await expect(
      provider.request({ query: 'q', sessionKey: SESSION }),
    ).rejects.toThrow('async boom');

    expect(spies.onError).toHaveBeenCalledTimes(1);
    expect(rpcPostsOf(fetchImpl)).toHaveLength(0);

    // 槽归还：getter 恢复后同 Provider 直接再请求，照常出站
    fail = false;
    const again = provider.request({ query: 'q2', sessionKey: SESSION });
    await sleep(50);
    expect(rpcPostsOf(fetchImpl)).toHaveLength(1);
    provider.abort();
    await again;
  });

  it('getter 抛带 .failure 的错误：原样拒回、分类不丢、不误报任务结果未知、不进恢复',
    async () => {
      const authErr = Object.assign(new Error('iam rejected'), {
        failure: { kind: 'auth', reason: 'gateway_rejected' },
      });
      const { provider, spies, fetchImpl } = newProvider({
        iamTokenGetter: () => { throw authErr; },
      });
      await provider.connect();

      await expect(
        provider.request({ query: 'q', sessionKey: SESSION }),
      ).rejects.toBe(authErr);

      expect(spies.onError).toHaveBeenCalledTimes(1);
      const emitted = spies.onError.mock.calls[0][0] as Error & { failure: unknown };
      expect(emitted).toBe(authErr);                              // 原样上抛，不克隆重包
      expect(emitted.failure).toEqual({ kind: 'auth', reason: 'gateway_rejected' });
      // 不得被发送阶段语义污染成"结果未知"分类
      expect((emitted.failure as { reason: string }).reason)
        .not.toBe('prior_to_task_event');
      // 不触发恢复（ReplayEvents/GetTask 等额外 rpc）
      expect(rpcPostsOf(fetchImpl)).toHaveLength(0);
    });

  it('A 等 token → abort → B 出站 → A 迟到 reject：B 的槽/错误面零污染',
    async () => {
      let mode: 'defer' | 'healthy' = 'defer';
      let releaseA!: (v: string | PromiseLike<string>) => void;
      const { provider, spies, fetchImpl } = newProvider({
        iamTokenGetter: () => new Promise<string>((res) => {
          if (mode === 'healthy') { res('token-b'); return; }
          releaseA = res;                       // defer：A 挂在 token await 上
        }),
      });
      await provider.connect();

      const a = provider.request({ query: 'A', sessionKey: SESSION });
      await sleep(10);                          // A 已进入 token 等待
      provider.abort();                          // cancelled-clean（零 wire 面）

      mode = 'healthy';
      const b = provider.request({ query: 'B', sessionKey: SESSION });
      await sleep(50);                           // B 已出站挂流
      expect(rpcPostsOf(fetchImpl)).toHaveLength(1);

      releaseA(Promise.reject(new Error('late boom')));   // A 的迟到异常

      // A 让位代次静默收尾：不触 onError、不清 B 的槽
      await expect(a).resolves.toBeUndefined();
      expect(spies.onError).not.toHaveBeenCalled();
      expect(rpcPostsOf(fetchImpl)).toHaveLength(1);      // A 零补发

      // B 仍在途 = isRequesting 未被 A 的迟到异常清掉（禁手 finally 的实证）
      await expect(
        provider.request({ query: 'C', sessionKey: SESSION }),
      ).rejects.toThrow('A request is already in progress');

      provider.abort();
      await b;
    });

  it('A 等 token → abort → token 迟到成功：A 不再启动发送（零出站、不补发错误）',
    async () => {
      let releaseA!: (v: string | PromiseLike<string>) => void;
      const { provider, spies, fetchImpl } = newProvider({
        iamTokenGetter: () => new Promise<string>((res) => { releaseA = res; }),
      });
      await provider.connect();

      const a = provider.request({ query: 'A', sessionKey: SESSION });
      await sleep(10);
      provider.abort();                           // completed-clean 播报已由 abort 完成
      releaseA('late-token');
      await expect(a).resolves.toBeUndefined();

      expect(rpcPostsOf(fetchImpl)).toHaveLength(0);     // 让位后不再出站
      expect(spies.onError).not.toHaveBeenCalled();      // 不补发（abort 已 onComplete([])）
      expect(spies.onComplete).toHaveBeenCalledWith([]);
    });

  it('getter 返回 undefined：回落静态 xIAMToken（既有行为零变化）', async () => {
    const { provider, fetchImpl } = newProvider({
      iamTokenGetter: async () => undefined,
      xIAMToken: 'static-token',
    });
    await provider.connect();

    const p = provider.request({ query: 'q', sessionKey: SESSION });
    await sleep(50);
    expect(rpcPostsOf(fetchImpl)).toHaveLength(1);
    expect(tokenOf(rpcPostsOf(fetchImpl)[0])).toBe('static-token');
    provider.abort();
    await p;
  });
});