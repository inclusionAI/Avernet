// src/flow-control/config.ts

import type { FlowControlConfig, ScopeLimitConfig, PerWorkflowConfig, DispatcherConfig, ScopeKey } from "./types.js";

/** 蓄流配置默认值 — 简化后只有 perWorkflow */
export const FLOW_CONTROL_DEFAULTS: FlowControlConfig = {
  enabled: false,
  perWorkflow: {},
  dispatcher: {
    pollIntervalMs: 1000,
  },
};

/** 默认的单工作流限流配置 */
const DEFAULT_WORKFLOW_LIMIT: ScopeLimitConfig = {
  maxConcurrent: 3,       // 默认每个 workflow 最多 3 个并发
  queueTimeoutMs: 300000, // 5 分钟
};

/**
 * 从 application.yaml 解析的 flowControl 段加载配置。
 * 传入空对象时返回全部默认值。
 *
 * 简化后只支持 perWorkflow scope，不再有 global 和 perExecutor。
 */
export function loadFlowControlConfig(yaml: Record<string, unknown>): FlowControlConfig {
  if (!yaml || typeof yaml !== "object") return { ...FLOW_CONTROL_DEFAULTS };

  const fc = yaml.flowControl ?? yaml;
  if (!fc || typeof fc !== "object") return { ...FLOW_CONTROL_DEFAULTS };

  const raw = fc as Record<string, unknown>;

  const perWorkflow = parsePerScopeConfig(
    raw.perWorkflow as Record<string, unknown> | undefined,
    DEFAULT_WORKFLOW_LIMIT,
  );

  const dispatcher: DispatcherConfig = {
    pollIntervalMs: typeof (raw.dispatcher as Record<string, unknown>)?.pollIntervalMs === "number"
      ? (raw.dispatcher as Record<string, unknown>).pollIntervalMs as number
      : FLOW_CONTROL_DEFAULTS.dispatcher.pollIntervalMs,
  };

  return {
    enabled: typeof raw.enabled === "boolean" ? raw.enabled : FLOW_CONTROL_DEFAULTS.enabled,
    perWorkflow,
    dispatcher,
  };
}

/**
 * 获取指定作用域键的最大并发数。
 * - "workflow:{id}" → perWorkflow[id].maxConcurrent || perWorkflow["default"].maxConcurrent || 0 (无限)
 * - 其他 → 0 (无限)
 */
export function getMaxConcurrentForScope(config: FlowControlConfig, scopeKey: ScopeKey): number {
  if (scopeKey.startsWith("workflow:")) {
    const workflowId = scopeKey.slice("workflow:".length);
    const wfConfig = config.perWorkflow[workflowId];
    if (wfConfig) return wfConfig.maxConcurrent;
    const defaultConfig = config.perWorkflow["default"];
    if (defaultConfig) return defaultConfig.maxConcurrent;
    return DEFAULT_WORKFLOW_LIMIT.maxConcurrent;
  }

  // Unknown scope — unlimited
  return 0;
}

/**
 * 获取指定作用域键的队列超时毫秒数。
 * - "workflow:{id}" → perWorkflow[id].queueTimeoutMs || perWorkflow["default"].queueTimeoutMs || 5min
 * - 其他 → 10min (fallback)
 */
export function getQueueTimeoutMsForScope(config: FlowControlConfig, scopeKey: ScopeKey): number {
  if (scopeKey.startsWith("workflow:")) {
    const workflowId = scopeKey.slice("workflow:".length);
    const wfConfig = config.perWorkflow[workflowId];
    if (wfConfig) return wfConfig.queueTimeoutMs;
    const defaultConfig = config.perWorkflow["default"];
    if (defaultConfig) return defaultConfig.queueTimeoutMs;
    return DEFAULT_WORKFLOW_LIMIT.queueTimeoutMs;
  }

  return FLOW_CONTROL_DEFAULTS.dispatcher.pollIntervalMs * 600; // 10min fallback
}

/** 解析按作用域的限流配置映射 */
function parsePerScopeConfig(
  raw: Record<string, unknown> | undefined,
  defaults: ScopeLimitConfig,
): Record<string, ScopeLimitConfig> {
  if (!raw || typeof raw !== "object") return {};
  const result: Record<string, ScopeLimitConfig> = {};
  for (const [key, value] of Object.entries(raw)) {
    if (!value || typeof value !== "object") continue;
    const v = value as Record<string, unknown>;
    result[key] = {
      maxConcurrent: typeof v.maxConcurrent === "number" ? v.maxConcurrent : defaults.maxConcurrent,
      queueTimeoutMs: typeof v.queueTimeoutMs === "number" ? v.queueTimeoutMs : defaults.queueTimeoutMs,
    };
  }
  return result;
}