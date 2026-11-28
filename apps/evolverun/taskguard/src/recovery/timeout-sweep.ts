import { flowTimeoutElapsedMs } from './timeout-clock.js';
import type { FlowRunReapFields, IFlowRunRepository } from '../db/repositories/types.js';
import type { FlowState } from '../types.js';
import { resolveFlowTimeoutPolicy, type WorkflowTimeoutResolver } from './flow-timeout.js';
import { loadRunningSnapshot, withFlowScan, type RunningSnapshot } from './scan.js';

const scanOffsets = new WeakMap<IFlowRunRepository, number>();

/** Policy, snapshot hydration and distributed CAS; side effects belong only to the winning writer. */
export async function reapTimedOutRuns(options: {
  repository: IFlowRunRepository;
  globalTimeoutMs: number;
  limit: number;
  getState?: (flowId: string) => Promise<FlowState | undefined>;
  resolveLegacyWorkflow?: WorkflowTimeoutResolver;
  onClaim: (snapshot: RunningSnapshot, fields: FlowRunReapFields) => Promise<void>;
}): Promise<number> {
  return withFlowScan(async () => {
    // Discover all running candidates, including overrides shorter than global
    // and finite frozen budgets when the current global default is disabled.
    const offset = scanOffsets.get(options.repository) ?? 0;
    const candidates = await options.repository.findStaleRunning(
      Math.floor(Date.now() / 1000) + 1, options.limit, offset,
    );
    // Rotate even when every row is disabled, long-lived or corrupt. Removals
    // can shift rows behind this offset; wrapping revisits them next rotation.
    scanOffsets.set(options.repository, candidates.length === options.limit ? offset + options.limit : 0);
    let claimed = 0;
    for (const candidate of candidates) {
      try {
        const snapshot = await loadRunningSnapshot(options.repository, candidate.flow_id, options.getState);
        if (!snapshot || !snapshot.wallClockTimeout) continue;
        const { row, state, retryEpoch } = snapshot;
        const policy = await resolveFlowTimeoutPolicy({
          workflow_id: row.workflow_id,
          state_json: state ? JSON.stringify(state) : row.state_json,
        }, options.globalTimeoutMs, options.resolveLegacyWorkflow);
        if (policy.timeoutMs === 0) continue;
        const elapsedMs = flowTimeoutElapsedMs(row, state);
        if (elapsedMs < policy.timeoutMs) continue;
        const fields: FlowRunReapFields = {
          reason: JSON.stringify({
            error: `Workflow timed out after ${Math.floor(elapsedMs / 1000)}s (budget ${policy.timeoutMs / 1000}s)`,
            reason: 'flow_timeout', ranForSecs: Math.floor(elapsedMs / 1000),
            timeoutSecs: policy.timeoutMs / 1000, timeoutSource: policy.source,
          }),
          currentPhase: row.current_phase ?? state?.currentPhase ?? 'timeout',
          retryEpoch,
          totalDurationMs: elapsedMs,
          completedAt: Math.floor(Date.now() / 1000),
        };
        if (!await options.repository.markFailedIfRunning(row.flow_id, fields)) continue;
        claimed++;
        await options.onClaim(snapshot, fields);
      } catch (error) {
        console.warn(`[taskguard] timeout scan flowId=${candidate.flow_id}: ${error instanceof Error ? error.message : String(error)}`);
      }
    }
    return claimed;
  }, 0);
}
