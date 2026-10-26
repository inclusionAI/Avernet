import { getCurrentEnv } from "@avernet/clawweb-shared/server/env";
import { resolveTaskControlPlaneEnvironment, type AgentControlPlaneEnvironment } from "./task-control-plane.js";
import type { IDatabase } from "@avernet/clawweb-shared/server/db";
import type { EvolveRepository } from "@avernet/clawevolve/server/repositories/evolve-repository";
import type { AisExecutor } from "@avernet/clawevolve/server/contracts/ais-executor";
import type { AisStatusHandler } from "@avernet/clawevolve/server/services/ais/status-handler";
import { aisContractForStep } from "@avernet/clawevolve/server/services/ais/ais-artifact-contract";
import { redactPersistableText } from "@avernet/clawweb-shared/server/services/redaction";
import { startRepairBackgroundJob, type RepairScanCache } from "./background-job.js";

/** Recover missing Base callbacks through the same injected business lifecycle handlers. */
export async function reconcileAgentExecutions(deps: {
  controlPlaneEnvironment: AgentControlPlaneEnvironment;
  db: IDatabase; repo: EvolveRepository;
  providers: Readonly<Record<string, Pick<AisExecutor, "getJobStatusDetail" | "stopExecution">>>;
  handlers: Readonly<Record<string, AisStatusHandler>>;
  nowSeconds?: () => number;
  isActive?: () => Promise<boolean>;
}): Promise<void> {
  const isActive = deps.isActive ?? (async () => true);
  const pending = new Set<Promise<void>>();
  let cursor = 0;
  const check = async (row: { task_id: string; step_id: string }) => {
    try {
      if (!await isActive()) return;
      const task = await deps.repo.findTask(row.task_id), step = await deps.repo.findStep(row.step_id);
      if (!task || !step || ["succeeded", "failed", "canceled"].includes(step.status)) return;
      const config = JSON.parse(task.config_json), contract = aisContractForStep(config, JSON.parse(step.ext_json ?? "{}"), step.step_id);
      if (resolveTaskControlPlaneEnvironment(config) !== deps.controlPlaneEnvironment) return;
      const name = contract?.aisBase.statusHandler;
      if (typeof name !== "string" || !Object.hasOwn(deps.handlers, name) || !Object.hasOwn(deps.providers, name)) return;
      const execution = config.execution;
      const current = execution?.executionId === contract?.aisBase.executionId;
      const expired = current && execution.state !== "ended" && execution.leaseExpiresAt <= (deps.nowSeconds?.() ?? Math.floor(Date.now() / 1000));
      const provider = deps.providers[name];
      let reason = expired ? "AGENT_LEASE_EXPIRED" : "";
      let errorMessage = "已保存业务进度与操作审计", retryable = true;
      if (step.bot_run_id) {
        if (!await isActive()) return;
        // A temporary query failure is not evidence of business failure.
        const remote = await provider.getJobStatusDetail(step.bot_run_id);
        if (remote.status === "stopped" || remote.status === "success" || (remote.status === "failed" && remote.rawStatus !== "notfound")) {
          reason = "AIS_RESULT_MISSING";
          retryable = remote.status !== "stopped" || expired;
        }
        if (remote.errorMessage) errorMessage = redactPersistableText(remote.errorMessage, 1500);
        if (expired && remote.status === "running") {
          if (!await isActive()) return;
          const latest = await deps.repo.findTask(row.task_id);
          const latestConfig = latest && JSON.parse(latest.config_json);
          if (!latestConfig || resolveTaskControlPlaneEnvironment(latestConfig) !== deps.controlPlaneEnvironment) return;
          const latestExecution = latestConfig.execution;
          if (!latestExecution || latestExecution.executionId !== execution.executionId || latestExecution.state === "ended"
            || latestExecution.leaseExpiresAt > (deps.nowSeconds?.() ?? Math.floor(Date.now() / 1000))) return;
          if (!await isActive()) return;
          await provider.stopExecution(step.bot_run_id);
        }
      }
      if (!reason || !await isActive()) return;
      await deps.handlers[name]({ task, step, report: { status: "failed", summary: "本次 Agent 容器执行中断，未收到有效运行回报",
        errorCode: reason, errorMessage, retryable } });
    } catch { console.error("[agent-execution] reconciliation failed; will retry", row.task_id, row.step_id); }
  };
  try {
    while (await isActive()) {
      const rows = await deps.db.query<{ id: number; step_id: string; task_id: string }>(
        "SELECT id, step_id, task_id FROM ce_steps WHERE step_type = 'agent_execution' AND status IN ('created','dispatching','dispatched','running') AND id > ? ORDER BY id ASC LIMIT 100", [cursor]);
      if (!rows.length) break;
      for (const row of rows) {
        if (!await isActive()) return;
        cursor = row.id;
        const work = check(row).finally(() => { pending.delete(work); });
        pending.add(work);
        if (pending.size >= 4) await Promise.race(pending);
      }
    }
  } finally { await Promise.all(pending); }
}

export function startAgentExecutionMonitor(deps: Omit<Parameters<typeof reconcileAgentExecutions>[0], "controlPlaneEnvironment"> & { controlPlaneEnvironment?: AgentControlPlaneEnvironment; cache?: RepairScanCache }): () => Promise<void> {
  const controlPlaneEnvironment = deps.controlPlaneEnvironment ?? (getCurrentEnv() === "prod" ? "prod" : "pre");
  return startRepairBackgroundJob({ controlPlaneEnvironment, job: "agent_execution", cache: deps.cache,
    run: isActive => reconcileAgentExecutions({ ...deps, controlPlaneEnvironment, isActive }) });
}
