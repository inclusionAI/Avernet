export type WorkflowTimeoutResolver = (workflowId: string) => number | undefined | Promise<number | undefined>;
export type FlowTimeoutPolicy = {
  timeoutMs: number;
  source: 'run-state' | 'workflow-snapshot' | 'per-workflow' | 'global';
};

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function timeoutFromMinutes(minutes: unknown, field = 'flowTimeoutMinutes'): number {
  if (typeof minutes !== 'number' || !Number.isFinite(minutes) || minutes < 0
    || !Number.isFinite(minutes * 60_000)) {
    throw new Error(`Invalid workflow ${field}; timeout claim deferred`);
  }
  return minutes * 60_000;
}

/** New runs freeze their budget at startup; only historical runs use the legacy resolver. */
export async function resolveFlowTimeoutPolicy(
  row: { workflow_id: string; state_json?: string | null },
  globalTimeoutMs: number,
  resolveLegacyWorkflow?: WorkflowTimeoutResolver,
): Promise<FlowTimeoutPolicy> {
  let state: unknown;
  if (row.state_json != null && row.state_json !== '') {
    try { state = JSON.parse(row.state_json); }
    catch { throw new Error('Invalid persisted flow state JSON; timeout claim deferred'); }
    if (!record(state)) throw new Error('Invalid persisted flow state object; timeout claim deferred');
    if (Object.hasOwn(state, 'effectiveFlowTimeoutMinutes')) {
      return { timeoutMs: timeoutFromMinutes(state.effectiveFlowTimeoutMinutes, 'effectiveFlowTimeoutMinutes'), source: 'run-state' };
    }
  }
  if (!Number.isFinite(globalTimeoutMs) || globalTimeoutMs < 0) {
    throw new Error('Invalid global flow timeout; timeout claim deferred');
  }
  if (record(state)) {
    if (Object.hasOwn(state, 'workflowSnapshot')) {
      const snapshot = state.workflowSnapshot;
      if (!record(snapshot)) throw new Error('Invalid persisted workflow snapshot; timeout claim deferred');
      if (typeof snapshot.id !== 'string' || !snapshot.id.trim()) {
        throw new Error('Invalid persisted workflow snapshot identity; timeout claim deferred');
      }
      if (snapshot.id !== row.workflow_id) {
        throw new Error('Persisted workflow snapshot identity mismatch; timeout claim deferred');
      }
      // Validate identity, not the entire execution spec: historical snapshots
      // need not carry nodes/title/version to establish their timeout policy.
      // An explicit canonical value owns the policy even when it is invalid;
      // only its absence permits the legacy spelling from this same snapshot.
      if (Object.hasOwn(snapshot, 'flowTimeoutMinutes')) {
        return { timeoutMs: timeoutFromMinutes(snapshot.flowTimeoutMinutes), source: 'workflow-snapshot' };
      }
      if (Object.hasOwn(snapshot, 'timeoutMinutes')) {
        return { timeoutMs: timeoutFromMinutes(snapshot.timeoutMinutes, 'timeoutMinutes'), source: 'workflow-snapshot' };
      }
      // No override in an identity-checked snapshot selects the global default,
      // never a timeout added by a subsequent deployment.
      return { timeoutMs: globalTimeoutMs, source: 'global' };
    }
    if (state.workflowPin != null) {
      throw new Error('Pinned workflow snapshot unavailable; timeout claim deferred');
    }
  }
  // Legacy runs have no snapshot. An unavailable/invalid definition is not
  // evidence that its override is absent: propagate so the sweep skips this run.
  const minutes = await resolveLegacyWorkflow?.(row.workflow_id);
  return minutes === undefined
    ? { timeoutMs: globalTimeoutMs, source: 'global' }
    : { timeoutMs: timeoutFromMinutes(minutes), source: 'per-workflow' };
}

/** Resolve once at creation, retaining explicit zero; invalid overrides never select a shorter default. */
export function freezeFlowTimeoutMinutes(
  workflow: { flowTimeoutMinutes?: unknown }, globalMinutes: unknown,
): number {
  const minutes = Object.hasOwn(workflow, 'flowTimeoutMinutes') ? workflow.flowTimeoutMinutes : globalMinutes;
  timeoutFromMinutes(minutes, 'effectiveFlowTimeoutMinutes');
  return minutes as number;
}
