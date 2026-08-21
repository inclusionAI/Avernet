// src/flow-control/types.ts

/** 蓄流作用域 — 简化后只保留 workflow 级别 */
export type FlowControlScope = "workflow";

/** 作用域键格式: "workflow:{workflowId}" | "workflow:default" */
export type ScopeKey = string;

/** 获取槽位请求选项 */
export interface AcquireOptions {
  /** 作用域类型 */
  scope: FlowControlScope;
  /** 作用域键，格式为 "workflow:{workflowId}" 或 "workflow:default" */
  key: ScopeKey;
  /** 流程实例 ID */
  flowId: string;
  /** 节点 ID（可选，用于工作流级排队时的节点标识） */
  nodeId?: string;
  /** 优先级，数值越小优先级越高（默认 0） */
  priority?: number;
  /** 队列中最大等待时间（毫秒），默认从配置读取 */
  timeoutMs?: number;
  /**
   * 任意 JSON payload，用于附带入队时的上下文信息（如 sessionKey），
   * 以便 dispatcher 恢复时能还原正确的执行环境。
   */
  payload?: string;
  /** 当前 session ID，用于调试追踪 */
  sessionId?: string;
  /** 租约过期时间（Unix秒）。0=旧数据(永不过期)，>0=租约模式 */
  leaseExpiresAt?: number;
}

/** 释放句柄——幂等操作，可安全多次调用 */
export interface ReleaseHandle {
  /** 是否已释放 */
  released: boolean;
  /** 执行释放（幂等） */
  release(): void;
}

/** 获取结果 */
export interface AcquireResult {
  /** true = 已获取槽位; false = 已入队等待 */
  acquired: boolean;
  /** acquired = true 时存在的释放句柄 */
  handle?: ReleaseHandle;
  /** acquired = false 时存在，表示队列位置 */
  queuePosition?: number;
  /** 该请求覆盖的所有作用域结果（已弃用 — 保留向后兼容，tryAcquireMultiple 已移除） */
  scopeResults?: Array<{
    scope: FlowControlScope;
    key: ScopeKey;
    acquired: boolean;
    handle?: ReleaseHandle;
  }>;
}

/** 单个作用域的运行状态 */
export interface ScopeStatus {
  /** 作用域键 */
  key: ScopeKey;
  /** 最大并发数 */
  maxConcurrent: number;
  /** 当前运行数 */
  currentRunning: number;
  /** 排队数 */
  queuedCount: number;
}

/** 全部作用域状态汇总 — 简化后只有 workflows */
export interface FlowControlAllStatus {
  workflows: ScopeStatus[];
}

/** 单个作用域的限流配置 */
export interface ScopeLimitConfig {
  /** 最大并发数，0 表示无限制 */
  maxConcurrent: number;
  /** 队列超时毫秒数 */
  queueTimeoutMs: number;
}

/** 执行器类型限流配置（已弃用 — 保留类型定义用于向后兼容） */
export type PerExecutorConfig = Record<string, ScopeLimitConfig>;

/** 工作流限流配置（索引签名为工作流 ID） */
export type PerWorkflowConfig = Record<string, ScopeLimitConfig>;

/** 调度器配置 */
export interface DispatcherConfig {
  /** 调度器轮询间隔毫秒数 */
  pollIntervalMs: number;
}

/** 蓄流顶层配置 — 简化后只有 perWorkflow + dispatcher */
export interface FlowControlConfig {
  /** 总开关 */
  enabled: boolean;
  /** 按工作流限流 */
  perWorkflow: PerWorkflowConfig;
  /** 调度器配置 */
  dispatcher: DispatcherConfig;
}

/** 排队条目（DB 行映射） */
export interface QueuedItemRow {
  id: number;
  instanceId: string;
  scopeKey: string;
  flowId: string;
  nodeId: string | null;
  priority: number;
  status: string;
  enqueuedAt: number;
  dispatchAfter: number | null;
  expiresAt: number | null;
  payload: string | null;
}

/** 槽位行（DB 行映射） */
export interface SlotRow {
  id: number;
  instanceId: string;
  scopeKey: string;
  flowId: string;
  nodeId: string | null;
  acquiredAt: number;
  /** 持有该 slot 的 session ID，用于调试追踪 */
  sessionId?: string | null;
  /** 租约过期时间（Unix秒）。0=旧数据(永不过期)，>0=租约模式 */
  leaseExpiresAt?: number;
  /** 续租次数，仅用于监控调测 */
  renewCount?: number;
}