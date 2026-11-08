/**
 * A2A JSON-RPC 信封与失败分类（spec stage2 §3.3）。
 * LOCK_CANDIDATE：仅"传输面不兼容/鉴权面持续拒绝"进 locked-v3；
 * 普通 transport 断流走恢复正路，业务 rpc 错误上浮 UI。
 *
 * 引擎现实口径（router.py 鉴权门）：A2A HTTP 面不回 401/403，鉴权
 * 拒绝一律走 JSON-RPC error 信封 `{code: -32000, data.reason:
 * 'auth_required' | 'ZERO_CHECK_FAILED'}`——故 auth 分类必须在
 * rpcError 分支之前按 reason 判定，否则 spec §3.3 "鉴权面持续拒绝
 * → locked-v3" 永远死路。
 */
export interface RpcRequest { jsonrpc: '2.0'; id: number; method: string; params: unknown }
export interface RpcError { code: number; message: string; data?: Record<string, unknown> }
export interface ParsedRpc {
  /** id=null 仅出现在错误信封（不可识别请求的 parse-error 通知），不关联任何请求 */
  id: number | string | null;
  result?: unknown;
  error?: RpcError;
}
export type FailureKind = 'auth' | 'protocol' | 'rpc' | 'transport';
export interface WireFailure {
  kind: FailureKind;
  /**
   * 类型化失败判别词（typed sub-failure 的公共判别面）——各消费层按 reason 分流：
   * recovery 的 not_found_or_expired、A2AProvider 的 prior_to_task_event/
   * approval_unsupported、cancelOutcomes 分类等。stage2 初版该字段只在
   * extends 子接口上出现（ts-jest transpile-only 未检）——nextgen 严格 tsc
   * 下收口到基类（类型级收口，运行时零差异）。
   */
  reason?: string;
  httpStatus?: number;
  rpcError?: RpcError;
}

export const LOCK_CANDIDATE_KINDS: ReadonlySet<FailureKind> =
  new Set<FailureKind>(['auth', 'protocol']);

// 引擎鉴权门在 JSON-RPC error 信封里给出的失败 reason（无 HTTP 401/403）。
const AUTH_REASONS = new Set(['auth_required', 'ZERO_CHECK_FAILED']);

let nextId = 1;
export function buildRpcRequest(method: string, params: unknown): RpcRequest {
  return { jsonrpc: '2.0', id: nextId++, method, params };
}

export function parseRpcMessage(raw: string): ParsedRpc | null {
  let parsed: any;
  try { parsed = JSON.parse(raw); } catch { return null; }
  if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) return null;
  if (typeof parsed.jsonrpc !== 'string' || parsed.jsonrpc !== '2.0') return null;
  const hasResult = parsed.result !== undefined;
  const hasError = parsed.error !== undefined;
  if (hasResult === hasError) return null;   // result XOR error；并存/皆缺都拒绝
  if (hasResult) {
    const id = parsed.id;
    if (typeof id !== 'number' && typeof id !== 'string') return null;
    return { id, result: parsed.result };
  }
  const e = parsed.error;
  if (typeof e !== 'object' || e === null
      || typeof e.code !== 'number' || typeof e.message !== 'string') return null;
  const data = (typeof e.data === 'object' && e.data !== null && !Array.isArray(e.data))
    ? e.data : undefined;                     // data 非对象 → 剥除
  const error: RpcError = data === undefined
    ? { code: e.code, message: e.message }
    : { code: e.code, message: e.message, data };
  // JSON-RPC 允许 error 信封 id:null（解析失败通知）：可解析但不关联任何请求
  if (parsed.id === null) return { id: null, error };
  const id = parsed.id;
  if (typeof id !== 'number' && typeof id !== 'string') return null;
  return { id, error };
}

export function classifyFailure(input: {
  httpStatus?: number; rpcError?: RpcError; aborted?: boolean;
}): WireFailure | null {
  if (input.aborted) return null;                       // 主动取消不是失败
  // 鉴权拒绝先于业务 rpc：引擎鉴权门回 -32000+reason，须归 auth（lock 候选）
  const rpcReason = (input.rpcError?.data as { reason?: unknown } | undefined)?.reason;
  if (input.rpcError && input.rpcError.code === -32000
    && typeof rpcReason === 'string' && AUTH_REASONS.has(rpcReason)) {
    return { kind: 'auth', rpcError: input.rpcError };
  }
  if (input.rpcError) return { kind: 'rpc', rpcError: input.rpcError };
  const s = input.httpStatus;
  if (s === 401 || s === 403) return { kind: 'auth', httpStatus: s };
  if (s !== undefined) return { kind: 'transport', httpStatus: s };
  return { kind: 'protocol' };                          // 无已知特征=协议问题
}