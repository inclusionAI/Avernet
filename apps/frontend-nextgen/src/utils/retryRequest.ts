import { getErrorStatus } from './requestErrorHandler';

export interface RetryOptions {
  retries?: number;
  baseDelayMs?: number;
  maxDelayMs?: number;
  shouldRetry?: (error: unknown, attempt: number) => boolean;
}

const TRANSIENT_STATUS = new Set([502, 503, 504]);

function readErrorMessage(error: unknown): string {
  if (error instanceof Error) return error.message;
  if (typeof error === 'string') return error;
  if (typeof error === 'object' && error && 'message' in error) {
    const message = (error as { message?: unknown }).message;
    return typeof message === 'string' ? message : '';
  }
  return '';
}

export function isTransientError(error: unknown): boolean {
  const status = getErrorStatus(error);
  if (status) return TRANSIENT_STATUS.has(status);
  return /timeout|network error|failed to fetch|err_network/i.test(readErrorMessage(error));
}

function wait(ms: number): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, ms);
  });
}

export async function retryOnTransient<T>(fn: () => Promise<T>, options: RetryOptions = {}): Promise<T> {
  const { retries = 2, baseDelayMs = 400, maxDelayMs = 2000, shouldRetry = isTransientError } = options;
  let lastError: unknown;

  for (let attempt = 0; attempt <= retries; attempt += 1) {
    try {
      return await fn();
    } catch (error) {
      lastError = error;
      if (attempt >= retries || !shouldRetry(error, attempt)) break;
      // 使用指数退避降低网关瞬时错误对后端的二次冲击。
      const delayMs = Math.min(maxDelayMs, baseDelayMs * 2 ** attempt);
      await wait(delayMs);
    }
  }

  throw lastError;
}
