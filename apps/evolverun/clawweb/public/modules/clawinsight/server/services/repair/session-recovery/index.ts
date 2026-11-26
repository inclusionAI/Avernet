import { getCurrentEnv } from "@avernet/clawweb-shared/server/env";
import { SessionRecoveryGroupNotifier, SessionRecoveryNotifier } from "./notification.js";
import { DingTalkWebhookSender } from "../../notifications/dingtalk-webhook-sender.js";
import { AppConfigRepository } from "@avernet/clawevolve/server/repositories/app-config-repository";
import { BotDiagnosisRecoveryConfigRepository } from '../../../repositories/bot-diagnosis-recovery-config-repository.js';
import { requireRecoveryEnabled } from './bot-configuration.js';
import { type IDatabase } from "@avernet/clawweb-shared/server/db";
import { DistributedLockRepository } from "@avernet/clawweb-shared/server/repositories/distributed-lock-repository";
import { DistributedLockService, DistributedLeaseLostError } from "@avernet/clawweb-shared/server/services/distributed-lock";
import type { EvolveRepository } from "@avernet/clawevolve/server/repositories/evolve-repository";
import type { AisExecutor } from "@avernet/clawevolve/server/contracts/ais-executor";
import { SessionRecoveryTaskRepository, type RecoveryArtifactStore } from "../../../repositories/repair/session-recovery-task-repository.js";
import { AgentToolCallRepository } from "../../../repositories/repair/agent-tool-call-repository.js";
import type { BotRuntime, BotTargetResolver, BotSessionTrajectoryReader } from "@avernet/clawweb-shared/server/services/bot-runtime";
import { SessionRecoveryTaskService } from "./task-service.js";
import { SessionRecoveryAgentService, recoveryTargetSnapshot } from "./agent-service.js";
import { HttpRecoveryResultReporter } from "./result-reporter.js";

import type { SessionRecoveryRuntime } from "./contracts.js";
import type { AgentRuntimeTool } from "../common/agent-tools/runtime-tool.js";
import { SessionRecoveryError } from "./errors.js";
import { logRepairEvent, repairLogError } from "../common/logging.js";
import type { AgentLogTool } from "../common/agent-tools/log-tool.js";
import type { RecoveryNotificationSender } from "./notification.js";

export const SESSION_RECOVERY_MAINTENANCE_INTERVAL_MS = 60_000;

