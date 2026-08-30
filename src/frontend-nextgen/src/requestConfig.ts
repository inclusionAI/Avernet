import type { RuntimeRequestConfig } from '@/adapters/request';
import { defaultRequestAdapter } from '@/adapters/request';
import { getPlatform } from '@/utils/platform';
import { extractErrorMessage, formatApiPath, normalizeRequestError } from '@/utils/requestErrorHandler';
import type { RequestConfig } from '@umijs/max';

export interface TeamClawRequestConfig extends RuntimeRequestConfig {
  skipErrorHandler?: boolean;
}

function isBusinessFailure(data: unknown): data is { success: false } {
  return typeof data === 'object' && data !== null && (data as { success?: unknown }).success === false;
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
  responseInterceptors: [
    (response: any) => {
      if (isBusinessFailure(response.data) && !response.config?.skipErrorHandler) {
        const apiPath = formatApiPath(response.config?.url);
        const message = extractErrorMessage(response.data, '请求失败');
        // requestConfig 只做标准化，不弹 toast，避免协议层绑定 UI 反馈。
        return {
          ...response,
          data: {
            ...response.data,
            normalizedError: { apiPath, message },
          },
        };
      }
      return response;
    },
  ],
  errorConfig: {
    errorHandler(error: unknown, opts?: { skipErrorHandler?: boolean }) {
      if (opts?.skipErrorHandler) return;
      const requestUrl =
        typeof error === 'object' && error && 'config' in error
          ? (error as { config?: { url?: string } }).config?.url
          : undefined;
      normalizeRequestError(error, requestUrl);
    },
  },
};
