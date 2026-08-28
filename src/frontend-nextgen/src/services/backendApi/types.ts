export interface BackendApiEnvelope<T> {
  success?: boolean;
  code?: string | number;
  message?: string;
  data?: T;
  request_id?: string;
}

/**
 * 后端统一信封 `{ code, message, data, request_id }` 的成败判定。
 * 成功哨兵:`code === 200000`(兼容历史 `success: true` 信封);其余一律视为业务失败。
 * 不枚举具体 `code` 错误类别——`code` 仅用于成败哨兵比较,错误文案取自 `message`。
 *
 * 注:故意返回纯 `boolean` 而非类型谓词(`data is BackendApiEnvelope<unknown>`)。
 * 若用「超类型」谓词,`if (isEnvelopeFailure(resp))` 的假分支会把 `resp`(原 `BackendApiEnvelope<T>`)
 * 收窄为 `never`(因为 T 可赋值给 unknown),导致后续 `resp.data` 报错。
 */
export function isEnvelopeSuccess(data: unknown): boolean {
  if (typeof data !== 'object' || data === null) return false;
  const env = data as BackendApiEnvelope<unknown>;
  if (env.success === true) return true;
  return env.code === 200000 || env.code === '200000';
}

/**
 * 业务失败 = 有信封对象但不是成功信封。
 * 无信封(非对象/null)不算业务失败:非 2xx 已由 fetch 客户端抛 BackendRequestError,
 * 此处只识别「HTTP 2xx 但 code !== 200000」的业务失败。
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
