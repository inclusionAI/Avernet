import type { RunAnalysisTimeoutRepositoryPort } from "../../ports/evolve-repository.js";

export const RUN_ANALYSIS_TIMEOUT_MS = 30 * 60 * 1000;
export const RUN_ANALYSIS_SWEEP_INTERVAL_MS = 5 * 60 * 1000;

export type RunAnalysisTimeoutLogger = Pick<Console, "info" | "warn">;

export type RunAnalysisTimeoutOptions = {
  timeoutMs?: number;
  intervalMs?: number;
  logger?: RunAnalysisTimeoutLogger;
};

export async function sweepRunAnalysisTimeouts(
  repo: RunAnalysisTimeoutRepositoryPort,
  options: RunAnalysisTimeoutOptions = {},
): Promise<number> {
  const timeoutMs = options.timeoutMs ?? RUN_ANALYSIS_TIMEOUT_MS;
  const logger = options.logger ?? console;
  const stuck = await repo.findStaleRunAnalysisSteps(timeoutMs);
  if (stuck.length === 0) return 0;
  logger.info(`[avernet][clawevolve][run-analysis-timeout] found ${stuck.length} stale step(s)`);
  let failed = 0;
  for (const row of stuck) {
    try {
      await repo.updateStepStatus(row.step_id, {
        status: "failed",
        errorCode: "RUN_ANALYSIS_TIMEOUT",
        errorMessage: `运行分析任务超过 ${timeoutMs / 60000} 分钟未完成，系统自动标记为失败`,
      });
      if (row.flow_id) await repo.failFlowAnalysis(row.flow_id);
      failed += 1;
    } catch (error) {
      logger.warn(
        `[avernet][clawevolve][run-analysis-timeout] failed to mark step ${row.step_id}: ${error instanceof Error ? error.message : String(error)}`,
      );
    }
  }
  return failed;
}

export function startRunAnalysisTimeoutSweeper(
  repo: RunAnalysisTimeoutRepositoryPort,
  options: RunAnalysisTimeoutOptions = {},
): NodeJS.Timeout {
  const logger = options.logger ?? console;
  const timer = setInterval(() => {
    void sweepRunAnalysisTimeouts(repo, options).catch((error) => {
      logger.warn(
        `[avernet][clawevolve][run-analysis-timeout] sweep failed: ${error instanceof Error ? error.message : String(error)}`,
      );
    });
  }, options.intervalMs ?? RUN_ANALYSIS_SWEEP_INTERVAL_MS);
  timer.unref();
  return timer;
}
