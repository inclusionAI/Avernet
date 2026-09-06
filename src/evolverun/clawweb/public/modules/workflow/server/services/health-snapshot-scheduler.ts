import type { HealthSnapshotRepository } from "../repositories/health-snapshot-repository.js";
import type { NodeStatsRepository } from "../repositories/node-stats-repository.js";

export function startHealthSnapshotScheduler(
  healthSnapshotRepo: HealthSnapshotRepository | null,
  nodeStatsRepo: NodeStatsRepository | null,
): void {
  async function runHealthSnapshots() {
    if (!healthSnapshotRepo || !nodeStatsRepo) return;
    try {
      const workflowIds = await healthSnapshotRepo.getDistinctWorkflowIds();
      const today = new Date().toISOString().slice(0, 10);
      const yesterday = new Date(Date.now() - 86400000).toISOString().slice(0, 10);
      for (const wfId of workflowIds) {
        const hasYesterday = await healthSnapshotRepo.hasSnapshotForDate(wfId, yesterday);
        if (!hasYesterday) {
          const health = await nodeStatsRepo.getWorkflowHealth(wfId, 7);
          await healthSnapshotRepo.upsertSnapshot({
            workflow_id: wfId, snapshot_date: yesterday,
            overall_score: health.overallScore, success_rate: health.successRate,
            node_failure_rate: health.nodeFailureRate, p95_duration_ms: health.p95DurationMs,
            retry_rate: health.retryRate, total_tokens: health.totalTokens,
          });
        }
        const hasToday = await healthSnapshotRepo.hasSnapshotForDate(wfId, today);
        if (!hasToday) {
          const health = await nodeStatsRepo.getWorkflowHealth(wfId, 7);
          await healthSnapshotRepo.upsertSnapshot({
            workflow_id: wfId, snapshot_date: today,
            overall_score: health.overallScore, success_rate: health.successRate,
            node_failure_rate: health.nodeFailureRate, p95_duration_ms: health.p95DurationMs,
            retry_rate: health.retryRate, total_tokens: health.totalTokens,
          });
        }
      }
      console.log(`[clawweb] Health snapshots: processed ${workflowIds.length} workflows`);
    } catch (err) {
      console.warn(`[clawweb] Health snapshot task failed: ${err instanceof Error ? err.message : err}`);
    }
  }

  setTimeout(() => void runHealthSnapshots(), 10000);
  const scheduleNextSnapshot = () => {
    const now = new Date();
    const next = new Date(now);
    next.setHours(2, 0, 0, 0);
    if (next <= now) next.setDate(next.getDate() + 1);
    setTimeout(() => { void runHealthSnapshots(); scheduleNextSnapshot(); }, next.getTime() - now.getTime());
  };
  scheduleNextSnapshot();
}
