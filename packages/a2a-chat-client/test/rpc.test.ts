import { buildRpcRequest, parseRpcMessage, classifyFailure } from '../src/index';

describe('JSON-RPC 信封（engine sse.py 口径：data 行=完整响应信封）', () => {
  it('请求信封带递增 id', () => {
    const a = buildRpcRequest('SendStreamingMessage', {});
    const b = buildRpcRequest('SendStreamingMessage', {});
    expect(a.jsonrpc).toBe('2.0');
    expect(typeof a.id).toBe('number');
    expect(b.id).toBe(a.id + 1);
  });

  it('result 信封解析', () => {
    const m = parseRpcMessage('{"jsonrpc":"2.0","id":7,"result":{"seq":1}}');
    expect(m?.id).toBe(7);
    expect(m?.result).toEqual({ seq: 1 });
    expect(m?.error).toBeUndefined();
  });

  it('error 信封解析（含 reason）', () => {
    const m = parseRpcMessage(
      '{"jsonrpc":"2.0","id":7,"error":{"code":-32000,"message":"x","data":{"reason":"not_found_or_expired"}}}');
    expect(m?.error?.code).toBe(-32000);
    expect((m?.error?.data as any)?.reason).toBe('not_found_or_expired');
  });

  it('非 JSON / 缺 id / 缺 result+error → null（协议级问题）', () => {
    expect(parseRpcMessage('not json')).toBeNull();
    expect(parseRpcMessage('{"jsonrpc":"2.0"}')).toBeNull();
    expect(parseRpcMessage('{"jsonrpc":"2.0","id":1}')).toBeNull();
  });
});

describe('信封硬化（jsonrpc 版本 / id 类型 / result XOR error / error 形状）', () => {
  it('jsonrpc 非 "2.0" 字符串 → null', () => {
    expect(parseRpcMessage('{"jsonrpc":"9.9","id":1,"result":{}}')).toBeNull();
    expect(parseRpcMessage('{"jsonrpc":2.0,"id":1,"result":{}}')).toBeNull();
    expect(parseRpcMessage('{"id":1,"result":{}}')).toBeNull();
  });

  it('id 只接受 number|string；对象/布尔/null 作为请求关联 → null', () => {
    expect(parseRpcMessage('{"jsonrpc":"2.0","id":{},"result":{}}')).toBeNull();
    expect(parseRpcMessage('{"jsonrpc":"2.0","id":true,"result":{}}')).toBeNull();
    expect(parseRpcMessage('{"jsonrpc":"2.0","id":null,"result":{}}')).toBeNull();
  });

  it('字符串 id 可接受（JSON-RPC 允许）', () => {
    expect(parseRpcMessage('{"jsonrpc":"2.0","id":"abc","result":{}}')?.id).toBe('abc');
  });

  it('result 与 error 并存 → null', () => {
    expect(parseRpcMessage(
      '{"jsonrpc":"2.0","id":1,"result":{},"error":{"code":-1,"message":"x"}}')).toBeNull();
  });

  it('error 形状不合法（code/message 类型错、error 非对象）→ null', () => {
    expect(parseRpcMessage('{"jsonrpc":"2.0","id":1,"error":{"code":"-1","message":"x"}}')).toBeNull();
    expect(parseRpcMessage('{"jsonrpc":"2.0","id":1,"error":{"code":-1,"message":1}}')).toBeNull();
    expect(parseRpcMessage('{"jsonrpc":"2.0","id":1,"error":"oops"}')).toBeNull();
  });

  it('error.data 非对象被剥除，其余字段保留', () => {
    const m = parseRpcMessage(
      '{"jsonrpc":"2.0","id":1,"error":{"code":-1,"message":"x","data":"junk"}}');
    expect(m?.error?.data).toBeUndefined();
    expect(m?.error?.code).toBe(-1);
    expect(m?.error?.message).toBe('x');
  });

  it('id:null 错误信封（不可识别请求的 parse-error 通知）→ 可解析，id 记 null，不关联任何请求', () => {
    const m = parseRpcMessage(
      '{"jsonrpc":"2.0","id":null,"error":{"code":-32700,"message":"Parse error"}}');
    expect(m?.id).toBeNull();
    expect(m?.error?.code).toBe(-32700);
    expect(m?.result).toBeUndefined();
  });
});

describe('失败分类（spec §3.3：仅 auth/protocol 进 lock 候选；abort 不是失败）', () => {
  it('主动 abort → null', () => {
    expect(classifyFailure({ aborted: true })).toBeNull();
  });

  it('HTTP 401/403 → auth（lock 候选）', () => {
    expect(classifyFailure({ httpStatus: 401 })?.kind).toBe('auth');
  });

  it('其他 HTTP 非 2xx → transport（不锁）', () => {
    expect(classifyFailure({ httpStatus: 502 })?.kind).toBe('transport');
  });

  it('-32602 参数错 → rpc（业务上浮）', () => {
    expect(classifyFailure({ rpcError: { code: -32602, message: 'x' } })?.kind).toBe('rpc');
  });

  it('-32000 业务 reason → rpc（恢复流程吃掉，不锁）', () => {
    expect(classifyFailure({ rpcError: { code: -32000, message: 'x',
      data: { reason: 'cache_limit' } } })?.kind).toBe('rpc');
  });

  it('-32000 + reason=auth_required → auth（引擎鉴权拒绝走 JSON-RPC 面，无 HTTP 401）', () => {
    expect(classifyFailure({ rpcError: { code: -32000, message: 'Missing x-iam-token',
      data: { reason: 'auth_required' } } })?.kind).toBe('auth');
  });

  it('-32000 + reason=ZERO_CHECK_FAILED → auth', () => {
    expect(classifyFailure({ rpcError: { code: -32000, message: 'x',
      data: { reason: 'ZERO_CHECK_FAILED' } } })?.kind).toBe('auth');
  });

  it('-32000 其他 reason 仍为 rpc（业务）', () => {
    expect(classifyFailure({ rpcError: { code: -32000, message: 'x',
      data: { reason: 'not_found_or_expired' } } })?.kind).toBe('rpc');
  });

  it('无任何已知特征 → protocol（lock 候选）', () => {
    expect(classifyFailure({})?.kind).toBe('protocol');
  });
});