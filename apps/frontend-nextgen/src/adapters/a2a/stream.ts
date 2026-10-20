/**
 * A2A SSE 流解析（engine router _sse() 真实 wire 口径，跨仓对拍
 * __tests__/fixtures/engine-wire-snapshot.json——fixture 由引擎 wire 函数产出）。
 * wire 事件形态：data: 行 = JSON-RPC 信封 `{jsonrpc:"2.0", id, result}`，
 * result 是"单一成员"普通对象——恰好含有 task|message|statusUpdate|artifactUpdate
 * 之一（无 type/ev 包装、无顶层 seq）；metadata 在成员对象内部
 * （statusUpdate/artifactUpdate 的 metadata.seq 恒为 number；task 的
 * metadata={}，客户端 seq 归 null）。终态 = statusUpdate 成员
 * metadata.final==="true"（服务端随后关流）。M1 只有单向流消费，
 * 恢复/取消/重试属 M2+。
 * 注意：SEP 按引擎直连 \n\n 口径解析；CRLF 整流属 M4 企业代理链验收项（反代常见 \r\n\r\n）。
 */
import { parseRpcMessage, classifyFailure, type RpcRequest, type WireFailure } from './rpc';

export interface WireEvent {
  /** 引擎对 task 事件的成员 metadata 不置 seq——真值口径是 number|null */
  seq: number | null;
  type: string;                       // 'task' | 'message' | 'statusUpdate' | 'artifactUpdate'
  member: Record<string, unknown>;    // result.<type> 的内层成员对象
  metadata: Record<string, unknown>;  // 内层成员的 metadata（缺省 {}）
}

const SEP = '\n\n';

/** 解析中缓冲上限：防畸形/恶意不间断流把内存拖死（1MB 对齐常见网关界限）。
 * 测试可经 opts.maxPendingBytes 调小触发。 */
export const MAX_PENDING_BYTES = 1024 * 1024;

/** result 里被承认的事件成员名；其余形状一律按协议破坏处理 */
const MEMBER_KEYS = ['task', 'message', 'statusUpdate', 'artifactUpdate'];

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}

function typedError(message: string, failure: WireFailure): Error & { failure: WireFailure } {
  return Object.assign(new Error(message), { failure });
}

interface MemberEvent {
  type: string; member: Record<string, unknown>;
  metadata: Record<string, unknown>; seq: number | null;
}

/** member-detection：result 必须是恰好含一个已知成员的普通对象，否则返回 null（协议破坏）。 */
function extractMemberEvent(result: unknown): MemberEvent | null {
  if (!isPlainObject(result)) return null;
  const present = MEMBER_KEYS.filter((k) => Object.prototype.hasOwnProperty.call(result, k));
  if (present.length !== 1) return null;   // 零成员或多成员都是协议破坏
  const type = present[0];
  const member = result[type];
  if (!isPlainObject(member)) return null;
  const metadata = isPlainObject(member.metadata) ? member.metadata : {};
  const seq = typeof metadata.seq === 'number' ? metadata.seq : null;
  return { type, member, metadata, seq };
}

/** 非 SSE 响应体处置：带上限读全文，归一为 {message, failure}。
 * 引擎鉴权门/预流错误走 200+application/json 的单一 JSON-RPC 错误信封
 * （没有 HTTP 401/403）——不进这里分类，auth 用户锁（locked-v3）就永远死路。 */
async function nonStreamBodyFailure(
  resp: Response, cap: number,
): Promise<{ message: string; failure: WireFailure }> {
  const reader = (resp.body as ReadableStream<Uint8Array>).getReader();
  const decoder = new TextDecoder();
  let text = '';
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      text += decoder.decode(value, { stream: true });
      if (text.length > cap) {
        await reader.cancel().catch(() => undefined);
        return { message: 'a2a body overflow', failure: { kind: 'protocol', reason: 'body_overflow' } };
      }
    }
    text += decoder.decode();
  } finally {
    reader.releaseLock();
  }
  if (text.trim() === '') {
    return { message: 'a2a empty body', failure: { kind: 'protocol', reason: 'empty_body' } };
  }
  const parsed = parseRpcMessage(text);
  if (!parsed) {
    return { message: 'a2a bad json body', failure: { kind: 'protocol', reason: 'bad_json_body' } };
  }
  if (parsed.error) {
    return { message: parsed.error.message, failure: classifyFailure({ rpcError: parsed.error })! };
  }
  return { message: 'a2a unexpected success body',
    failure: { kind: 'protocol', reason: 'unexpected_success_body' } };
}

