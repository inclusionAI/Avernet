/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 *
 * retryOnTransient - 瞬时错误（网关超时 / 网络抖动）的指数退避重试
 *
 * 适用于「非关键、可重试」的请求：网关 502/503/504 与无 response 的网络/超时错误
 * 多为间歇性，重试一两次通常即成功。业务错误（401/403/4xx 等）不重试，保持快速失败。
 *
 * 仅做客户端重试，不改动后端与共享 proxy 配置。
 */

/** 视为可重试的瞬时网关状态码 */
const TRANSIENT_STATUS = new Set([502, 503, 504]);

export interface RetryOptions {
  /** 额外重试次数（不含首次），默认 2（即最多请求 3 次） */
  retries?: number;
  /** 退避基数毫秒，默认 400 */
  baseDelayMs?: number;
  /** 退避上限毫秒，默认 2000 */
  maxDelayMs?: number;
}

/** 判断错误是否为可重试的瞬时错误：网关 5xx 超时，或无 response 的网络/超时错误 */
export function isTransientError(err: any): boolean {
  const status = err?.response?.status;
  if (typeof status === 'number') {
    return TRANSIENT_STATUS.has(status);
  }
  // 没有 response：网络层错误（连接超时 / 断网 / DNS 等）
  const code = err?.code || '';
  const message = err?.message || '';
  return (
    code === 'ECONNABORTED' ||
    code === 'ETIMEDOUT' ||
    /timeout|network error|failed to fetch/i.test(message)
  );
}

/**
 * 对 fn 执行带退避的重试。仅在 isTransientError 命中时重试；
 * 非瞬时错误（如 401 未登录）立即抛出，不浪费时间。
 */
export async function retryOnTransient<T>(
  fn: () => Promise<T>,
  opts: RetryOptions = {},
): Promise<T> {
  const { retries = 2, baseDelayMs = 400, maxDelayMs = 2000 } = opts;
  let lastError: any;
  for (let attempt = 0; attempt <= retries; attempt++) {
    try {
      return await fn();
    } catch (err) {
      lastError = err;
      if (attempt === retries || !isTransientError(err)) {
        throw err;
      }
      const delay = Math.min(baseDelayMs * 2 ** attempt, maxDelayMs);
      console.warn(
        `[retryOnTransient] 瞬时错误，${delay}ms 后重试（第 ${
          attempt + 1
        }/${retries} 次）`,
        err?.response?.status || err?.message,
      );
      await new Promise<void>((resolve) => {
        setTimeout(resolve, delay);
      });
    }
  }
  throw lastError;
}
