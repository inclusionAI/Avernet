import type { IDatabase } from '@avernet/clawweb-shared/server/db';

export type CompletionOutcome = 'updated' | 'duplicate' | 'rejected';
export type RunSnapshot = {
  status: string; started_at: number | null; state_json: string | null;
  retry_epoch?: number | null; completed_at: number | null;
  result_json: string | null; input_json: string | null; current_phase: string | null;
  total_duration_ms: number | null; total_token_usage: number | null;
  succeeded_count: number | null; failed_count: number | null;
};
export type SettlementFields = {
  status: string; retryEpoch?: number; resultJson?: string | null; inputJson?: string | null;
  currentPhase?: string | null; totalDurationMs?: number | null; totalTokenUsage?: number | null;
  succeededCount?: number; failedCount?: number;
};
export const terminalStatuses = ['succeeded', 'failed', 'cancelled', 'canceled'];
// Historical native success spelling is protected, not a new wire settlement status.
export const protectedTerminalStatuses = [...terminalStatuses, 'completed'];
export const isMissingRetryEpoch = (error: unknown): boolean =>
  /unknown column[^\n]*retry_epoch|no such column: (?:\w+\.)?retry_epoch\b/i.test(String(error));

/** Omitted epochs mean legacy epoch zero, not permission to write any attempt. */
export function matchesRetryEpoch(snapshot: { retry_epoch?: number | null }, expected = 0): boolean {
  const actual = snapshot.retry_epoch ?? 0;
  return Number.isSafeInteger(expected) && expected >= 0 && Number.isSafeInteger(actual)
    && actual >= 0 && actual === expected;
}

/**
 * Epochless compatibility must hold a schema lock through the write. An ALTER
 * between an unlocked fallback SELECT and UPDATE could otherwise create a new
 * generation that the old write never fenced. Re-probe after acquiring the lock.
 */
export async function withRetryEpochRead<T, R>(
  db: IDatabase, read: (db: IDatabase, epoch: boolean) => Promise<T>,
  apply: (db: IDatabase, saved: T, epoch: boolean) => Promise<R>,
): Promise<R> {
  let saved: T;
  try { saved = await read(db, true); }
  catch (error) {
    if (!isMissingRetryEpoch(error)) throw error;
    return db.transaction(async tx => {
      let epoch = true; let locked: T;
      try { locked = await read(tx, true); }
      catch (error) { if (!isMissingRetryEpoch(error)) throw error; epoch = false; locked = await read(tx, false); }
      return apply(tx, locked, epoch);
    });
  }
  return apply(db, saved, true);
}

async function readRunSnapshot(db: IDatabase, flowId: string, epoch: boolean): Promise<RunSnapshot | null> {
  return (await db.query<RunSnapshot>(
    `SELECT status, started_at, state_json, completed_at, result_json, input_json, current_phase,
      total_duration_ms, total_token_usage, succeeded_count, failed_count${epoch ? ', retry_epoch' : ''}
     FROM flow_runs WHERE flow_id = ?`, [flowId]))[0] ?? null;
}

/** Raw NULLs and byte-exact text are snapshot identity, independent of DB collation. */
export function snapshotFence(db: IDatabase, flowId: string, snapshot: RunSnapshot) {
  const params: unknown[] = [];
  const exact = (column: string, value: unknown, text = false) => {
    if (value === null) return `${column} IS NULL`;
    params.push(value);
    return text ? (db.dialect.driver === 'mysql2' ? `BINARY ${column} = BINARY ?` : `${column} COLLATE BINARY = ?`)
      : `${column} = ?`;
  };
  const textColumns = ['status', 'state_json', 'result_json', 'input_json', 'current_phase'];
  const conditions = [exact('flow_id', flowId, true), ...Object.entries(snapshot).map(([column, value]) =>
    exact(column, value, textColumns.includes(column)))];
  return { sql: conditions.join(' AND '), params };
}

function sameGeneration(a: RunSnapshot, b: RunSnapshot): boolean {
  return a.started_at === b.started_at && a.state_json === b.state_json && a.retry_epoch === b.retry_epoch
    && Object.hasOwn(a, 'retry_epoch') === Object.hasOwn(b, 'retry_epoch');
}

const settlementColumns = {
  resultJson: 'result_json', inputJson: 'input_json', currentPhase: 'current_phase',
  totalDurationMs: 'total_duration_ms', totalTokenUsage: 'total_token_usage',
  succeededCount: 'succeeded_count', failedCount: 'failed_count',
} as const;

/** All terminal writes share one admission/CAS protocol; acknowledgements never UPDATE. */
export async function writeRunSettlement(
  db: IDatabase, flowId: string, fields: SettlementFields,
  patch: () => { sets: string[]; values: unknown[] },
  acknowledgeDuplicate: boolean, strictlyRunning = false,
): Promise<CompletionOutcome> {
  return withRetryEpochRead(db, (tx, epoch) => readRunSnapshot(tx, flowId, epoch), async (db, snapshot, epoch) => {
    if (!snapshot || !snapshot.status || snapshot.status !== snapshot.status.trim().toLowerCase()
      || !terminalStatuses.includes(fields.status) || !matchesRetryEpoch(snapshot, fields.retryEpoch)
      || (fields.totalDurationMs != null && (!Number.isFinite(fields.totalDurationMs) || fields.totalDurationMs < 0))) return 'rejected';
    const duplicate = async (current: RunSnapshot): Promise<CompletionOutcome> => {
      if (!acknowledgeDuplicate || !sameGeneration(snapshot, current) || current.status !== fields.status
        || current.completed_at === null || !Number.isFinite(current.completed_at) || current.completed_at < 0
        || !matchesRetryEpoch(current, fields.retryEpoch)) return 'rejected';
      for (const [field, column] of Object.entries(settlementColumns)) {
        // Omission is a preserve/no-assertion, NULL is an explicit assertion.
        if (fields[field] !== undefined && fields[field] !== current[column]) return 'rejected';
      }
      const fence = snapshotFence(db, flowId, current);
      const rows = await db.query(`SELECT status FROM flow_runs WHERE ${fence.sql}`, fence.params);
      return rows.length === 1 ? 'duplicate' : 'rejected';
    };
    if (protectedTerminalStatuses.includes(snapshot.status)) return duplicate(snapshot);
    if (snapshot.completed_at !== null || (strictlyRunning && snapshot.status !== 'running')) return 'rejected';
    const fence = snapshotFence(db, flowId, snapshot);
    const update = patch();
    const result = await db.exec(`UPDATE flow_runs SET ${update.sets.join(', ')} WHERE ${fence.sql}`,
      [...update.values, ...fence.params]);
    if (result.affectedRows > 0) return 'updated';
    if (!acknowledgeDuplicate) return 'rejected';
    const current = await readRunSnapshot(db, flowId, epoch);
    return current ? duplicate(current) : 'rejected';
  });
}
