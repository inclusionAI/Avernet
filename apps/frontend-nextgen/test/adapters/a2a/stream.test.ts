import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { streamRpc, MAX_PENDING_BYTES } from '@/adapters/a2a/stream';
import { buildRpcRequest, type RpcRequest } from '@/adapters/a2a/rpc';

const enc = new TextEncoder();
function sseResponse(chunks: string[], status = 200): Response {
  const stream = new ReadableStream({
    start(c) { chunks.forEach((ch) => c.enqueue(enc.encode(ch))); c.close(); },
  });
  return new Response(stream, {
    status, headers: { 'content-type': 'text/event-stream' },
  });
}

// === 引擎真实 wire 形态构造器（口径对拍 __tests__/fixtures/engine-wire-snapshot.json）===
// result 为"单一成员"对象：{task}|{message}|{statusUpdate}|{artifactUpdate}，无 type/ev 包装、无顶层 seq。
const resultEnvelope = (id: number | string, result: Record<string, unknown>) =>
  ({ jsonrpc: '2.0', id, result });
const membersToSse = (envs: any[]) =>
  envs.map((e) => `data: ${JSON.stringify(e)}\n\n`).join('');

const taskEnv = (id: number) => resultEnvelope(id, {
  task: { id: 't1', contextId: 'c1', status: { state: 'TASK_STATE_WORKING' }, metadata: {} },
});
const artifactEnv = (id: number, seq: number, text: string, lastChunk = false) =>
  resultEnvelope(id, {
    artifactUpdate: { taskId: 't1', contextId: 'c1',
      artifact: { artifactId: 'text-0', parts: [{ text }] },
      append: true, lastChunk, metadata: { seq } },
  });
const finalEnv = (id: number, seq: number) => resultEnvelope(id, {
  statusUpdate: { taskId: 't1', contextId: 'c1',
    status: { state: 'TASK_STATE_COMPLETED' },
    metadata: { seq, final: 'true', lastSeq: seq } },
});

async function collect(gen: AsyncGenerator<any>) {
  const out: any[] = [];
  for await (const e of gen) out.push(e);
  return out;
}

function jsonResponse(body: string, status = 200): Response {
  return new Response(body, { status, headers: { 'content-type': 'application/json' } });
}

// 跨仓对拍快照：fixture 由真引擎 A2aKernel 产出（真实发布序，勿手改——
// 重生成见 fixture 的 _header 与 scripts/gen-engine-wire-snapshot.py）。
// 信封 id=1，故用显式 request（不走模块内递增 id）保证关联校验与 fixture 恒一致。
const snapshot: any[] = (JSON.parse(readFileSync(
  join(__dirname, 'fixtures', 'engine-wire-snapshot.json'), 'utf8'))).envelopes;

