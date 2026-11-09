// @asset-migrated: teamclaw 自研资产
/**
 * outputEnvelope —— 统一处理 HTTP/任务响应信封剥离与渲染源解析。
 * 节点输出摘要、节点输出整块渲染、下钻会话消息内容均走同一套逻辑，
 * 避免某处未处理而把整段 JSON 信封（data/result 包装层）当文本原样输出。
 */

/** HTTP/任务响应信封的 meta 键：当前层只有 data/result 载荷键 + 这些 meta 键时，才视为"包装层"而非真正 payload。 */
export const ENVELOPE_META_KEYS = new Set([
  'code',
  'message',
  'msg',
  'request_id',
  'requestId',
  'success',
  'ok',
  'gaps',
  'error',
  'error_code',
  'status',
  'errno',
  'ret',
  'ret_msg',
  'total',
]);

/** 剥离 data/result（多层）HTTP 响应信封，定位真正的 payload。
 *  规则：当前层是 plain object，且除 result/data 载荷键外的其余键都是信封 meta（code/message/success/gaps…）时，
 *  优先钻入 result，其次 data；否则视为已到 payload。最多 5 层防坏数据兜底。
 *  兼容 {code,message,data:{result}}、{success,data:{result},gaps}、{result}、{data} 等不同信封形态。 */
export function unwrapHttpEnvelope(value: unknown): unknown {
  let cur = value;
  let depth = 0;
  while (cur && typeof cur === 'object' && !Array.isArray(cur) && depth < 5) {
    const obj = cur as Record<string, unknown>;
    const keys = Object.keys(obj);
    const isEnvelopeBy = (carrier: string) => keys.every((k) => k === carrier || ENVELOPE_META_KEYS.has(k));
    if ('result' in obj && isEnvelopeBy('result')) {
      cur = obj.result;
      depth++;
      continue;
    }
    if ('data' in obj && obj.data !== null && typeof obj.data === 'object' && isEnvelopeBy('data')) {
      cur = obj.data;
      depth++;
      continue;
    }
    break;
  }
  return cur;
}

/** 把结构化对象/数组包成 ```json 代码块，交由 markdown 渲染为代码块（而非把整个 response 原样当文本输出）。 */
export function asJsonCodeBlock(value: unknown): string | null {
  if (value === null || value === undefined || typeof value !== 'object') return null;
  try {
    return '```json\n' + JSON.stringify(value, null, 2) + '\n```';
  } catch {
    return null;
  }
}

/** 统一渲染源解析：节点输出整块渲染、下钻会话消息内容共用。
 *  - 字符串输入：先识别 ```lang 围栏——内部是合法 JSON 则剥围栏后继续信封剥离；非 JSON 围栏保留原样交由 markdown 渲染为代码块；
 *    无围栏的合法裸 JSON（如任务契约 {"success":...,"data":{"result":...}}）则解析后信封剥离；
 *    其余文本/markdown 原样返回。
 *  - 对象输入：直接信封剥离——结果为字符串则当文本/markdown 渲染，对象/数组则包成 ```json 代码块。
 *  - 兜底返回 null，由调用方回退原始内容。 */
export function renderableSource(input: unknown): string | null {
  if (input === null || input === undefined) return null;
  let value: unknown = input;
  if (typeof input === 'string') {
    const s = input.trim();
    if (!s) return null;
    const fence = s.match(/^```[a-zA-Z0-9+#.-]*\n([\s\S]*?)\n?```$/);
    if (fence) {
      try {
        value = JSON.parse(fence[1].trim());
      } catch {
        // 非 JSON 围栏（如 Python/SQL 代码块）：保留围栏原样，交由 markdown 渲染为代码块。
        return s;
      }
    } else {
      try {
        value = JSON.parse(s);
      } catch {
        // 普通 markdown/文本：原样渲染。
        return s;
      }
    }
  }
  const payload = unwrapHttpEnvelope(value);
  if (typeof payload === 'string') return payload.trim() ? payload : null;
  return asJsonCodeBlock(payload);
}
