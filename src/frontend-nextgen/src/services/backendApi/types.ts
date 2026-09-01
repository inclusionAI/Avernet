export interface BackendApiEnvelope<T> {
  success?: boolean;
  code?: string | number;
  message?: string;
  data?: T;
  request_id?: string;
}

/**
 * 判断业务 code 是否落在 HTTP 2xx 成功段。
 * 后端契约:成功 code = HTTP status × 1000(200000=OK、201000=Created、204000=No Content 等),
 * 故 `floor(code/1000) ∈ [200,299]` 即成功;非 2xx 段(4xx/5xx/其他)视为失败。
 * 不枚举具体错误码——`code` 仅做成败段比较,错误文案取自 `message`。
 */
function isHttp2xxBusinessCode(code: string | number | undefined): boolean {
  if (code === undefined || code === null) return false;
  const numeric = typeof code === 'number' ? code : Number(code);
  if (!Number.isFinite(numeric)) return false;
  const status = Math.floor(numeric / 1000);
  return status >= 200 && status <= 299;
}

/**
 * 后端统一信封 `{ code, message, data, request_id }` 的成败判定。
 * 成功:`success === true`(历史信封),或 `code` 落在 2xx 成功段(200000–299999)。
 *
 * 注:故意返回纯 `boolean` 而非类型谓词(`data is BackendApiEnvelope<unknown>`)。
 * 若用「超类型」谓词,`if (isEnvelopeFailure(resp))` 的假分支会把 `resp`(原 `BackendApiEnvelope<T>`)
 * 收窄为 `never`(因为 T 可赋值给 unknown),导致后续 `resp.data` 报错。
 */
export function isEnvelopeSuccess(data: unknown): boolean {
  if (typeof data !== 'object' || data === null) return false;
  const env = data as BackendApiEnvelope<unknown>;
  if (env.success === true) return true;
  return isHttp2xxBusinessCode(env.code);
}

/**
 * 业务失败 = 有信封对象但不是成功信封。
 * 无信封(非对象/null)不算业务失败:非 2xx 已由 fetch 客户端抛 BackendRequestError,
 * 此处只识别「HTTP 2xx 但 code 不在 2xx 成功段」的业务失败。
 */
export function isEnvelopeFailure(data: unknown): boolean {
  if (typeof data !== 'object' || data === null) return false;
  return !isEnvelopeSuccess(data);
}

export interface BackendApiPage<T> {
  items?: T[];
  total?: number;
  page?: number;
  pageSize?: number;
  page_size?: number;
  offset?: number;
  limit?: number;
  cursor?: string;
  hasMore?: boolean;
}

export interface BackendApiErrorBody {
  code?: string;
  message?: string;
  traceId?: string;
}

export type BackendUnknownRecord = Record<string, unknown>;