describe('SSE 成员形态解析（engine wire.py 真实口径，跨仓对拍）', () => {
  it('引擎真实 wire 快照全序列可消费（跨仓对拍，fixture 由引擎 kernel 产出）', async () => {
    const request: RpcRequest = { jsonrpc: '2.0', id: 1, method: 'SendStreamingMessage', params: {} };
    const fetchImpl = async () => sseResponse([membersToSse(snapshot)]);
    const events = await collect(streamRpc('http://x/rpc',
      request, { fetchImpl: fetchImpl as any }));
    expect(events.map((e) => e.type)).toEqual([
      'task', 'statusUpdate', 'artifactUpdate', 'artifactUpdate', 'artifactUpdate',
      'statusUpdate', 'statusUpdate']);
    expect(events.map((e) => e.seq)).toEqual([null, 2, 3, 4, 5, 6, 6]);
    // 终态两连发（kernel.complete_run:156-180 引擎事实）：state-change@N+1
    // （非 final）先消费，final marker 以**同一 seq N+1** 下发——共 seq 钉板
    expect(events[5].metadata.final).toBeUndefined();
    expect(events[6].metadata.final).toBe('true');
    expect(events[6].seq).toBe(events[5].seq);
    expect(events[6].metadata.lastSeq).toBe(events[6].seq);
  });

  it('逐事件产出：task/artifactUpdate/statusUpdate 成员转成扁平 wire 事件', async () => {
    const req = buildRpcRequest('SendStreamingMessage', {});
    const fetchImpl = async () => sseResponse([
      membersToSse([taskEnv(req.id), artifactEnv(req.id, 1, '你'), finalEnv(req.id, 2)]),
    ]);
    const events = await collect(streamRpc('http://x/rpc',
      req, { fetchImpl: fetchImpl as any }));
    expect(events.map((e) => [e.seq, e.type]))
      .toEqual([[null, 'task'], [1, 'artifactUpdate'], [2, 'statusUpdate']]);
    expect(events[0].member).toMatchObject({ id: 't1' });
    expect(events[1].metadata).toEqual({ seq: 1 });
    expect(events[2].metadata.final).toBe('true');
  });

  it('final marker 到达即停（不再读流尾）', async () => {
    const req = buildRpcRequest('m', {});
    const fetchImpl = async () => sseResponse([
      membersToSse([artifactEnv(req.id, 1, 'a'), finalEnv(req.id, 2), artifactEnv(req.id, 99, 'late')]),
    ]);
    const events = await collect(streamRpc('http://x/rpc',
      req, { fetchImpl: fetchImpl as any }));
    expect(events.map((e) => e.seq)).toEqual([1, 2]);   // late 事件不被消费
  });

  it('分块跨界（半行）正确拼接', async () => {
    const req = buildRpcRequest('m', {});
    const whole = membersToSse([artifactEnv(req.id, 1, 'x')]);
    const [head, tail] = [whole.slice(0, 20), whole.slice(20)];
    const fetchImpl = async () => sseResponse([
      head, tail, membersToSse([finalEnv(req.id, 2)])]);
    const events = await collect(streamRpc('http://x/rpc',
      req, { fetchImpl: fetchImpl as any }));
    expect(events.map((e) => e.seq)).toEqual([1, 2]);
  });

  it('SSE 注释行/非 data 行被忽略', async () => {
    const req = buildRpcRequest('m', {});
    const fetchImpl = async () => sseResponse([
      ': keepalive\n\n', membersToSse([artifactEnv(req.id, 1, 'a'), finalEnv(req.id, 2)]),
    ]);
    const events = await collect(streamRpc('http://x/rpc',
      req, { fetchImpl: fetchImpl as any }));
    expect(events.map((e) => e.seq)).toEqual([1, 2]);
  });

  it('message 成员同样可消费（引擎知识事件）', async () => {
    const req = buildRpcRequest('m', {});
    const messageEnv = resultEnvelope(req.id, {
      message: { role: 'agent', parts: [{ text: 'hi' }], metadata: { seq: 1 } },
    });
    const fetchImpl = async () => sseResponse([
      membersToSse([messageEnv, finalEnv(req.id, 2)])]);
    const events = await collect(streamRpc('http://x/rpc',
      req, { fetchImpl: fetchImpl as any }));
    expect(events[0].type).toBe('message');
    expect(events[0].seq).toBe(1);
  });

  it('metadata 缺省 → {}；metadata.seq 非数字 → seq null', async () => {
    const req = buildRpcRequest('m', {});
    const noMeta = resultEnvelope(req.id, {
      statusUpdate: { taskId: 't1', status: { state: 'TASK_STATE_WORKING' } },
    });
    const strSeq = resultEnvelope(req.id, {
      artifactUpdate: { taskId: 't1', metadata: { seq: '7' } },
    });
    const fetchImpl = async () => sseResponse([
      membersToSse([noMeta, strSeq, finalEnv(req.id, 3)])]);
    const events = await collect(streamRpc('http://x/rpc',
      req, { fetchImpl: fetchImpl as any }));
    expect(events[0].metadata).toEqual({});
    expect(events[0].seq).toBeNull();
    expect(events[1].seq).toBeNull();
  });

  it('result 非对象（数组/标量）/零成员/多成员/成员非对象 → protocol 失败', async () => {
    const req = buildRpcRequest('m', {});
    const cases: unknown[] = [
      [1, 2],                          // 数组
      'hello',                         // 标量
      { foo: 1 },                      // 零个已知成员
      null,                            // null 结果
      { task: { id: 't' }, artifactUpdate: { taskId: 't' } },  // 两个成员
      { task: 'not-an-object' },       // 成员非对象
    ];
    for (const result of cases) {
      const fetchImpl = async () => sseResponse([
        membersToSse([{ jsonrpc: '2.0', id: req.id, result }]),
      ]);
      await expect(collect(streamRpc('http://x/rpc',
        req, { fetchImpl: fetchImpl as any })))
        .rejects.toMatchObject({ failure: { kind: 'protocol' } });
    }
  });

  it('信封 id 与请求 id 不匹配 → protocol 失败（envelope_id_mismatch）', async () => {
    const req = buildRpcRequest('m', {});
    const fetchImpl = async () => sseResponse([
      membersToSse([artifactEnv(req.id + 100, 1, 'x')]),   // 信封载体 id 错位
    ]);
    await expect(collect(streamRpc('http://x/rpc',
      req, { fetchImpl: fetchImpl as any })))
      .rejects.toMatchObject({ failure: { kind: 'protocol', reason: 'envelope_id_mismatch' } });
  });

  it('流中 error 信封 → 抛 rpc 失败（typed）', async () => {
    const req = buildRpcRequest('m', {});
    const fetchImpl = async () => sseResponse([
      `data: ${JSON.stringify({ jsonrpc: '2.0', id: req.id,
        error: { code: -32000, message: 'x', data: { reason: 'not_found_or_expired' } } })}\n\n`,
    ]);
    await expect(collect(streamRpc('http://x/rpc',
      req, { fetchImpl: fetchImpl as any })))
      .rejects.toMatchObject({ failure: { kind: 'rpc' } });
  });

  it('HTTP 非 2xx → 抛 failure（kind=transport 由 classify 定）', async () => {
    const req = buildRpcRequest('m', {});
    const fetchImpl = async () => sseResponse([], 502);
    await expect(collect(streamRpc('http://x/rpc',
      req, { fetchImpl: fetchImpl as any })))
      .rejects.toMatchObject({ failure: { kind: 'transport', httpStatus: 502 } });
  });

  it('读流中途 reader 错误（网络 reset）→ 抛 typed transport 失败，不是裸错误', async () => {
    const req = buildRpcRequest('m', {});
    const stream = new ReadableStream({
      start(c) { c.enqueue(enc.encode(membersToSse([artifactEnv(req.id, 1, 'a')]))); },
      pull(c) { c.error(new TypeError('reset')); },
    });
    const fetchImpl = async () => new Response(stream, {
      status: 200, headers: { 'content-type': 'text/event-stream' },
    });
    await expect(collect(streamRpc('http://x/rpc',
      req, { fetchImpl: fetchImpl as any })))
      .rejects.toMatchObject({ failure: { kind: 'transport' } });
  });

  it('iamToken 进 x-iam-token 头；accept=text/event-stream', async () => {
    const req = buildRpcRequest('m', {});
    let seen: HeadersInit | undefined;
    const fetchImpl = async (_u: string, init?: RequestInit) => {
      seen = init?.headers as HeadersInit;
      return sseResponse([membersToSse([finalEnv(req.id, 1)])]);
    };
    await collect(streamRpc('http://x/rpc', req,
      { iamToken: 'IAM-T', fetchImpl: fetchImpl as any }));
    const h = new Headers(seen);
    expect(h.get('x-iam-token')).toBe('IAM-T');
    expect(h.get('accept')).toBe('text/event-stream');
  });
});

