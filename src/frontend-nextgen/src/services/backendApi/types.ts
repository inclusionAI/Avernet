export interface BackendApiEnvelope<T> {
  success?: boolean;
  code?: string | number;
  message?: string;
  data?: T;
  request_id?: string;
}

/**
 * 判断业务 code 是否落在 HTTP 2xx 成功段(仅 6 位方言)。
 * 后端契约:成功 code = HTTP status × 1000(200000=OK、201000=Created、204000=No Content 等),
 * 故 `floor(code/1000) ∈ [200,299]` 即成功;非 2xx 段(4xx/5xx/其他)视为失败。
 * 不枚举具体错误码——`code` 仅做成败段比较,错误文案取自 `message`。
 *
 * ⚠ 刻意**不**在此并入 BCS 5 位方言(20000–29999):python 域收到 5 位码属跨域误码,
 * 必须按业务失败拒绝(catalog / 私有 Session 语义有紧致性锁定测试)。同时服务两种部署的
 * BCS 域消费方(collaboration 面适配器 / auth 协议边界)用 `isEnvelopeSuccessAnyDialect` 显式并集。
 */
function isHttp2xxBusinessCode(code: string | number | undefined): boolean {
  if (code === undefined || code === null) return false;
  const numeric = typeof code === 'number' ? code : Number(code);
  if (!Number.isFinite(numeric)) return false;
  const status = Math.floor(numeric / 1000);
  return status >= 200 && status <= 299;
}

/**
 * BCS(阿里云 auth + collaboration 面,5 位码方言)成功段判定:
 * 成功 code = HTTP status × 100(实测 20000=OK、20100=Created、20200=Accepted,
 * 见 bcs-api-http `Envelope::success` 用例),即 `floor(code/100) ∈ [200,299]`(20000–29999)。
 * 仅经 `isEnvelopeSuccessAnyDialect` 并集生效,不参与全局默认判定。
 */
function isBcs5DigitBusinessCode(code: string | number | undefined): boolean {
  if (code === undefined || code === null) return false;
  const numeric = typeof code === 'number' ? code : Number(code);
  if (!Number.isFinite(numeric)) return false;
  const status = Math.floor(numeric / 100);
  return status >= 200 && status <= 299;
}

/**
 * 双方言并集成败判定(成功):6 位段(python backend,200000–299999)或 5 位段(BCS,20000–29999)
 * 任一命中即成功。两段无交集([20000,30000) vs [200000,300000)),BCS 错误码 40000–50200
 * (bcs-api-http error.rs 全量映射)与 python 错误码 ≥400000 均不落入任一成功段——任一 code 至多命中一段。
 * 仅供同时服务两种部署形态的消费方使用;纯 python 域(admin/tasks/catalog 等)继续用 `isEnvelopeSuccess`
 * 保持跨域误码紧致拒绝。测试矩阵与零冲突断言见 test/services/backendApi/envelope.test.ts。
 */
export function isEnvelopeSuccessAnyDialect(data: unknown): boolean {
  if (typeof data !== 'object' || data === null) return false;
  const env = data as BackendApiEnvelope<unknown>;
  if (env.success === true) return true;
  return isHttp2xxBusinessCode(env.code) || isBcs5DigitBusinessCode(env.code);
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

/**
 * 业务 code 是否落在「未登录」段(双方言并集):python 6 位 `floor(code/1000) === 401`(401000–401999),
 * BCS 5 位 `floor(code/100) === 401`(40100–40199)。两段无交集,任一 code 至多命中一段(同 envelope.test.ts 矩阵)。
 */
function isUnauthenticatedBusinessCode(code: string | number | undefined): boolean {
  if (code === undefined || code === null) return false;
  const numeric = typeof code === 'number' ? code : Number(code);
  if (!Number.isFinite(numeric)) return false;
  return Math.floor(numeric / 1000) === 401 || Math.floor(numeric / 100) === 401;
}

/**
 * 信封体「未登录」判定(不依赖 HTTP status,双方言并集):
 * - BCS 显式形态:`data.error_code === 'unauthenticated'`(401 反应口既有契约,见 add-external-oauth-login 8.9);
 * - 网关误包形态:HTTP 2xx 但 `code` 落未登录段(实测 40100/401000,见 authApiController 勘探注释)。
 *
 * 与 `isEnvelopeFailure` 的对象语义一致:非对象不算,成功信封(2xx 段)不算。供两条请求通道
 * (通道 B `backendRequest`、通道 A `requestConfig`)统一收口为「登录处置 + 静默上抛」,不投递逐条错误 toast。
 */
export function isEnvelopeUnauthenticated(data: unknown): boolean {
  if (typeof data !== 'object' || data === null) return false;
  if (isEnvelopeSuccess(data)) return false;
  const env = data as BackendApiEnvelope<unknown> & { data?: { error_code?: unknown } };
  if (env.data?.error_code === 'unauthenticated') return true;
  return isUnauthenticatedBusinessCode(env.code);
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