export function createSessionRecoveryRuntime(options: {
  db: IDatabase; repo: EvolveRepository; runtime: BotRuntime; targets: BotTargetResolver; ais: AisExecutor;
  publicBaseUrl: string; snapshotId: number; artifactStore: RecoveryArtifactStore;
  notificationSender?: RecoveryNotificationSender | null; logs?: AgentLogTool; runtimeTool?: AgentRuntimeTool;
  resolveSessionUrl?: ConstructorParameters<typeof SessionRecoveryNotifier>[5];
  trajectory?: BotSessionTrajectoryReader; nasRoots: string[];
  observationMs?: number; leaseSeconds?: number; launchGraceSeconds?: number;
  resolveModelApiKey?: (reference: string) => Promise<string>;
  canReadAsBotCollaborator?: ConstructorParameters<typeof SessionRecoveryTaskService>[0]["canReadAsBotCollaborator"];
}): SessionRecoveryRuntime | undefined {
  if (options.db.dbType === "noop") {
    logRepairEvent("session-recovery", "session_recovery_runtime", "unavailable", { reason: "task database is not configured" });
    return undefined;
  }
  if (!Number.isSafeInteger(options.snapshotId) || options.snapshotId <= 0) {
    logRepairEvent("session-recovery", "session_recovery_runtime", "unavailable", { reason: "AIS Snapshot is not configured" });
    return undefined;
  }
  const controlPlaneEnvironment = getCurrentEnv() === "prod" ? "prod" : "pre";
  const observationMs = options.observationMs ?? 3600000;
  if (!Number.isSafeInteger(observationMs) || observationMs < 20000) throw new Error("Invalid self-healing waiting deadline");
  const store = new SessionRecoveryTaskRepository(options.db, options.repo, options.artifactStore);
  const notificationConfig = new AppConfigRepository(options.db);
  const botConfigurations = new BotDiagnosisRecoveryConfigRepository(options.db);
  const actionNotifier = new SessionRecoveryGroupNotifier({ controlPlaneEnvironment, tasks: store,
    config: notificationConfig, botConfigurations, sender: new DingTalkWebhookSender(), publicBaseUrl: options.publicBaseUrl });
  const reporter = new HttpRecoveryResultReporter({ baseUrl: options.publicBaseUrl });
  let tasks: SessionRecoveryTaskService;
  const notifier = new SessionRecoveryNotifier(options.notificationSender, options.publicBaseUrl, notificationConfig, controlPlaneEnvironment, botConfigurations, options.resolveSessionUrl);
  const agent = new SessionRecoveryAgentService({ controlPlaneEnvironment, tasks: store, repo: options.repo,
    audit: new AgentToolCallRepository(options.db, "session_recovery"), ais: options.ais, runtime: options.runtime,
    targets: options.targets, trajectory: options.trajectory, logs: options.logs, runtimeTool: options.runtimeTool, business: () => tasks, publicBaseUrl: options.publicBaseUrl,
    snapshotId: options.snapshotId, nasRoots: options.nasRoots, leaseSeconds: options.leaseSeconds ?? 120,
    launchGraceSeconds: options.launchGraceSeconds ?? 1800, observationMs, resolveModelApiKey: options.resolveModelApiKey, actionNotifier });
  tasks = new SessionRecoveryTaskService({ controlPlaneEnvironment, tasks: store, agent, notifier, reporter, observationMs, actionNotifier,
    handbookConfig: notificationConfig, botConfigurations, canReadAsBotCollaborator: options.canReadAsBotCollaborator,
    authorizeCreation: request => requireRecoveryEnabled(botConfigurations, request, notificationConfig),
    resolveTarget: async request => {
      const expected = { OC: "openclaw", CC: "claude_code", TE: "teclaw" }[request.target.engine];
      const target = await options.targets.resolve({ environment: request.target.env, ownerId: request.target.entity_id, botId: request.target.bot_id, ...(request.target.runtime_identity ? { runtimeIdentity: request.target.runtime_identity } : {}) });
      if (target.environment !== request.target.env || target.ownerId !== request.target.entity_id || target.botId !== request.target.bot_id || target.activeEngine !== expected) throw new SessionRecoveryError(422, "invalid_recovery_target", "目标快照与请求身份或引擎不一致");
      return recoveryTargetSnapshot(target);
    } });
  const maintenanceLocks = new DistributedLockService(new DistributedLockRepository(options.db));
  const maintenance = async (): Promise<void> => {
    let startedAtMs: number | undefined;
    let context: { environment: string; stateVersion?: number } = { environment: controlPlaneEnvironment };
    try {
      await maintenanceLocks.withLease({
        environment: controlPlaneEnvironment, scene: "session_recovery_maintenance", tenantId: "__global__", resourceKey: "scan",
        leaseMs: 120_000, renewEveryMs: 20_000, minIntervalMs: SESSION_RECOVERY_MAINTENANCE_INTERVAL_MS,
      }, async lease => {
        startedAtMs = Date.now();
        context = { environment: controlPlaneEnvironment, stateVersion: lease.version };
        lease.assertHeld();
        logRepairEvent("session-recovery", "session_recovery_maintenance", "scan_started", context);
        await tasks.maintenance(() => lease.assertHeld());
        lease.assertHeld();
        // Scan completion is recorded while ownership is held, before release.
        logRepairEvent("session-recovery", "session_recovery_maintenance", "scan_completed", {
          ...context, outcome: "succeeded", elapsedMs: Date.now() - startedAtMs,
        });
      });
    } catch (error) {
      logRepairEvent("session-recovery", "session_recovery_maintenance",
        error instanceof DistributedLeaseLostError ? "lease_lost" : "maintenance_failed", {
          ...context, outcome: "failed", ...(startedAtMs === undefined ? {} : { elapsedMs: Date.now() - startedAtMs }),
          ...repairLogError(error),
        });
      throw error;
    }
  };
  return { service: tasks, tasks, agent, maintenance, dispose: () => tasks.dispose() };
}