describe('非 SSE JSON 错误体路由（引擎鉴权门：200 + application/json，拒绝不走 HTTP 401/403）', () => {
  it('200 + application/json + auth_required 错误信封 → 抛 auth（评测缺口的 REPRO）', async () => {
    const req = buildRpcRequest('m', {});
    const fetchImpl = async () => jsonResponse(JSON.stringify({
      jsonrpc: '2.0', id: req.id,
      error: { code: -32000, message: 'Missing x-iam-token',
        data: { reason: 'auth_required' } },
    }));
    await expect(collect(streamRpc('http://x/rpc',
      req, { fetchImpl: fetchImpl as any })))
      .rejects.toMatchObject({ failure: { kind: 'auth' } });
  });

  it('200 + JSON + not_found_or_expired → 抛 rpc（业务上浮）', async () => {
    const req = buildRpcRequest('m', {});
    const fetchImpl = async () => jsonResponse(JSON.stringify({
      jsonrpc: '2.0', id: req.id,
      error: { code: -32000, message: 'x', data: { reason: 'not_found_or_expired' } },
    }));
    await expect(collect(streamRpc('http://x/rpc',
      req, { fetchImpl: fetchImpl as any })))
      .rejects.toMatchObject({ failure: { kind: 'rpc' } });
  });

  it('200 + JSON 畸形体 → 抛 protocol（bad_json_body）', async () => {
    const req = buildRpcRequest('m', {});
    const fetchImpl = async () => jsonResponse('not json at all');
    await expect(collect(streamRpc('http://x/rpc',
      req, { fetchImpl: fetchImpl as any })))
      .rejects.toMatchObject({ failure: { kind: 'protocol', reason: 'bad_json_body' } });
  });

  it('200 + 空 body（非 SSE）→ 抛 protocol（empty_body）', async () => {
    const req = buildRpcRequest('m', {});
    const fetchImpl = async () => jsonResponse('   ');
    await expect(collect(streamRpc('http://x/rpc',
      req, { fetchImpl: fetchImpl as any })))
      .rejects.toMatchObject({ failure: { kind: 'protocol', reason: 'empty_body' } });
  });

  it('200 + JSON result 信封（非 SSE 面不该出现）→ 抛 protocol（unexpected_success_body）', async () => {
    const req = buildRpcRequest('m', {});
    const fetchImpl = async () => jsonResponse(JSON.stringify({
      jsonrpc: '2.0', id: req.id, result: { task: { id: 't1' } },
    }));
    await expect(collect(streamRpc('http://x/rpc',
      req, { fetchImpl: fetchImpl as any })))
      .rejects.toMatchObject({ failure: { kind: 'protocol', reason: 'unexpected_success_body' } });
  });
});

