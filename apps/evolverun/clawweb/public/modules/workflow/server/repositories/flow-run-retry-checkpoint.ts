import { isDeepStrictEqual } from 'node:util';
import type { IDatabase } from '@avernet/clawweb-shared/server/db';

export class RetryCheckpointInvalid extends Error {}
class SavedCheckpointConflict extends Error {}
type State = Record<string, unknown>;
type SavedRun = {
  status: string; retry_epoch: number | null; workflow_id: string;
  state_json: string | null; params_json: string | null; completed_at: number | null;
};

function objectJson(value: unknown): State {
  if (typeof value !== 'string') throw new Error('Expected JSON object string');
  const parsed: unknown = JSON.parse(value);
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('Expected JSON object');
  return parsed as State;
}

/** Present-but-invalid aliases must never fall back to a valid sibling. */
function checkpointEpoch(state: State): number {
  const aliases = ['retryEpoch', 'recoveryEpoch'].filter(key => Object.hasOwn(state, key));
  if (!aliases.length) return 0;
  const epoch = state[aliases[0]];
  if (!Number.isSafeInteger(epoch) || (epoch as number) < 0 || aliases.some(key => state[key] !== epoch)) {
    throw new Error('Invalid checkpoint epoch aliases');
  }
  return epoch as number;
}
/** Invalid caller data must fail before any admission write (including replay). */
export function validateRetryCheckpoint(stateJson: unknown, nextEpoch: number): State {
  let state: State;
  try { state = objectJson(stateJson); }
  catch { throw new RetryCheckpointInvalid('state_json must be a valid checkpoint JSON object string'); }
  try { if (checkpointEpoch(state) !== nextEpoch) throw new Error('Mismatched epoch'); }
  catch { throw new RetryCheckpointInvalid('state_json checkpoint retryEpoch/recoveryEpoch must match retry_epoch'); }
  return state;
}

function prepareCheckpoint(row: SavedRun, next: State, stateJson: string, previousEpoch: number): { stateJson: string; paramsJson: string | null } {
  try {
    const params = row.params_json === null ? null : objectJson(row.params_json);
    const hasEmbedded = params !== null && Object.hasOwn(params, '_clawmind_state');
    const embeddedValue = hasEmbedded ? params._clawmind_state : undefined;
    const embedded = hasEmbedded ? (typeof embeddedValue === 'string' ? objectJson(embeddedValue)
      : objectJson(JSON.stringify(embeddedValue))) : undefined;
    const canonical = row.state_json === null ? undefined : objectJson(row.state_json);
    for (const prior of [canonical, embedded]) {
      if (!prior) continue;
      if (checkpointEpoch(prior) !== previousEpoch
        || (prior.workflowId !== undefined && prior.workflowId !== row.workflow_id)) throw new Error('Saved identity or epoch mismatch');
    }
    if (canonical && embedded && !isDeepStrictEqual(canonical, embedded)) throw new Error('Saved checkpoint disagreement');
    const current = canonical ?? embedded ?? {};
    if ([next, current].some(state => ['_fanoutIdentity', '_fanoutRevision'].some(key => Object.hasOwn(state, key)))) {
      throw new Error('Managed child checkpoints require revision CAS');
    }
    // Preserve existing fanout authority exactly; retry admission cannot author it.
    const { subworkflowFanouts: _untrusted, ...merged } = next;
    if (Object.hasOwn(current, 'fanoutOrigin')) {
      if (Object.hasOwn(next, 'fanoutOrigin') && !isDeepStrictEqual(current.fanoutOrigin, next.fanoutOrigin)) throw new Error('Fanout origin is immutable');
      merged.fanoutOrigin = current.fanoutOrigin;
    }
    if (Object.hasOwn(current, 'subworkflowFanouts')) merged.subworkflowFanouts = current.subworkflowFanouts;
    const serialized = isDeepStrictEqual(merged, next) ? stateJson : JSON.stringify(merged);
    if (hasEmbedded) params._clawmind_state = typeof embeddedValue === 'string' ? serialized : merged;
    return { stateJson: serialized, paramsJson: hasEmbedded ? JSON.stringify(params) : row.params_json };
  } catch { throw new SavedCheckpointConflict('Saved retry checkpoint conflicts with the original attempt'); }
}

/** Canonical and historical state are one generation-bearing parent-row CAS. */
export async function resumeWithCheckpoint(
  db: IDatabase, flowId: string, nextEpoch: number, stateJson: string,
): Promise<'resumed' | 'not_found' | 'conflict'> {
  const next = validateRetryCheckpoint(stateJson, nextEpoch);
  return db.transaction(async tx => {
    const exact = (column: string) => tx.dialect.driver === 'mysql2' ? `BINARY ${column} = BINARY ?` : `${column} COLLATE BINARY = ?`;
    const [row] = await tx.query<SavedRun>(
      `SELECT status, retry_epoch, workflow_id, state_json, params_json, completed_at FROM flow_runs
       WHERE ${exact('flow_id')}${tx.dialect.driver === 'mysql2' ? ' FOR UPDATE' : ''}`, [flowId]);
    if (!row) return 'not_found';
    if (next.workflowId !== undefined && next.workflowId !== row.workflow_id) {
      throw new RetryCheckpointInvalid('state_json checkpoint workflow identity does not match the run');
    }
    if (['cancelled', 'canceled'].includes(row.status.trim().toLowerCase())
      || ['_fanoutIdentity', '_fanoutRevision'].some(key => Object.hasOwn(next, key))) return 'conflict';
    // Replayed admission is not permission to roll back this attempt's progress.
    if (row.status === 'running' && row.retry_epoch === nextEpoch && row.completed_at === null) return 'resumed';
    if ((row.retry_epoch ?? 0) !== nextEpoch - 1) return 'conflict';
    let checkpoint: { stateJson: string; paramsJson: string | null };
    try { checkpoint = prepareCheckpoint(row, next, stateJson, nextEpoch - 1); }
    catch (error) { if (error instanceof SavedCheckpointConflict) return 'conflict'; throw error; }
    const conditions = [exact('flow_id'), 'COALESCE(retry_epoch, 0) = ?'];
    const params: unknown[] = [flowId, nextEpoch - 1];
    for (const [column, value] of Object.entries(row)) {
      if (value === null) conditions.push(`${column} IS NULL`);
      else { conditions.push(typeof value === 'string' ? exact(column) : `${column} = ?`); params.push(value); }
    }
    const result = await tx.exec(
      `UPDATE flow_runs SET status = 'running', retry_epoch = ?, state_json = ?, params_json = ?, completed_at = NULL, gmt_modified = ?
       WHERE ${conditions.join(' AND ')}`, [nextEpoch, checkpoint.stateJson, checkpoint.paramsJson, tx.dialect.now(), ...params]);
    if (!Number.isSafeInteger(result.affectedRows) || result.affectedRows < 0 || result.affectedRows > 1) throw new Error('Invalid retry checkpoint affectedRows');
    return result.affectedRows === 1 ? 'resumed' : 'conflict';
  });
}
