/**
 * 历史消息 Mapper 共用 helper（sprint workspaceMessageMapper 与本分支 groupMessageMapper 共用）。
 * 两个 mapper 入口函数因输入 DTO shape 不同（PrivateSessionRawMessage vs SessionMessageData）保持独立，
 * 仅在此处共享纯函数工具，避免 ~80 行重复实现。
 */

/** 把后端 content（string | string[] | {text} | {text}[]）规范化为字符串。 */
export function extractMessageContent(content: unknown): string {
  if (typeof content === 'string') return content;
  if (content === null || content === undefined) return '';
  if (Array.isArray(content)) {
    return content
      .map((item) => {
        if (typeof item === 'string') return item;
        if (item && typeof item === 'object' && 'text' in item) {
          return String((item as { text?: unknown }).text ?? '');
        }
        return '';
      })
      .filter(Boolean)
      .join('\n');
  }
  if (typeof content === 'object' && 'text' in content) {
    return String((content as { text?: unknown }).text ?? '');
  }
  return '';
}

/**
 * 把任意原始时间戳（数字或可解析字符串，秒/毫秒自适应）转毫秒。
 * - 数字 < 1e12 视为秒级 → ×1000
 * - 字符串：先 Number()，若得有限数则按数字规则；否则 Date.parse
 * - 无法解析返回 undefined
 */
export function normalizeTimestamp(raw: unknown): number | undefined {
  if (typeof raw === 'number') return raw < 10_000_000_000 ? raw * 1000 : raw;
  if (typeof raw === 'string') {
    const numeric = Number(raw);
    if (Number.isFinite(numeric) && raw.trim()) return numeric < 10_000_000_000 ? numeric * 1000 : numeric;
    const parsed = Date.parse(raw);
    if (Number.isFinite(parsed)) return parsed;
  }
  return undefined;
}

/** tool_result 元信息形状（来自 PrivateSessionRawMessage.metadata 或 SessionMessageData.metadata）。 */
export interface ToolResultMeta {
  tool_name?: unknown;
  tool_call_id?: unknown;
  arguments?: unknown;
  result?: unknown;
  success?: unknown;
  status?: unknown;
  error?: unknown;
  is_error?: unknown;
}

/**
 * 判定 tool_result 是否为错误形态 —— 对齐 demo §8.4 五类 error 信号：
 * 1. success === false
 * 2. status === 'error'
 * 3. error 存在
 * 4. result.status === 'error'（result 为对象时）
 * 5. result.error 存在（result 为对象时）
 */
export function isToolError(meta: ToolResultMeta): boolean {
  if (meta.success === false) return true;
  if (meta.status === 'error') return true;
  if (Boolean(meta.error)) return true;
  if (
    meta.result &&
    typeof meta.result === 'object' &&
    'status' in meta.result &&
    (meta.result as { status?: unknown }).status === 'error'
  ) {
    return true;
  }
  if (
    meta.result &&
    typeof meta.result === 'object' &&
    'error' in meta.result &&
    Boolean((meta.result as { error?: unknown }).error)
  ) {
    return true;
  }
  return false;
}

/** 把 tool 输入/输出值规范化为展示字符串：undefined/null → undefined；string 原样；对象 JSON 序列化。 */
export function stringifyToolValue(value: unknown): string | undefined {
  if (value === undefined || value === null) return undefined;
  if (typeof value === 'string') return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

/**
 * 从 DTO metadata.history_meta 上读取 conversationRoundId。
 * 注意：已映射的 ChatMessage 上的 roundId 由 mapper 写入 extra.conversationRoundId，
 * 此 helper 只负责原始 DTO 侧的读取。
 */
export function readConversationRoundId(
  meta?: { history_meta?: { conversationRoundId?: string } } | null,
): string | undefined {
  if (!meta || typeof meta !== 'object') return undefined;
  const roundId = meta.history_meta?.conversationRoundId;
  if (roundId === undefined || roundId === null) return undefined;
  return String(roundId);
}
