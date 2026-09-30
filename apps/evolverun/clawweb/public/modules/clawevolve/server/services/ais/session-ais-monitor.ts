import type { AisExecutor } from "../../contracts/ais-executor.js";
import type { EvolveRepository } from "../../repositories/evolve-repository.js";

type SessionAisBaseConfig = { snapshotId: number; packageId: string; deadlineAt: number };

/** Recover Base-backed session jobs whose terminal callback can no longer arrive. */
export async function reconcileSessionAis(
  repo: EvolveRepository,
  ais: Pick<AisExecutor, "getJobStatusDetail" | "stopExecution">,
  now = Date.now(),
): Promise<void> {
  let cursor = 0;
  for (;;) {
    const tasks = await repo.listActiveSessionAisTasks(cursor, 100);
    if (!tasks.length) return;
    for (const task of tasks) {
      cursor = task.id;
      const config = JSON.parse(task.config_json) as { aisBase?: SessionAisBaseConfig; stepId?: string };
      if (!config.aisBase || !config.stepId) continue;
      const step = await repo.findStep(config.stepId);
      if (!step || ["succeeded", "failed", "canceled"].includes(step.status)) continue;
      const expired = now >= config.aisBase.deadlineAt;
      let reason = expired ? "AIS_DEADLINE_EXCEEDED" : "";
      if (step.bot_run_id) {
        try {
          const remote = await ais.getJobStatusDetail(step.bot_run_id);
          if (remote.status === "stopped" || (remote.status === "failed" && remote.rawStatus !== "notfound")) {
            reason = "AIS_JOB_FAILED";
          } else if (remote.status === "success") {
            reason = "AIS_RESULT_MISSING";
          }
          if (expired && remote.status === "running") await ais.stopExecution(step.bot_run_id);
        } catch {
          // A temporary status-query failure is not evidence that the task failed.
        }
      }
      if (reason) await repo.applySessionAisStatus(task.task_id, step.step_id, {
        status: "failed",
        summary: "AIS 执行未交付有效结果",
        errorCode: reason,
        errorMessage: "AIS Job 已结束或超时，未收到有效结果回调",
        retryable: true,
      });
    }
  }
}

type StopMonitor = () => Promise<void>;
const monitors = new WeakMap<EvolveRepository, StopMonitor>();

export function startSessionAisMonitor(
  repo: EvolveRepository,
  ais: Pick<AisExecutor, "getJobStatusDetail" | "stopExecution">,
): StopMonitor {
  const previous = monitors.get(repo);
  if (previous) return previous;
  let inFlight: Promise<void> | undefined;
  const timer = setInterval(() => {
    if (inFlight) return;
    inFlight = reconcileSessionAis(repo, ais).catch(() => {
      console.error("[session-ais] recovery check failed; will retry");
    }).finally(() => { inFlight = undefined; });
  }, 30_000);
  timer.unref();
  const stop = async () => {
    clearInterval(timer);
    await inFlight;
    if (monitors.get(repo) === stop) monitors.delete(repo);
  };
  monitors.set(repo, stop);
  return stop;
}