export async function* streamRpc(
  rpcUrl: string, request: RpcRequest,
  opts: {
    iamToken?: string; signal?: AbortSignal; fetchImpl?: typeof fetch;
    maxPendingBytes?: number;
  },
): AsyncGenerator<WireEvent> {
  const headers = new Headers({
    'content-type': 'application/json',
    accept: 'text/event-stream',
    ...(opts.iamToken ? { 'x-iam-token': opts.iamToken } : {}),
  });
  let resp: Response;
  try {
    resp = await (opts.fetchImpl ?? fetch)(rpcUrl, {
      method: 'POST', headers, signal: opts.signal,
      body: JSON.stringify(request),
    });
  } catch (err) {
    if (opts.signal?.aborted) return;                  // 主动取消：非失败
    throw Object.assign(new TypeError('a2a transport'), {
      failure: { kind: 'transport' } as WireFailure,
    });
  }
  if (!resp.ok) {
    const failure = classifyFailure({ httpStatus: resp.status });
    throw Object.assign(new Error(`a2a http ${resp.status}`), { failure });
  }
  if (!resp.body) {
    throw Object.assign(new Error('a2a empty body'), { failure: { kind: 'protocol' } });
  }

  const cap = opts.maxPendingBytes ?? MAX_PENDING_BYTES;

  // content-type 分派：真 SSE 才走流；200 + application/json = 鉴权门/预流
  // 错误信封（总对拍事实：引擎拒绝不走 HTTP 401/403，鉴权分类必须在流外生效）
  const contentType = resp.headers.get('content-type') ?? '';
  if (!contentType.includes('text/event-stream')) {
    const { message, failure } = await nonStreamBodyFailure(resp, cap);
    throw typedError(message, failure);
  }

  const reader = (resp.body as ReadableStream<Uint8Array>).getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let eventCount = 0;
  try {
    for (;;) {
      let read: ReadableStreamReadResult<Uint8Array>;
      try {
        read = await reader.read();
      } catch {
        // 读流中断/网络 reset：abort=主动取消（非失败），其余=typed transport 断流
        if (opts.signal?.aborted) return;
        throw Object.assign(new TypeError('a2a stream broken'), {
          failure: { kind: 'transport' } as WireFailure,
        });
      }
      const { done, value } = read;
      if (done) {
        // 兜底：零事件且剩余缓冲恰为 JSON-RPC 错误信封（SSE 头但裸 JSON 收尾）→ 按分类抛
        const leftover = buffer.trim();
        if (eventCount === 0 && leftover) {
          const parsed = parseRpcMessage(leftover);
          if (parsed?.error) {
            throw typedError(parsed.error.message,
              classifyFailure({ rpcError: parsed.error })!);
          }
        }
        // 终态 marker 到达会提前 return——走到这里即"未收到 final 的异常结束"，
        // 上层以 reason 区分异常结束 vs 终态完成（恢复属 M2）
        throw typedError('a2a sse eof without final',
          { kind: 'transport', reason: 'sse_eof_no_final' });
      }
      buffer += decoder.decode(value, { stream: true });
      let idx: number;
      while ((idx = buffer.indexOf(SEP)) >= 0) {
        const rawEvent = buffer.slice(0, idx);
        buffer = buffer.slice(idx + SEP.length);
        for (const line of rawEvent.split('\n')) {
          if (!line.startsWith('data:')) continue;    // 注释/空行忽略
          const parsed = parseRpcMessage(line.slice(5).trim());
          if (!parsed) {
            throw typedError('a2a bad envelope', { kind: 'protocol' });
          }
          if (parsed.id !== request.id) {
            // 信封必须关联本次请求（id:null parse-error 通知也在此被拒）
            throw typedError('a2a envelope id mismatch',
              { kind: 'protocol', reason: 'envelope_id_mismatch' });
          }
          if (parsed.error) {
            throw typedError(parsed.error.message,
              classifyFailure({ rpcError: parsed.error })!);
          }
          const wire = extractMemberEvent(parsed.result);
          if (!wire) {
            throw typedError('a2a bad result',
              { kind: 'protocol', reason: 'unexpected_result_shape' });
          }
          const event: WireEvent = {
            seq: wire.seq, type: wire.type,
            member: wire.member, metadata: wire.metadata,
          };
          yield event;
          eventCount += 1;
          if (event.type === 'statusUpdate' && event.metadata.final === 'true') {
            return;
          }
        }
      }
      if (buffer.length > cap) {
        // 排干后剩余仍越限：畸形/恶意不间断流，丢弃并断流（防内存拖死）
        reader.cancel().catch(() => undefined);
        throw typedError('a2a event overflow',
          { kind: 'protocol', reason: 'event_overflow' });
      }
    }
  } finally {
    reader.releaseLock();
    await (resp.body as ReadableStream<Uint8Array>).cancel().catch(() => undefined);
  }
}