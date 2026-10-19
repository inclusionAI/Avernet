import {
  resolveBaasConfig,
  resolveInsightHandoffConfig,
  resolveRepairServiceConfig,
  type ResolvedBaasConfig,
} from "@avernet/clawweb-shared/server/db";
import { getCurrentEnv } from "@avernet/clawweb-shared/server/env";
import type { RepairControlPlaneEnvironment, RepairTaskConfig } from "./contracts.js";
import { resolveTaskControlPlaneEnvironment } from "../common/task-control-plane.js";

/** Preserve legacy snapshot selection while sharing strict ownership resolution with background workers. */
export function resolveRepairTaskControlPlaneEnvironment(
  task: Pick<RepairTaskConfig, "controlPlaneEnvironment" | "publicBaseUrl">,
  fallback: RepairControlPlaneEnvironment,
): RepairControlPlaneEnvironment {
  return resolveTaskControlPlaneEnvironment(task) ?? fallback;
}

export type RepairConfig = {
  publicBaseUrl: string;
  /** Standard Base package; absent only for legacy/test compositions. */
  packageId?: string;
  controlPlaneEnvironment: RepairControlPlaneEnvironment;
  aisSnapshotIds: Record<RepairControlPlaneEnvironment, number>;
  decisionGraceSeconds: number;
  contextWaitSeconds: number;
  executionLeaseSeconds: number;
  heartbeatIntervalSeconds: number;
  agentTimeoutSeconds: number;
  agentCloseoutTimeoutSeconds: number;
  maxAgentAutoRecoveries: number;
  agentCorrectionTimeoutSeconds: number;
  maxAgentOutputCorrectionRetries: number;
  maxAgentRateLimitRetries: number;
  agentRateLimitRetryBaseSeconds: number;
  baas: ResolvedBaasConfig;
  requestTimeoutMs: number;
};

export function resolveRepairConfig(
  aisSnapshotIds: Record<RepairControlPlaneEnvironment, number>,
  configPath?: string,
): RepairConfig {
  const repair = resolveRepairServiceConfig(configPath);
  const currentEnvironment = getCurrentEnv();
  const resolved: RepairConfig = {
    publicBaseUrl: resolveInsightHandoffConfig().publicBaseUrl,
    packageId: "clawevolve-repair",
    controlPlaneEnvironment: currentEnvironment === "prod" ? "prod" : "pre",
    aisSnapshotIds,
    decisionGraceSeconds: repair.decisionGraceSeconds,
    contextWaitSeconds: repair.contextWaitSeconds,
    executionLeaseSeconds: repair.executionLeaseSeconds,
    heartbeatIntervalSeconds: 20,
    agentTimeoutSeconds: repair.agentTimeoutSeconds,
    agentCloseoutTimeoutSeconds: repair.agentCloseoutTimeoutSeconds,
    maxAgentAutoRecoveries: repair.maxAgentAutoRecoveries,
    agentCorrectionTimeoutSeconds: repair.agentCorrectionTimeoutSeconds,
    maxAgentOutputCorrectionRetries: repair.maxAgentOutputCorrectionRetries,
    maxAgentRateLimitRetries: repair.maxAgentRateLimitRetries,
    agentRateLimitRetryBaseSeconds: repair.agentRateLimitRetryBaseSeconds,
    baas: resolveBaasConfig(configPath),
    requestTimeoutMs: repair.requestTimeoutMs,
  };
  for (const [name, value] of Object.entries({
    decisionGraceSeconds: resolved.decisionGraceSeconds,
    contextWaitSeconds: resolved.contextWaitSeconds,
    executionLeaseSeconds: resolved.executionLeaseSeconds,
    heartbeatIntervalSeconds: resolved.heartbeatIntervalSeconds,
  })) {
    if (!Number.isSafeInteger(value) || value <= 0) throw new Error(`repair.${name} 必须是正整数`);
  }
  if (resolved.heartbeatIntervalSeconds >= resolved.executionLeaseSeconds) {
    throw new Error("repair.heartbeatIntervalSeconds 必须小于 executionLeaseSeconds");
  }
  for (const [name, value, minimum, maximum] of [
    ["agentTimeoutSeconds", resolved.agentTimeoutSeconds, 60, 7_200],
    ["agentCloseoutTimeoutSeconds", resolved.agentCloseoutTimeoutSeconds, 30, 1_800],
    ["maxAgentAutoRecoveries", resolved.maxAgentAutoRecoveries, 1, 3],
    ["agentCorrectionTimeoutSeconds", resolved.agentCorrectionTimeoutSeconds, 60, 300],
    ["maxAgentOutputCorrectionRetries", resolved.maxAgentOutputCorrectionRetries, 1, 3],
    ["maxAgentRateLimitRetries", resolved.maxAgentRateLimitRetries, 0, 3],
    ["agentRateLimitRetryBaseSeconds", resolved.agentRateLimitRetryBaseSeconds, 1, 60],
  ] as const) {
    if (!Number.isSafeInteger(value) || value < minimum || value > maximum) {
      throw new Error(`repair.${name} 必须是 ${minimum} 到 ${maximum} 之间的整数`);
    }
  }
  if (resolved.agentTimeoutSeconds < resolved.contextWaitSeconds + 5 * 60) {
    throw new Error(
      "repair.agentTimeoutSeconds 必须至少比 contextWaitSeconds 多 300 秒",
    );
  }
  return resolved;
}
