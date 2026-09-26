import type { RuntimeRequestConfig } from '@/adapters/request';
import { defaultRequestAdapter } from '@/adapters/request';
import { resolveAuthFailureDisposition } from '@/services/backendApi/authFailurePolicy';
import { AceLoginRedirectError } from '@/services/backendApi/httpClient';
import { isEnvelopeFailure } from '@/services/backendApi/types';
import { useErrorNotifyStore } from '@/stores/errorNotifyStore';
import { getPlatform } from '@/utils/platform';
import {
  buildToastKey,
  extractErrorMessage,
  extractFriendlyErrorMessage,
  formatApiPath,
  getErrorStatus,
} from '@/utils/requestErrorHandler';
import type { RequestConfig } from '@umijs/max';

export interface TeamClawRequestConfig extends RuntimeRequestConfig {
  skipErrorHandler?: boolean;
  /** 操作语义标签,参与 toastKey 去重键组成(同通道 B `backendRequest`)。 */
  operation?: string;
}

/**
 * 通道 A(umi request)协议层抛出的标准化失败错误,挂去重键与 alreadyHandled,供下游守卫去重。
 * 与通道 B 的 `BackendRequestError` 同构(都有 message/apiPath/toastKey/alreadyHandled),
 * 使 Hook 的 `safeReportError(err)` 可对两通道错误统一处理。
 */
export class RequestProtocolError extends Error {
  status?: number;
  apiPath: string;
  toastKey: string;
  alreadyHandled: true;
  data?: unknown;

  constructor(message: string, options: { status?: number; apiPath: string; toastKey: string; data?: unknown }) {
    super(message);
    this.name = 'RequestProtocolError';
    this.status = options.status;
    this.apiPath = options.apiPath;
    this.toastKey = options.toastKey;
    this.data = options.data;
    this.alreadyHandled = true;
  }
}

interface ReportFailureInput {
  apiPath: string;
  message: string;
  operation?: string;
  status?: number;
  data?: unknown;
}

/**
 * 投递默认提示(`errorNotifyStore.enqueue`)+ 抛 `RequestProtocolError`(挂 toastKey/alreadyHandled)。
 * 守分层:不直接 toast(由顶层观察者 `useErrorNotifyObserver` 消费);与通道 B 一致仅 enqueue 上抛。
 *
 * 未登录处置(与通道 B 对偶,见 `resolveAuthFailureDisposition`):oauth-provider 策略下,
 * - 未登录失败(HTTP 401 / 信封未登录体)→ 弹窗信号已由策略内单飞登记,静默抛 `AceLoginRedirectError`
 *   (与通道 B 同类错误,下游统一识别),不投递逐条错误 toast——未登录 UX 唯一出口是登录弹窗;
 * - 已确认未登录后的其余失败(cidm 500 等)→ 静默抛 `RequestProtocolError`(仍挂 alreadyHandled,
 *   下游 `safeReportError` 也不补发);ace-gateway 一律既有路径。
 */
export function reportProtocolFailure(input: ReportFailureInput): never {
  const disposition = resolveAuthFailureDisposition({ status: input.status, data: input.data });
  if (disposition === 'login-prompt-silent') {
    throw new AceLoginRedirectError();
  }
  const toastKey = buildToastKey({ apiPath: input.apiPath, operation: input.operation, message: input.message });
  if (disposition !== 'silent') {
    useErrorNotifyStore.getState().enqueue({
      toastKey,
      message: input.message,
      apiPath: input.apiPath,
      operation: input.operation,
    });
  }
  throw new RequestProtocolError(input.message, {
    status: input.status,
    apiPath: input.apiPath,
    toastKey,
    data: input.data,
  });
}

/**
 * 响应拦截器:HTTP 2xx 但业务失败(`code` 信封判定)→ 投递默认提示并抛错(**reject**),不再静默 resolve。
 *
 * 语义依据 umi-request(axios):2xx 才进 responseInterceptor;此处抛错会 reject 并路由到 `teamclawErrorHandler`
 * (后者检测 `RequestProtocolError` 不重复投递)。`skipErrorHandler` 透传不处理(调用方自管)。
 */
export function teamclawResponseInterceptor(response: any): any {
  const config = response?.config ?? {};
  if (config.skipErrorHandler) return response;
  if (!isEnvelopeFailure(response?.data)) return response;
  const apiPath = formatApiPath(config.url);
  const message = extractErrorMessage(response.data, '请求失败');
  reportProtocolFailure({
    apiPath,
    message,
    operation: config.operation,
    status: response?.status,
    data: response?.data,
  });
}

/**
 * 错误处理器:HTTP/网络错误(及拦截器抛出的业务失败)的统一收口。
 * - 已由拦截器报告的 `RequestProtocolError`:不重复投递,原样上抛(保持 reject)。
 * - 原始 HTTP/网络错误:投递默认提示 + 上抛 `RequestProtocolError`(修正既有"裸 return 即静默吞没")。
 * - `skipErrorHandler`:不投递不抛错,交 umi 以原 error reject(调用方自管)。
 */
export function teamclawErrorHandler(error: unknown, opts?: { skipErrorHandler?: boolean }): void {
  if (opts?.skipErrorHandler) return;
  if (error instanceof RequestProtocolError) throw error; // 已报告:不重复投递。
  const config = (error as { config?: Record<string, unknown> })?.config ?? {};
  const requestUrl = (config.url as string | undefined) ?? (error as { url?: string })?.url;
  const apiPath = formatApiPath(requestUrl);
  reportProtocolFailure({
    apiPath,
    message: extractFriendlyErrorMessage(error, '请求失败'),
    operation: config.operation as string | undefined,
    status: getErrorStatus(error),
    data: (error as { response?: { data?: unknown } })?.response?.data,
  });
}

export const request: RequestConfig = {
  withCredentials: true,
  requestInterceptors: [
    (config: any) => {
      const adapted = defaultRequestAdapter(config, { platform: getPlatform() });
      return {
        ...adapted,
        headers: {
          ...adapted.headers,
        },
      };
    },
  ],
  responseInterceptors: [teamclawResponseInterceptor],
  errorConfig: {
    errorHandler: teamclawErrorHandler,
  },
};
