/**
 * Fire-and-forget 模块入口。
 *
 * 本模块处理 ClawMind 中所有 fire-and-forget（void 前缀）异步调用的
 * 失败记录和可观测性问题。所有 fire-and-forget 的 .catch() 处理器
 * 应遵循统一模式：console + enqueueRunLog + 计数器。
 */

export {
  recordFailure,
  setEnqueueRunLog,
  report,
  reset,
  getTotal,
  getByCaller,
} from "./failures.js";
export type { RunLogEntry, EnqueueRunLogFn } from "./failures.js";

export { withRetry } from "./retry.js";
export type { RetryOptions } from "./retry.js";