describe('SSE 流终止语义', () => {
  it('未收到 final 即 EOF → 抛 transport sse_eof_no_final（先产出的成员不丢）', async () => {
    const req = buildRpcRequest('m', {});
    const fetchImpl = async () => sseResponse([
      membersToSse([artifactEnv(req.id, 1, '你'), artifactEnv(req.id, 2, '好')]),
    ]);
    const got: any[] = [];
    let caught: any;
    try {
      for await (const e of streamRpc('http://x/rpc', req, { fetchImpl: fetchImpl as any })) {
        got.push(e);
      }
    } catch (err) { caught = err; }
    expect(caught?.failure).toEqual({ kind: 'transport', reason: 'sse_eof_no_final' });
    expect(got.map((e) => e.seq)).toEqual([1, 2]);
  });

  it('零事件 + 剩余缓冲恰为错误信封（SSE 头但裸 JSON 收尾）→ 按分类抛', async () => {
    const req = buildRpcRequest('m', {});
    const rawBody = JSON.stringify({
      jsonrpc: '2.0', id: req.id,
      error: { code: -32000, message: 'x', data: { reason: 'auth_required' } },
    });
    const fetchImpl = async () => sseResponse([rawBody]);   // 无 data: 行、无 \n\n
    await expect(collect(streamRpc('http://x/rpc',
      req, { fetchImpl: fetchImpl as any })))
      .rejects.toMatchObject({ failure: { kind: 'auth' } });
  });
});

describe('解析缓冲上限（防畸形/恶意流拖死内存）', () => {
  it('MAX_PENDING_BYTES 默认 1MB', () => {
    expect(MAX_PENDING_BYTES).toBe(1024 * 1024);
  });

  it('无分隔符的超限单 chunk → protocol event_overflow', async () => {
    const req = buildRpcRequest('m', {});
    const stream = new ReadableStream({
      start(c) { c.enqueue(enc.encode('x'.repeat(1024))); },  // 无 SEP、不 close
    });
    const fetchImpl = async () => new Response(stream, {
      status: 200, headers: { 'content-type': 'text/event-stream' },
    });
    await expect(collect(streamRpc('http://x/rpc', req,
      { fetchImpl: fetchImpl as any, maxPendingBytes: 64 })))
      .rejects.toMatchObject({ failure: { kind: 'protocol', reason: 'event_overflow' } });
  });

  it('持续小写入（无分隔符）累积越限 → 同样 event_overflow', async () => {
    const req = buildRpcRequest('m', {});
    const stream = new ReadableStream({
      pull(c) { c.enqueue(enc.encode('y'.repeat(10) + ' ')); },  // 永续供给、无 SEP
    });
    const fetchImpl = async () => new Response(stream, {
      status: 200, headers: { 'content-type': 'text/event-stream' },
    });
    await expect(collect(streamRpc('http://x/rpc', req,
      { fetchImpl: fetchImpl as any, maxPendingBytes: 64 })))
      .rejects.toMatchObject({ failure: { kind: 'protocol', reason: 'event_overflow' } });
  });

  it('非 SSE body 超上限 → protocol body_overflow', async () => {
    const req = buildRpcRequest('m', {});
    const fetchImpl = async () => jsonResponse('z'.repeat(200));
    await expect(collect(streamRpc('http://x/rpc', req,
      { fetchImpl: fetchImpl as any, maxPendingBytes: 64 })))
      .rejects.toMatchObject({ failure: { kind: 'protocol', reason: 'body_overflow' } });
  });

  it('事件按行源源排干时不受上限影响（每帧 < cap 即持续产出）', async () => {
    const req = buildRpcRequest('m', {});
    const frames = Array.from({ length: 30 }, (_, i) =>
      membersToSse([artifactEnv(req.id, i + 1, 'a')]));
    const stream = new ReadableStream({
      start(c) { frames.forEach((f) => c.enqueue(enc.encode(f))); c.close(); },
    });
    // 正常流每帧排干不超过 cap，但故意不收 final：上手 expect 的是 transport EOF 失败
    const fetchImpl = async () => new Response(stream, {
      status: 200, headers: { 'content-type': 'text/event-stream' },
    });
    const got: any[] = [];
    let caught: any;
    try {
      for await (const e of streamRpc('http://x/rpc', req,
        { fetchImpl: fetchImpl as any, maxPendingBytes: 64 })) {
        got.push(e);
      }
    } catch (err) { caught = err; }
    expect(got).toHaveLength(30);
    expect(caught?.failure).toMatchObject({ kind: 'transport', reason: 'sse_eof_no_final' });
  });
});
