/**
 * rpcOnce — 非流式单信封 JSON-RPC POST（B11 GetTask 恢复三态分诊用）。
 * ============================================================================
 *
 * streamRpc 是 B10 的流面；GetTask 是**单请求-单响应**面（引擎回
 * `{jsonrpc, id, result:{task:{...}}}` 或 JSON-RPC error 信封，无 SSE）。
 * 失败分类复用 rpc.ts classifyFailure：鉴权门（-32000 + reason
 * auth_required/ZERO_CHECK_FAILED）→ auth；业务 error 信封 → rpc（带
 * rpcError.data.reason，恢复层在此读 not_found_or_expired）。
 *
 * 超时面：本层不自带定时器——单连接 10s 上限由 RecoveryRunner 的 per-call
 * race 统一施加（与总预算取 min 的归属地在 runner，见 recovery.ts 头注）。
 */
import { parseRpcMessage, classifyFailure, type ParsedRpc, type RpcRequest, type WireFailure } from './rpc';

function typedError(message: string, failure: WireFailure): Error & { failure: WireFailure } {
  return Object.assign(new Error(message), { failure });
}

export async function rpcOnce(
  rpcUrl: string, request: RpcRequest,
  opts: { iamToken?: string; signal?: AbortSignal; fetchImpl?: typeof fetch } = {},
): Promise<ParsedRpc> {
  const headers = new Headers({
    'content-type': 'application/json',
    ...(opts.iamToken ? { 'x-iam-token': opts.iamToken } : {}),
  });
  let resp: Response;
  try {
    resp = await (opts.fetchImpl ?? fetch)(rpcUrl, {
      method: 'POST', headers, signal: opts.signal,
      body: JSON.stringify(request),
    });
  } catch (err) {
    if (opts.signal?.aborted) {
      // 主动取消：对位 streamRpc 的"取消非失败"，但单信封面没有静默产出位，
      // 须回一个 typed（runner 分类为可重试后在取消门前停下）
      throw typedError('a2a rpcOnce aborted', { kind: 'transport', reason: 'aborted' });
    }
    if (err instanceof Error && err.name === 'AbortError') {
      throw typedError('a2a rpcOnce aborted', { kind: 'transport', reason: 'aborted' });
    }
    throw Object.assign(new TypeError('a2a transport'), { failure: { kind: 'transport' } });
  }
  if (!resp.ok) {
    throw typedError(`a2a http ${resp.status}`, classifyFailure({ httpStatus: resp.status })!);
  }
  let text: string;
  try {
    text = await resp.text();
  } catch {
    if (opts.signal?.aborted) {
      throw typedError('a2a rpcOnce aborted', { kind: 'transport', reason: 'aborted' });
    }
    throw typedError('a2a rpcOnce body broken', { kind: 'protocol', reason: 'body_read_failed' });
  }
  const parsed = parseRpcMessage(text);
  if (!parsed) {
    throw typedError('a2a bad json body', { kind: 'protocol', reason: 'bad_json_body' });
  }
  if (parsed.error) {
    throw typedError(parsed.error.message, classifyFailure({ rpcError: parsed.error })!);
  }
  if (parsed.id !== request.id) {
    // 单信封响应必须关联本次请求（id:null 的 parse-error 通知已在上面的
    // error 分支按分类抛出，不会流到这里）
    throw typedError('a2a envelope id mismatch',
      { kind: 'protocol', reason: 'envelope_id_mismatch' });
  }
  return parsed;
}