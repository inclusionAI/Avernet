import { submittedAisUser } from "@avernet/clawevolve/server/services/ais/ais-task-runner";
import { getCurrentEnv } from "@avernet/clawweb-shared/server/env";
import { logRepairEvent, repairLogError } from "./logging.js";
import { resolveTaskControlPlaneEnvironment, type AgentControlPlaneEnvironment } from "./task-control-plane.js";
import type { IDatabase } from "@avernet/clawweb-shared/server/db";
import type { EvolveRepository } from "@avernet/clawevolve/server/repositories/evolve-repository";
import type { AisExecutor } from "@avernet/clawevolve/server/contracts/ais-executor";
import type { AisStatusHandler } from "@avernet/clawevolve/server/services/ais/status-handler";
import { aisContractForStep } from "@avernet/clawevolve/server/services/ais/ais-artifact-contract";
import { redactPersistableText } from "@avernet/clawweb-shared/server/services/redaction";

/** Recover missing Base callbacks through the same injected business lifecycle handlers. */
export async function reconcileAgentExecutions(deps: {
  controlPlaneEnvironment: AgentControlPlaneEnvironment;
  db: IDatabase; repo: EvolveRepository;
  providers: Readonly<Record<string, Pick<AisExecutor, "getJobStatusDetail" | "stopExecution">>>;
  handlers: Readonly<Record<string, AisStatusHandler>>;
  nowSeconds?: () => number;
}): Promise<void> {
  let cursor = 0;
  for (;;) {
    const rows = await deps.db.query<{ id: number; step_id: string; task_id: string }>(
      "SELECT id, step_id, task_id FROM ce_steps WHERE step_type = 'agent_execution' AND status IN ('created','dispatching','dispatched','running') AND id > ? ORDER BY id ASC LIMIT 100", [cursor]);
    if (!rows.length) return;
    for (const row of rows) {
      cursor = row.id;
      try {
        const task = await deps.repo.findTask(row.task_id), step = await deps.repo.findStep(row.step_id);
        if (!task || !step) continue;
        const config = JSON.parse(task.config_json), contract = aisContractForStep(config, JSON.parse(step.ext_json ?? "{}"), step.step_id);
        if (resolveTaskControlPlaneEnvironment(config) !== deps.controlPlaneEnvironment) continue;
        const name = contract?.aisBase.statusHandler;
        if (typeof name !== "string" || !Object.hasOwn(deps.handlers, name) || !Object.hasOwn(deps.providers, name)) continue;
        const execution = config.execution;
        const current = execution?.executionId === contract?.aisBase.executionId;
        const expired = current && execution.state !== "ended" && execution.leaseExpiresAt <= (deps.nowSeconds?.() ?? Math.floor(Date.now() / 1000));
        const provider = deps.providers[name];
        let reason = expired ? "AGENT_LEASE_EXPIRED" : "";
        let errorMessage = "已保存业务进度与操作审计", retryable = true;
        if (step.bot_run_id) {
          // A temporary query failure is not evidence of business failure.
          const remote = await provider.getJobStatusDetail(step.bot_run_id);
          if (remote.status === "stopped" || remote.status === "success" || (remote.status === "failed" && remote.rawStatus !== "notfound")) {
            reason = "AIS_RESULT_MISSING";
            retryable = remote.status !== "stopped" || expired;
          }
          if (remote.errorMessage) errorMessage = redactPersistableText(remote.errorMessage, 1500);
          if (expired && remote.status === "running") await provider.stopExecution(step.bot_run_id, submittedAisUser(step.bot_response_json)
            ?? (name === "session_recovery" ? task.user_id : task.created_by));
        }
        if (!reason) continue;
        await deps.handlers[name]({ task, step, report: { status: "failed", summary: "本次 Agent 容器执行中断，未收到有效运行回报",
          errorCode: reason, errorMessage, retryable } });
      } catch (error) {
        logRepairEvent("agent-execution-monitor", "agent_execution_monitor", "reconciliation_failed", { environment: deps.controlPlaneEnvironment,
          taskId: row.task_id, stepId: row.step_id, ...repairLogError(error) });
      }
    }
  }
}

export function startAgentExecutionMonitor(deps: Omit<Parameters<typeof reconcileAgentExecutions>[0], "controlPlaneEnvironment"> & { controlPlaneEnvironment?: AgentControlPlaneEnvironment }): () => Promise<void> {
  const controlPlaneEnvironment = deps.controlPlaneEnvironment ?? (getCurrentEnv() === "prod" ? "prod" : "pre");
  let inFlight: Promise<void> | undefined;
  const timer = setInterval(() => {
    if (inFlight) return;
    inFlight = reconcileAgentExecutions({ ...deps, controlPlaneEnvironment }).catch(error => {
      logRepairEvent("agent-execution-monitor", "agent_execution_monitor", "unavailable", { environment: controlPlaneEnvironment, ...repairLogError(error) });
    })
      .finally(() => { inFlight = undefined; });
  }, 20000);
  timer.unref();
  return async () => { clearInterval(timer); await inFlight; };
}
