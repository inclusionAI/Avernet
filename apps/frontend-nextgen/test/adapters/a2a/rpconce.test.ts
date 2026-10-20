/**
 * B11 — rpcOnce（单信封 JSON-RPC POST）测试：GetTask 恢复分诊的底座。
 * 失败分类复用 rpc.ts classifyFailure 口径（鉴权走 error 信封 reason 分支，
 * 非 HTTP 401/403——对拍 stream.test.ts 同源事实）。
 */
import { rpcOnce } from '@/adapters/a2a/rpcOnce';
import { buildRpcRequest } from '@/adapters/a2a/rpc';

function jsonResponse(body: string, status = 200): Response {
  return new Response(body, { status, headers: { 'content-type': 'application/json' } });
}

const capture = (resp: Response) => {
  const calls: Array<{ url: string; init: RequestInit }> = [];
  const impl: any = async (url: string, init: RequestInit) => {
    calls.push({ url, init });
    return resp;
  };
  impl.calls = calls;
  return impl as typeof fetch & { calls: typeof calls };
};

describe('rpcOnce — 单信封响应面', () => {
  it('result 信封原样返回；请求面：POST + content-type + x-iam-token；body 关联请求 id', async () => {
    const req = buildRpcRequest('GetTask', { taskId: 't1' });
    const fetchImpl = capture(jsonResponse(JSON.stringify({
      jsonrpc: '2.0', id: req.id,
      result: { task: { id: 't1', status: { state: 'TASK_STATE_COMPLETED' } } },
    })));
    const parsed = await rpcOnce('http://x/rpc', req,
      { iamToken: 'IAM-T', fetchImpl });
    expect(parsed.result).toEqual({ task: { id: 't1', status: { state: 'TASK_STATE_COMPLETED' } } });
    const [call] = (fetchImpl as any).calls;
    expect(call.url).toBe('http://x/rpc');
    const headers = new Headers(call.init.headers as HeadersInit);
    expect(headers.get('content-type')).toBe('application/json');
    expect(headers.get('x-iam-token')).toBe('IAM-T');
    expect(JSON.parse(String(call.init.body))).toMatchObject({ jsonrpc: '2.0', id: req.id });
  });

  it('error 信封 auth_required → typed auth（引擎鉴权门不走 HTTP 401/403）', async () => {
    const req = buildRpcRequest('GetTask', { taskId: 't1' });
    const fetchImpl = capture(jsonResponse(JSON.stringify({
      jsonrpc: '2.0', id: req.id,
      error: { code: -32000, message: 'Missing x-iam-token', data: { reason: 'auth_required' } },
    })));
    await expect(rpcOnce('http://x/rpc', req, { fetchImpl }))
      .rejects.toMatchObject({
        message: 'Missing x-iam-token',
        failure: { kind: 'auth', rpcError: { data: { reason: 'auth_required' } } },
      });
  });

  it('error 信封 not_found_or_expired → typed rpc（恢复层在此读 reason）', async () => {
    const req = buildRpcRequest('GetTask', { taskId: 't1' });
    const fetchImpl = capture(jsonResponse(JSON.stringify({
      jsonrpc: '2.0', id: req.id,
      error: { code: -32000, message: 'gone', data: { reason: 'not_found_or_expired' } },
    })));
    await expect(rpcOnce('http://x/rpc', req, { fetchImpl }))
      .rejects.toMatchObject({
        failure: { kind: 'rpc', rpcError: { data: { reason: 'not_found_or_expired' } } },
      });
  });

  it('id:null parse-error 通知信封 → 可解析、按分类抛（不 streamRpc 的 reject 反例）', async () => {
    const req = buildRpcRequest('GetTask', { taskId: 't1' });
    const fetchImpl = capture(jsonResponse(JSON.stringify({
      jsonrpc: '2.0', id: null,
      error: { code: -32700, message: 'Parse error' },
    })));
    await expect(rpcOnce('http://x/rpc', req, { fetchImpl }))
      .rejects.toMatchObject({ failure: { kind: 'rpc', rpcError: { code: -32700 } } });
  });

  it('HTTP 非 2xx → typed transport（httpStatus 随行）', async () => {
    const req = buildRpcRequest('GetTask', { taskId: 't1' });
    const fetchImpl = capture(jsonResponse('x', 503));
    await expect(rpcOnce('http://x/rpc', req, { fetchImpl }))
      .rejects.toMatchObject({ failure: { kind: 'transport', httpStatus: 503 } });
  });

  it('非 JSON / 非 RPC 信封 body → typed protocol bad_json_body', async () => {
    const req = buildRpcRequest('GetTask', { taskId: 't1' });
    for (const body of ['not json', '{}', '{"jsonrpc":"2.0","id":1}']) {
      // {}: 非 RPC 对象；第三例：result XOR error 不满足
      const fetchImpl = capture(jsonResponse(body));
      await expect(rpcOnce('http://x/rpc', req, { fetchImpl }))
        .rejects.toMatchObject({ failure: { kind: 'protocol', reason: 'bad_json_body' } });
    }
  });

  it('result 信封 id 与请求 id 不符 → typed protocol envelope_id_mismatch', async () => {
    const req = buildRpcRequest('GetTask', { taskId: 't1' });
    const fetchImpl = capture(jsonResponse(JSON.stringify({
      jsonrpc: '2.0', id: 999, result: { task: { id: 'other' } },
    })));
    await expect(rpcOnce('http://x/rpc', req, { fetchImpl }))
      .rejects.toMatchObject({ failure: { kind: 'protocol', reason: 'envelope_id_mismatch' } });
  });

  it('网络失败 → typed transport；signal 已 abort → typed aborted（取消归 runner 分类）', async () => {
    const req = buildRpcRequest('GetTask', { taskId: 't1' });
    const netFail: any = async () => { throw new TypeError('fetch failed'); };
    await expect(rpcOnce('http://x/rpc', req, { fetchImpl: netFail }))
      .rejects.toMatchObject({ failure: { kind: 'transport' } });

    const ctrl = new AbortController();
    ctrl.abort();
    expect(ctrl.signal.aborted).toBe(true);
    const abortedFetch: any = () =>
      Promise.reject(new DOMException('aborted', 'AbortError'))
        .catch((e: unknown) => { throw e; });
    const p = rpcOnce('http://x/rpc', req,
      { fetchImpl: abortedFetch, signal: ctrl.signal });
    await expect(p).rejects.toMatchObject({
      failure: { kind: 'transport', reason: 'aborted' },
    });
  });
});
