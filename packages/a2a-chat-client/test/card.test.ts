import { fetchCard, validateScenarios, Scenario } from '../src/index';
import { makeEngineCard } from './helpers';

describe('card 探测（spec §2/§5：card 通过=基础面；场景缺项→该场景 v3）', () => {
  it('HTTP 非 2xx → card null + reason（probe-v3 不锁定）', async () => {
    const fetchImpl = async () => new Response(null, { status: 404 });
    const r = await fetchCard('http://x/.well-known/agent-card.json', fetchImpl as any);
    expect(r.card).toBeNull();
    expect(r.reason).toBe('http_404');
  });

  it('网络异常 → card null + network reason', async () => {
    const fetchImpl = async () => { throw new TypeError('network down'); };
    const r = await fetchCard('http://x/.well-known/agent-card.json', fetchImpl as any);
    expect(r.card).toBeNull();
    expect(r.reason).toBe('network');
  });

  it('非 JSON → card null + bad_json', async () => {
    const fetchImpl = async () => new Response('not json', { status: 200 });
    const r = await fetchCard('http://x/.well-known/agent-card.json', fetchImpl as any);
    expect(r.reason).toBe('bad_json');
  });

  it('结构缺基础面（非 JSONRPC/1.0）→ 场景全 fail', () => {
    const card = makeEngineCard({ binding: 'HTTP+JSON' });
    const rs = validateScenarios(card, [Scenario.STREAM, Scenario.RECOVERY, Scenario.HITL]);
    expect(rs.every((x: any) => !x.ok)).toBe(true);
    expect(rs[0].reason).toBe('no_jsonrpc_interface');
  });

  it('全量引擎 card（六扩展+streaming）→ 三场景全过', () => {
    const rs = validateScenarios(makeEngineCard(), [
      Scenario.STREAM, Scenario.RECOVERY, Scenario.HITL,
    ]);
    expect(rs.every((x: any) => x.ok)).toBe(true);
  });

  it('缺 resolve 声明（部分支持引擎）→ 仅 hitl fail', () => {
    const rs = validateScenarios(makeEngineCard({ resolve: false }), [Scenario.HITL]);
    expect(rs[0].ok).toBe(false);
    expect(rs[0].reason).toBe('missing_resolve_extension');
  });

  it('streaming=false → stream 场景 fail（reason not_streaming）', () => {
    const rs = validateScenarios(makeEngineCard({ streaming: false }), [Scenario.STREAM]);
    expect(rs[0].reason).toBe('not_streaming');
  });

  it('extensions 非数组（畸形卡）→ 场景 fail 而非抛错', () => {
    const rs = validateScenarios({ ...makeEngineCard(), capabilities:
      { streaming: true, extensions: 'corrupted' } }, [Scenario.RECOVERY]);
    expect(rs[0].ok).toBe(false);
    expect(rs[0].reason).toBe('missing_replay_extension');
  });
});

// 模拟真实 fetch 对 abort signal 的行为：拒绝挂在 signal 上（真 fetch 同口径）
const hangingFetch = (u: string, init?: RequestInit) =>
  new Promise<Response>((_res, reject) => {
    const s = init?.signal as AbortSignal | undefined;
    if (!s) return;                            // 无 signal = 真挂起
    if (s.aborted) reject(new DOMException('aborted', 'AbortError'));
    else s.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')));
  });

describe('card 探测超时与取消（探测不携带 token；永久挂死须有上限）', () => {
  it('fetch 永不返回 + timeoutMs 20ms → card null + reason timeout', async () => {
    const r = await fetchCard('http://x/.well-known/agent-card.json',
      hangingFetch as any, { timeoutMs: 20 });
    expect(r.card).toBeNull();
    expect(r.reason).toBe('timeout');
  });

  it('调用方预先 abort → reason aborted（区分于超时）', async () => {
    const ctrl = new AbortController();
    ctrl.abort();
    const r = await fetchCard('http://x/.well-known/agent-card.json',
      hangingFetch as any, { signal: ctrl.signal });
    expect(r.card).toBeNull();
    expect(r.reason).toBe('aborted');
  });

  it('timeoutMs 后调用方才 abort → 仍归 aborted（调用方信号优先）', async () => {
    const ctrl = new AbortController();
    const p = fetchCard('http://x/.well-known/agent-card.json',
      hangingFetch as any, { signal: ctrl.signal, timeoutMs: 50 });
    setTimeout(() => ctrl.abort(), 10);
    const r = await p;
    expect(r.reason).toBe('aborted');
  });

  // 响应头已到、响应体阻塞：超时必须覆盖 body 读取（真 fetch abort 会让
  // 挂起的 body 读以 AbortError 报错）。旧实现 fetch 返回即 clearTimeout +
  // 摘除 abort 监听 → json() 永悬，探测永不收口——本用例 10s 兜底超时即红。
  it('响应头已到、响应体阻塞 → 超时覆盖 body 读取 → reason timeout', async () => {
    const r = await fetchCard('http://x/.well-known/agent-card.json',
      headersThenHangingBodyFetch as any, { timeoutMs: 20 });
    expect(r.card).toBeNull();
    expect(r.reason).toBe('timeout');
  }, 10_000);

  it('响应头已到、响应体阻塞 + 调用方 abort → 归 aborted', async () => {
    const ctrl = new AbortController();
    const p = fetchCard('http://x/.well-known/agent-card.json',
      headersThenHangingBodyFetch as any, { signal: ctrl.signal, timeoutMs: 50 });
    setTimeout(() => ctrl.abort(), 10);
    const r = await p;
    expect(r.reason).toBe('aborted');
  });
});

// 模拟真 fetch 的 body 语义：头立即返回，body ReadableStream 挂死；仅在
// fetch 的 signal abort 时让 body 以 AbortError 断流（与 undici 同口径）。
const headersThenHangingBodyFetch = (u: string, init?: RequestInit) =>
  new Promise<Response>((resolve, reject) => {
    const s = init?.signal as AbortSignal | undefined;
    if (!s) return;                    // 无 signal：真挂起
    if (s.aborted) { reject(new DOMException('aborted', 'AbortError')); return; }
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        s.addEventListener('abort', () => {
          try { controller.error(new DOMException('aborted', 'AbortError')); } catch { /* closed */ }
        });
        // 不 enqueue、不 close：body 永不完成
      },
    });
    resolve(new Response(stream, { status: 200, headers: { 'content-type': 'application/json' } }));
  });