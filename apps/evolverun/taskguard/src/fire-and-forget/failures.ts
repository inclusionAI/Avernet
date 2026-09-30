/**
 * FireAndForgetFailures — 全局 fire-and-forget 失败计数器模块。
 *
 * 提供统一的错误处理模式：所有 fire-and-forget 异步调用的失败
 * 都通过本模块记录，确保失败可观测。
 *
 * 统一错误处理模式：
 *   .catch((err) => {
 *     // 1. console.warn/error — 即时可见
 *     // 2. enqueueRunLog — 持久化到 run_logs 表
 *     // 3. fireAndForgetFailures.byCaller[callerId]++
 *     // 4. fireAndForgetFailures.total++
 *   });
 */

export type RunLogEntry = {
  flow_id: string;
  node_id?: string | null;
  level: string;
  source: string | null;
  message: string;
  timestamp: number;
};

export type EnqueueRunLogFn = (entry: RunLogEntry) => void;

/** 按调用点分组的失败计数器 */
const byCaller: Record<string, number> = {};
let total = 0;
let lastReportTime = Date.now();
let enqueueRunLogFn: EnqueueRunLogFn | null = null;

const REPORT_INTERVAL_MS = 5 * 60 * 1000; // 5 分钟
const ALERT_THRESHOLD = 10;

/**
 * 设置 enqueueRunLog 函数引用（由 controller 初始化时注入）。
 * 解耦避免循环依赖。
 */
export function setEnqueueRunLog(fn: EnqueueRunLogFn | null): void {
  enqueueRunLogFn = fn;
}

/**
 * 记录一次 fire-and-forget 失败。
 *
 * @param callerId  调用点标识，如 "emitNodeEvent.tracker"、"reconcileStaleRunning"
 * @param flowId    关联的 flow ID（可选，为 "" 时使用占位符）
 * @param nodeId    关联的 node ID（可选）
 * @param error     错误对象或错误消息
 * @param level     日志级别，默认 "warn"
 */
export function recordFailure(
  callerId: string,
  flowId: string,
  nodeId: string | undefined,
  error: unknown,
  level: "warn" | "error" = "warn",
): void {
  total++;
  byCaller[callerId] = (byCaller[callerId] ?? 0) + 1;

  const errMsg = error instanceof Error ? error.message : String(error);
  const message = `[fire-and-forget] ${callerId} failed: ${errMsg}`;

  // 1. console 即时可见
  const logFn = level === "error" ? console.error : console.warn;
  logFn(`[fire-and-forget] caller=${callerId} flowId=${flowId} nodeId=${nodeId ?? "n/a"} error=${errMsg}`);

  // 2. run_logs 持久化
  if (enqueueRunLogFn) {
    enqueueRunLogFn({
      flow_id: flowId || "unknown",
      node_id: nodeId ?? null,
      level,
      source: "fire-and-forget",
      message,
      timestamp: Date.now(),
    });
  }

  // 3. 周期性阈值检查
  maybeReport();
}

/** 检查是否需要输出聚合报告 */
function maybeReport(): void {
  const now = Date.now();
  if (now - lastReportTime < REPORT_INTERVAL_MS) return;
  lastReportTime = now;
  report();
}

/**
 * 输出当前聚合报告到日志。如果总失败次数超过阈值，
 * 额外输出 error 级别告警。
 */
export function report(): void {
  if (total === 0) return;

  const lines: string[] = [`[fire-and-forget] REPORT: total=${total} callers=${Object.keys(byCaller).length}`];
  for (const [caller, count] of Object.entries(byCaller)) {
    lines.push(`  ${caller}: ${count}`);
  }
  const reportStr = lines.join("\n");

  if (total >= ALERT_THRESHOLD) {
    console.error(reportStr);
    if (enqueueRunLogFn) {
      enqueueRunLogFn({
        flow_id: "",
        level: "error",
        source: "fire-and-forget",
        message: `[fire-and-forget] ALERT: ${total} failures in last 5 minutes (threshold=${ALERT_THRESHOLD})`,
        timestamp: Date.now(),
      });
    }
  } else {
    console.warn(reportStr);
  }
}

/** 重置计数器（主要用于测试） */
export function reset(): void {
  total = 0;
  for (const key of Object.keys(byCaller)) {
    delete byCaller[key];
  }
  lastReportTime = Date.now();
}

/** 获取当前总失败次数（主要用于测试） */
export function getTotal(): number {
  return total;
}

/** 获取按调用点分组的失败次数（主要用于测试） */
export function getByCaller(): Record<string, number> {
  return { ...byCaller };
}