import type { EvolveRepository } from "../../repositories/evolve-repository.js";

const RUN_ANALYSIS_TIMEOUT_MS = 30 * 60 * 1000;
const SWEEP_INTERVAL_MS = 5 * 60 * 1000;

export function startRunAnalysisTimeoutSweeper(repo: EvolveRepository): NodeJS.Timeout {
  const timer = setInterval(async () => {
    try {
      const stuck = await repo.findStaleRunAnalysisSteps(RUN_ANALYSIS_TIMEOUT_MS);
      if (stuck.length === 0) return;
      console.log(`[clawweb][evolve][run-analysis-timeout] found ${stuck.length} stale run-analysis step(s)`);
      for (const row of stuck) {
        try {
          await repo.updateStepStatus(row.step_id, {
            status: "failed",
            errorCode: "RUN_ANALYSIS_TIMEOUT",
            errorMessage: `运行分析任务超过 ${RUN_ANALYSIS_TIMEOUT_MS / 60000} 分钟未完成，系统自动标记为失败`,
          });
          if (row.flow_id) {
            await repo.failFlowAnalysis(row.flow_id);
          }
          console.log(`[clawweb][evolve][run-analysis-timeout] marked step ${row.step_id} (flow ${row.flow_id}) failed`);
        } catch (inner) {
          console.warn(`[clawweb][evolve][run-analysis-timeout] failed to mark step ${row.step_id}:`, inner instanceof Error ? inner.message : String(inner));
        }
      }
    } catch (err) {
      console.warn(`[clawweb][evolve][run-analysis-timeout] sweep failed: ${err instanceof Error ? err.message : String(err)}`);
    }
  }, SWEEP_INTERVAL_MS);
  timer.unref();
  return timer;
}
