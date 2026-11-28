import { stateAttemptEpoch, nextAttemptState } from './retry-epoch.js';

function parseObject(value: unknown): Record<string, unknown> {
  const parsed: unknown = typeof value === 'string' ? JSON.parse(value) : value;
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('Invalid retry checkpoint object');
  return parsed as Record<string, unknown>;
}

/** Validate the caller's captured next-attempt checkpoint before retry admission. */
export function validateRetryCheckpoint(stateJson: string, nextEpoch: number, workflowId?: string): Record<string, unknown> {
  const state = parseObject(stateJson);
  if (stateAttemptEpoch(state) !== nextEpoch) throw new Error('Retry checkpoint attempt mismatch');
  if (workflowId && state.workflowId != null && state.workflowId !== workflowId) throw new Error('Retry checkpoint identity mismatch');
  return state;
}

/** Retry admission updates both canonical and historical state in the same row CAS. */
export function prepareRetryCheckpoint(
  row: { workflow_id: string; state_json?: string | null; params_json?: string | null },
  nextEpoch: number, supplied?: string,
): { stateJson: string | null; paramsJson: string | null } {
  const params = row.params_json == null ? undefined : parseObject(row.params_json);
  const embedded = params && Object.hasOwn(params, '_clawmind_state') ? params._clawmind_state : undefined;
  const prior = row.state_json ?? embedded;
  for (const value of [row.state_json, embedded]) {
    if (value == null) continue;
    const state = parseObject(value);
    if (stateAttemptEpoch(state) !== nextEpoch - 1) throw new Error('Saved retry checkpoint attempt mismatch');
    if (state.workflowId != null && state.workflowId !== row.workflow_id) throw new Error('Saved retry checkpoint identity mismatch');
  }
  const next = supplied !== undefined ? validateRetryCheckpoint(supplied, nextEpoch, row.workflow_id)
    : prior == null ? undefined : nextAttemptState(parseObject(prior), nextEpoch);
  const stateJson = next ? JSON.stringify(next) : null;
  if (params && embedded !== undefined && next) {
    params._clawmind_state = typeof embedded === 'string' ? stateJson : next;
  }
  return { stateJson, paramsJson: params ? JSON.stringify(params) : null };
}
