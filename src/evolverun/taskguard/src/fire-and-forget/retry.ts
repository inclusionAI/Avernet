/**
 * withRetry — 通用异步重试工具。
 *
 * 为 fire-and-forget 调用提供指数退避重试能力：
 *  - 最多重试 maxRetries 次
 *  - 每次重试间隔为 baseDelayMs * 2^(attempt-1)
 *  - 所有重试耗尽后调用 onExhausted 回调
 *
 * 典型用法（在同步上下文中 fire-and-forget）：
 *   void withRetry(
 *     () => repo.someOperation(flowId),
 *     { callerId: "someOperation", flowId, nodeId },
 *     (err) => { console.error(...); enqueueRunLog(...); recordFailure(...); },
 *   );
 *
 * 典型用法（在异步上下文中 await）：
 *   await withRetry(
 *     () => repo.someOperation(flowId),
 *     { callerId: "someOperation", flowId, nodeId },
 *   );
 */

export type RetryOptions = {
  /** 最多重试次数（不包括第一次尝试），默认 3 */
  maxRetries?: number;
  /** 基础退避延迟（毫秒），默认 200 */
  baseDelayMs?: number;
  /** 调用点标识，用于日志 */
  callerId?: string;
  /** 关联的 flow ID */
  flowId?: string;
  /** 关联的 node ID */
  nodeId?: string;
};

/**
 * 执行异步操作并自动重试。
 *
 * @param fn          要执行的异步操作
 * @param options     重试选项
 * @param onExhausted 所有重试耗尽后的回调（可选），参数为最后一次错误
 * @returns           操作成功时返回结果，所有重试耗尽时抛出最后一次错误
 */
export async function withRetry<T>(
  fn: () => Promise<T>,
  options: RetryOptions = {},
  onExhausted?: (err: unknown) => void,
): Promise<T> {
  const { maxRetries = 3, baseDelayMs = 200, callerId = "unknown", flowId, nodeId } = options;

  let lastError: unknown;

  for (let attempt = 0; attempt <= maxRetries; attempt++) {
    try {
      return await fn();
    } catch (err) {
      lastError = err;
      const isLast = attempt === maxRetries;
      const errMsg = err instanceof Error ? err.message : String(err);

      if (isLast) {
        console.warn(`[retry] ${callerId} exhausted after ${maxRetries + 1} attempts: flowId=${flowId} nodeId=${nodeId ?? "n/a"} error=${errMsg}`);
        if (onExhausted) {
          onExhausted(err);
        }
        throw err;
      }

      const delay = baseDelayMs * Math.pow(2, attempt);
      console.warn(`[retry] ${callerId} attempt ${attempt + 1}/${maxRetries + 1} failed, retrying in ${delay}ms: flowId=${flowId} nodeId=${nodeId ?? "n/a"} error=${errMsg}`);
      await sleep(delay);
    }
  }

  throw lastError; // unreachable, but TypeScript needs it
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}