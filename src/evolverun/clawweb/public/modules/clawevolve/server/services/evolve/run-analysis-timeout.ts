import type { EvolveRepository } from "../../repositories/evolve-repository.js";

const RUN_ANALYSIS_TIMEOUT_MS = 30 * 60 * 1000;
const SWEEP_INTERVAL_MS = 5 * 60 * 1000;

export async function runRunAnalysisTimeoutSweep(repo: EvolveRepository): Promise<number> {
  const stuck = await repo.findStaleRunAnalysisSteps(RUN_ANALYSIS_TIMEOUT_MS);
  if (stuck.length === 0) return 0;
  console.log(`[clawweb][evolve][run-analysis-timeout] found ${stuck.length} stale run-analysis step(s)`);
  let timedOut = 0;
  for (const row of stuck) {
    try {
      const message = `运行分析任务超过 ${RUN_ANALYSIS_TIMEOUT_MS / 60000} 分钟未完成，系统自动标记为失败`;
      if (await repo.tryTimeoutRunAnalysisStep(
        row.step_id,
        row.flow_id,
        "RUN_ANALYSIS_TIMEOUT",
        message,
        Date.now(),
      )) {
        timedOut += 1;
        console.log(`[clawweb][evolve][run-analysis-timeout] marked step ${row.step_id} (flow ${row.flow_id}) failed`);
      }
    } catch (inner) {
      console.warn(`[clawweb][evolve][run-analysis-timeout] failed to mark step ${row.step_id}:`, inner instanceof Error ? inner.message : String(inner));
    }
  }
  return timedOut;
}

export function startRunAnalysisTimeoutSweeper(repo: EvolveRepository): NodeJS.Timeout {
  const sweep = () => runRunAnalysisTimeoutSweep(repo).catch((err) => {
    console.warn(`[clawweb][evolve][run-analysis-timeout] sweep failed: ${err instanceof Error ? err.message : String(err)}`);
  });
  void sweep();
  const timer = setInterval(sweep, SWEEP_INTERVAL_MS);
  timer.unref();
  return timer;
}
