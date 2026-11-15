export interface NormalizedRequestError {
  status?: number;
  message: string;
  apiPath?: string;
  raw: unknown;
}

const DEFAULT_ERROR_MESSAGE = '请求失败，请稍后重试';

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function readString(record: Record<string, unknown>, keys: string[]): string {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === 'string' && value.trim()) return value;
  }
  return '';
}

function pickErrorPayload(error: unknown): unknown {
  if (!isRecord(error)) return error;
  const response = error.response;
  if (isRecord(response) && response.data !== undefined) return response.data;
  if (error.data !== undefined) return error.data;
  return error;
}

export function getErrorStatus(error: unknown): number | undefined {
  if (!isRecord(error)) return undefined;
  const response = error.response;
  const status = isRecord(response) ? response.status : error.status;
  return typeof status === 'number' ? status : undefined;
}

export function formatApiPath(url?: string): string {
  if (!url) return '未知接口';
  try {
    const parsed = /^https?:\/\//i.test(url) ? new URL(url) : undefined;
    if (parsed) return `${parsed.pathname}${parsed.search}` || '/';
  } catch {
    // URL 解析失败时继续走下方兜底，避免错误处理再次抛错。
  }
  const [path] = url.split('#');
  return path || '未知接口';
}

export function extractErrorMessage(error: unknown, fallback = DEFAULT_ERROR_MESSAGE): string {
  if (typeof error === 'string' && error.trim()) return error;

  const payload = pickErrorPayload(error);
  if (!isRecord(payload)) return fallback;

  const directMessage = readString(payload, ['message', 'error', 'msg', 'errorMsg', 'error_msg', 'buserviceErrorMsg']);
  if (directMessage) return directMessage;

  const detail = payload.detail;
  if (typeof detail === 'string' && detail.trim()) return detail;
  if (isRecord(detail)) {
    const detailMessage = readString(detail, ['message', 'error', 'msg']);
    if (detailMessage) return detailMessage;
  }

  const nestedData = payload.data;
  if (isRecord(nestedData)) {
    const nestedMessage = readString(nestedData, ['message', 'error', 'msg', 'errorMsg', 'error_msg']);
    if (nestedMessage) return nestedMessage;
  }

  return fallback;
}

/**
 * 后端 ``ENVELOPE_ERRORS`` 固定输出英文(设计规则:固定 message,不透传异常原文),
 * 用户侧看到的 toast 却需要中文。这里把已知的配额/上限类英文固定串映射为中文,
 * 与后端 ``bot_service._check_bot_count_limit`` 既有中文文案("已达到 Bot 数量上限 ... 请删除部分 Bot 后再创建新的")
 * 同口径,保证创建 Bot 与复制 Bot 超额时提示一致。仅做精确串匹配,不影响其余可读 message。
 */
const BACKEND_MESSAGE_CHINESE_OVERRIDES: Record<string, string> = {
  'Bot quota exceeded for this Space': '已达到当前空间 Bot 数量上限，请删除部分 Bot 后再创建新的。',
  'Bot creation limit reached': '已达到 Bot 数量上限，请删除部分 Bot 后再创建新的。',
};

function overrideBackendMessage(message: string): string {
  return BACKEND_MESSAGE_CHINESE_OVERRIDES[message] ?? message;
}

export function extractFriendlyErrorMessage(error: unknown, fallback = DEFAULT_ERROR_MESSAGE): string {
  const status = getErrorStatus(error);
  const message = extractErrorMessage(error, '');
  const lowerMessage = message.toLowerCase();

  // 网络层故障(无 HTTP 状态、请求未拿到 response):extractErrorMessage 此时会取出 JS 的
  // "Failed to fetch"/"timeout" 等异常串,翻译为友好中文,而不是把英文异常透传给用户。
  // 仅在无 status 时判定,避免覆盖 5xx 里后端显式给出的 message。
  if (status === undefined && /timeout|network error|failed to fetch|err_network/i.test(lowerMessage))
    return '网络异常或请求超时，请检查网络后重试';

  // 有后端可读 message 时优先返回,不被状态相关预设覆盖(核心修复):
  // 后端 { code, message, data, request_id } 的 message 才是给用户看的真实原因。
  // 已知英文固定串(配额/上限类)在此映射为中文,与创建/复制两路 toast 保持一致。
  if (message) return overrideBackendMessage(message);

  // 无可读 message 时,按 HTTP 状态回退预设文案。
  if (status === 401) return '登录已过期，请重新登录';
  if (status === 403) return '当前账号暂无权限执行该操作';
  if (status === 429) return '请求过于频繁，请稍后再试';
  if (status && status >= 500) return '服务器暂时不可用，请稍后重试';

  return fallback;
}

export function normalizeRequestError(
  error: unknown,
  requestUrl?: string,
  fallback = DEFAULT_ERROR_MESSAGE,
): NormalizedRequestError {
  return {
    status: getErrorStatus(error),
    message: extractFriendlyErrorMessage(error, fallback),
    apiPath: formatApiPath(requestUrl),
    raw: error,
  };
}

/** 稳定字符串哈希(djb2),仅用于生成 toastKey,非加密用途。 */
export function hashString(input: string): string {
  let hash = 5381;
  for (let i = 0; i < input.length; i += 1) {
    hash = ((hash << 5) + hash + input.charCodeAt(i)) | 0;
  }
  return (hash >>> 0).toString(16);
}

export interface ToastKeyInput {
  apiPath: string;
  operation?: string;
  message?: string;
}

/**
 * 构建错误提示去重键(global-error-notify-dedup D3):`req:<apiPath>:<operation | hash(message)>`。
 * 含 operation 保证「同接口跨不同操作」的独立失败不被误合并;缺省用 message 哈希兜底,兼容未传 operation 的存量调用。
 * 纯函数、无 React/DOM 依赖,可被 service/协议层(通道 B `backendRequest`、通道 A `requestConfig`)安全引用。
 */
export function buildToastKey({ apiPath, operation, message }: ToastKeyInput): string {
  const tail = operation ?? hashString(message ?? '');
  return `req:${apiPath}:${tail}`;
}
