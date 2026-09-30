// src/flow-control/index.ts

import type { IDatabase } from "../db/types.js";
import type { ApiClient } from "../db/api-client.js";
import type { IFlowControlRepository } from "../db/repositories/types.js";
import type { FlowControlConfig } from "./types.js";
import { SqliteFlowControlRepository } from "./repository.js";
import { FlowControlApiRepository } from "../db/api-repositories/flow-control-api-repository.js";
import { FlowControlService } from "./service.js";
import { FlowControlDispatcher, type DispatcherCallbacks } from "./dispatcher.js";
import { LeaseManager } from "./lease-manager.js";
import { loadInstanceId } from "../credentials.js";

let _flowControlService: FlowControlService | null = null;
let _dispatcher: FlowControlDispatcher | null = null;
let _leaseManager: LeaseManager | null = null;

/**
 * 初始化蓄流子系统。
 * @param db 数据库实例（API 模式下不使用，但保持接口兼容）
 * @param config 已解析的 FlowControlConfig（来自 loadConfig().app.flowControl）
 * @param callbacks 调度器回调（恢复工作流/节点）
 * @param apiClient 可选的 ApiClient，提供时使用 API 模式仓库
 */
export function initFlowControl(
  db: IDatabase,
  config: FlowControlConfig,
  callbacks: DispatcherCallbacks,
  apiClient?: ApiClient,
): FlowControlService | null {
  if (!config.enabled) {
    console.log("[flow-control] disabled by config");
    return null;
  }

  const instanceId = loadInstanceId();
  if (!instanceId) {
    console.warn("[flow-control] cannot determine instance_id (missing ~/.credentials), flow control disabled");
    return null;
  }

  let repo: IFlowControlRepository;
  if (apiClient) {
    repo = new FlowControlApiRepository(apiClient);
    console.log("[flow-control] using API-backed repository (api mode)");
  } else {
    repo = new SqliteFlowControlRepository(db);
    console.log(`[flow-control] using SQLite-backed repository (dbType=${db.dbType})`);
  }

  _flowControlService = new FlowControlService(repo, config, instanceId);
  _dispatcher = new FlowControlDispatcher(repo, config, instanceId, callbacks);
  _dispatcher.start();

  // Start lease heartbeat: renews all active slots every 30s, cleans expired leases
  _leaseManager = new LeaseManager(repo, config, instanceId);
  _leaseManager.start();

  // Diagnostic: log full perWorkflow config so we can verify scope resolution
  const scopeSummary = Object.entries(config.perWorkflow)
    .map(([k, v]) => `${k}: maxConcurrent=${v.maxConcurrent}, queueTimeoutMs=${v.queueTimeoutMs}`)
    .join("; ");
  console.log(
    `[flow-control] initialized (instance: ${instanceId}, repo: ${apiClient ? "API" : "SQLite"}, ` +
    `perWorkflow scopes: ${Object.keys(config.perWorkflow).length} [${scopeSummary}])`,
  );
  return _flowControlService;
}

/** 获取全局 FlowControlService 实例 */
export function getFlowControlService(): FlowControlService | null {
  return _flowControlService;
}

/** 设置全局 FlowControlService 实例（测试用） */
export function setFlowControlService(service: FlowControlService | null): void {
  _flowControlService = service;
}

/** 停止蓄流调度器和租约管理器 */
export function stopFlowControl(): void {
  if (_leaseManager) {
    _leaseManager.stop();
    _leaseManager = null;
  }
  if (_dispatcher) {
    _dispatcher.stop();
    _dispatcher = null;
  }
  _flowControlService = null;
}

export { FlowControlService } from "./service.js";
export { FlowControlDispatcher } from "./dispatcher.js";
export { LeaseManager } from "./lease-manager.js";
export type { DispatcherCallbacks } from "./dispatcher.js";