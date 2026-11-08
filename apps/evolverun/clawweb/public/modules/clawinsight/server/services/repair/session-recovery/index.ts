import { getCurrentEnv } from "@avernet/clawweb-shared/server/env";
import { SessionRecoveryGroupNotifier, SessionRecoveryNotifier } from "./notification.js";
import { DingTalkWebhookSender } from "../../notifications/dingtalk-webhook-sender.js";
import { AppConfigRepository } from "@avernet/clawevolve/server/repositories/app-config-repository";
import { type IDatabase } from "@avernet/clawweb-shared/server/db";
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
import type { AgentLogTool } from "../common/agent-tools/log-tool.js";
import type { DingTalkNotificationSender } from "../../notifications/dingtalk-sender.js";

export function createSessionRecoveryRuntime(options: {
  db: IDatabase; repo: EvolveRepository; runtime: BotRuntime; targets: BotTargetResolver; ais: AisExecutor;
  publicBaseUrl: string; snapshotId: number; artifactStore: RecoveryArtifactStore;
  notificationSender?: Pick<DingTalkNotificationSender, "sendNotification"> | null; logs?: AgentLogTool; runtimeTool?: AgentRuntimeTool;
  trajectory?: BotSessionTrajectoryReader; nasRoots: string[];
  observationMs?: number; leaseSeconds?: number; launchGraceSeconds?: number;
}): SessionRecoveryRuntime | undefined {
  if (options.db.dbType === "noop") {
    console.warn("[session-recovery] unavailable: task database is not configured");
    return undefined;
  }
  if (!Number.isSafeInteger(options.snapshotId) || options.snapshotId <= 0) {
    console.warn("[session-recovery] unavailable: AIS Snapshot is not configured");
    return undefined;
  }
  const controlPlaneEnvironment = getCurrentEnv() === "prod" ? "prod" : "pre";
  const observationMs = options.observationMs ?? 3600000;
  if (!Number.isSafeInteger(observationMs) || observationMs < 20000) throw new Error("Invalid self-healing waiting deadline");
  const store = new SessionRecoveryTaskRepository(options.db, options.repo, options.artifactStore);
  const notificationConfig = new AppConfigRepository(options.db);
  const actionNotifier = new SessionRecoveryGroupNotifier({ controlPlaneEnvironment, tasks: store,
    config: notificationConfig, sender: new DingTalkWebhookSender(), publicBaseUrl: options.publicBaseUrl });
  const reporter = new HttpRecoveryResultReporter({ baseUrl: options.publicBaseUrl });
  let tasks: SessionRecoveryTaskService;
  const notifier = new SessionRecoveryNotifier(options.notificationSender, options.publicBaseUrl, notificationConfig);
  const agent = new SessionRecoveryAgentService({ controlPlaneEnvironment, tasks: store, repo: options.repo,
    audit: new AgentToolCallRepository(options.db, "session_recovery"), ais: options.ais, runtime: options.runtime,
    targets: options.targets, trajectory: options.trajectory, logs: options.logs, runtimeTool: options.runtimeTool, business: () => tasks, publicBaseUrl: options.publicBaseUrl,
    snapshotId: options.snapshotId, nasRoots: options.nasRoots, leaseSeconds: options.leaseSeconds ?? 120,
    launchGraceSeconds: options.launchGraceSeconds ?? 1800, observationMs, actionNotifier });
  tasks = new SessionRecoveryTaskService({ controlPlaneEnvironment, tasks: store, agent, notifier, reporter, observationMs, actionNotifier,
    handbookConfig: notificationConfig,
    resolveTarget: async request => {
      const expected = { OC: "openclaw", CC: "claude_code", TE: "teclaw" }[request.target.engine];
      const target = await options.targets.resolve({ environment: request.target.env, ownerId: request.target.entity_id, botId: request.target.bot_id, ...(request.target.runtime_identity ? { runtimeIdentity: request.target.runtime_identity } : {}) });
      if (target.environment !== request.target.env || target.ownerId !== request.target.entity_id || target.botId !== request.target.bot_id || target.activeEngine !== expected) throw new SessionRecoveryError(422, "invalid_recovery_target", "目标快照与请求身份或引擎不一致");
      return recoveryTargetSnapshot(target);
    } });
  return { service: tasks, tasks, agent, maintenance: () => tasks.maintenance(), reportResults: () => tasks.reportResults() };
}
