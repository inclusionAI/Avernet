import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import { FanoutStateRepository } from './fanout-state-repository.js';
import { matchesRetryEpoch, protectedTerminalStatuses, withRetryEpochRead } from './flow-run-write-fence.js';

type MirrorRow = { status: string; state_json: string | null; params_json: string | null; current_phase: string | null;
  completed_at: number | null; retry_epoch?: number | null };
/** A late mirror is not permission to rewind a retry or mutate accepted settlement. */
export async function writeRunMirror(
  db: IDatabase, flowId: string, column: 'state_json' | 'current_phase', value: string, retryEpoch?: number,
): Promise<boolean> {
  return db.transaction(tx => withRetryEpochRead(tx, async (locked, epoch) => {
    const exact = locked.dialect.driver === 'mysql2' ? 'BINARY flow_id = BINARY ?' : 'flow_id COLLATE BINARY = ?';
    const lock = locked.dialect.driver === 'mysql2' ? ' FOR UPDATE' : '';
    return (await locked.query<MirrorRow>(`SELECT status, state_json, params_json, current_phase, completed_at${epoch ? ', retry_epoch' : ''}
      FROM flow_runs WHERE ${exact}${lock}`, [flowId]))[0];
  }, async (locked, row) => {
    if (!row || !matchesRetryEpoch(row, retryEpoch) || !row.status
      || row.status !== row.status.trim().toLowerCase()) return false;
    // Even a same-value acknowledgement must carry the original generation.
    if (protectedTerminalStatuses.includes(row.status) || row.completed_at !== null) return row[column] === value;
    if (column === 'state_json') {
      const state: unknown = JSON.parse(value);
      if (state && typeof state === 'object' && !Array.isArray(state)) {
        const aliases = ['retryEpoch', 'recoveryEpoch'].filter(key => Object.hasOwn(state, key));
        const epoch = retryEpoch ?? 0;
        if ((!aliases.length && epoch !== 0) || aliases.some(key => !Number.isSafeInteger(state[key]) || state[key] !== epoch)) return false;
      }
      if (!await new FanoutStateRepository(locked).updateMirror(flowId, value)) return false;
      if (row.params_json !== null) {
        const params = JSON.parse(row.params_json);
        if (!params || typeof params !== 'object' || Array.isArray(params)) throw new Error('Invalid saved mirror params');
        if (Object.hasOwn(params, '_clawmind_state')) {
          // Use the trusted fanout merge, not the raw incoming checkpoint. Both
          // stores commit/roll back with the locked generation in this transaction.
          const [saved] = await locked.query<{ state_json: string }>('SELECT state_json FROM flow_runs WHERE flow_id = ?', [flowId]);
          if (!saved) throw new Error('Mirror checkpoint disappeared');
          params._clawmind_state = typeof params._clawmind_state === 'string' ? saved.state_json : JSON.parse(saved.state_json);
          const serialized = JSON.stringify(params);
          if (serialized !== row.params_json) {
            const exact = (field: string) => locked.dialect.driver === 'mysql2' ? `BINARY ${field} = BINARY ?` : `${field} COLLATE BINARY = ?`;
            const result = await locked.exec(`UPDATE flow_runs SET params_json = ? WHERE ${exact('flow_id')} AND ${exact('params_json')}`,
              [serialized, flowId, row.params_json]);
            if (result.affectedRows !== 1) throw new Error('Embedded mirror CAS lost');
          }
        }
      }
      return true;
    }
    const exact = (field: string) => locked.dialect.driver === 'mysql2' ? `BINARY ${field} = BINARY ?` : `${field} COLLATE BINARY = ?`;
    const conditions = [exact('flow_id'), exact('status'), 'completed_at IS NULL'];
    const params: unknown[] = [value, locked.dialect.now(), flowId, row.status];
    if (Object.hasOwn(row, 'retry_epoch')) {
      conditions.push(row.retry_epoch === null ? 'retry_epoch IS NULL' : 'retry_epoch = ?');
      if (row.retry_epoch !== null) params.push(row.retry_epoch);
    }
    const result = await locked.exec(`UPDATE flow_runs SET current_phase = ?, gmt_modified = ? WHERE ${conditions.join(' AND ')}`, params);
    if (!Number.isSafeInteger(result.affectedRows) || result.affectedRows < 0 || result.affectedRows > 1) throw new Error('Invalid mirror affectedRows');
    return result.affectedRows === 1;
  }));
}
