import type { IDatabase } from '@avernet/clawweb-shared/server/db';

export type CompletionOutcome = 'updated' | 'duplicate' | 'rejected';
type JsonObject = Record<string, unknown>;
type TimeoutSnapshot = {
  status: string;
  started_at: number;
  state_json: string | null;
  retry_epoch?: number | null;
  completed_at: number | null;
  result_json: string | null;
};
type TimeoutFields = {
  resultJson: unknown;
  retryEpoch?: number;
  totalDurationMs?: number | null;
};

function objectJson(value: unknown): JsonObject | null {
  if (typeof value !== 'string') return null;
  try {
    const parsed: unknown = JSON.parse(value);
    return parsed !== null && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed as JsonObject : null;
  } catch { return null; }
}

/** The controller wraps the original watchdog JSON in buildFailedResultJson.message. */
function evidenceObject(value: unknown, watchdog: boolean): JsonObject | null {
  let object = objectJson(value);
  for (let depth = 0; object && depth < 4; depth++) {
    if (object.reason === 'flow_timeout' || object.reason === 'timeout') return object;
    const nested = objectJson(object.message);
    if (!nested) return watchdog ? object : null;
    object = nested;
  }
  return null;
}

export function isTimeoutResult(value: unknown): boolean {
  return evidenceObject(value, false) !== null;
}

function budgetSeconds(stateJson: string | null): number | null {
  const state = objectJson(stateJson);
  if (!state) return null;
  let minutes: unknown;
  if (Object.hasOwn(state, 'effectiveFlowTimeoutMinutes')) {
    // A present disabled/invalid saved policy is authoritative, never a fallback.
    minutes = state.effectiveFlowTimeoutMinutes;
  } else {
    const snapshot = state.workflowSnapshot;
    if (!snapshot || typeof snapshot !== 'object' || Array.isArray(snapshot)) return null;
    const workflow = snapshot as JsonObject;
    minutes = Object.hasOwn(workflow, 'flowTimeoutMinutes') ? workflow.flowTimeoutMinutes : workflow.timeoutMinutes;
  }
  return typeof minutes === 'number' && Number.isFinite(minutes) && minutes > 0
    && minutes * 60 <= Number.MAX_SAFE_INTEGER ? minutes * 60 : null;
}

function validEvidence(snapshot: TimeoutSnapshot, fields: TimeoutFields, nowMs: number): boolean {
  const budget = budgetSeconds(snapshot.state_json);
  const evidence = evidenceObject(fields.resultJson, true);
  const wallSecs = nowMs / 1000 - snapshot.started_at;
  return budget !== null && evidence !== null
    && typeof snapshot.started_at === 'number' && Number.isFinite(snapshot.started_at) && snapshot.started_at >= 0
    && Number.isFinite(wallSecs) && wallSecs >= budget
    && evidence.timeoutSecs === budget
    && typeof evidence.ranForSecs === 'number' && Number.isFinite(evidence.ranForSecs)
    && evidence.ranForSecs >= budget && evidence.ranForSecs <= wallSecs
    && (fields.totalDurationMs == null || (typeof fields.totalDurationMs === 'number'
      && Number.isFinite(fields.totalDurationMs) && fields.totalDurationMs >= 0 && fields.totalDurationMs <= wallSecs * 1000));
}

async function readSnapshot(db: IDatabase, flowId: string): Promise<TimeoutSnapshot | null> {
  const select = (epoch: boolean) => db.query<TimeoutSnapshot>(
    `SELECT status, started_at, state_json, completed_at, result_json${epoch ? ', retry_epoch' : ''}
     FROM flow_runs WHERE flow_id = ?`, [flowId]);
  try { return (await select(true))[0] ?? null; }
  catch (error) {
    if (!/unknown column[^\n]*retry_epoch|no such column: retry_epoch\b/i.test(String(error))) throw error;
    // Preserve no-epoch clients on older schemas, without inventing an epoch column.
    return (await select(false))[0] ?? null;
  }
}

/** Nullness and text bytes are part of the policy snapshot, not SQL collation semantics. */
function snapshotFence(db: IDatabase, flowId: string, snapshot: TimeoutSnapshot) {
  const params: unknown[] = [];
  const exact = (column: string, value: unknown, text = false) => {
    if (value === null) return `${column} IS NULL`;
    params.push(value);
    return text ? (db.dialect.driver === 'mysql2' ? `BINARY ${column} = BINARY ?` : `${column} COLLATE BINARY = ?`)
      : `${column} = ?`;
  };
  const conditions = [exact('flow_id', flowId, true), exact('status', snapshot.status, true),
    exact('started_at', snapshot.started_at), exact('state_json', snapshot.state_json, true),
    exact('completed_at', snapshot.completed_at), exact('result_json', snapshot.result_json, true)];
  if (Object.hasOwn(snapshot, 'retry_epoch')) conditions.push(exact('retry_epoch', snapshot.retry_epoch));
  return { sql: conditions.join(' AND '), params };
}

function sameGeneration(a: TimeoutSnapshot, b: TimeoutSnapshot): boolean {
  return a.started_at === b.started_at && a.state_json === b.state_json && a.retry_epoch === b.retry_epoch
    && Object.hasOwn(a, 'retry_epoch') === Object.hasOwn(b, 'retry_epoch');
}

/** Every watchdog entry point uses the same validation and exact atomic SQL fence. */
export async function writeTimeoutFailure(
  db: IDatabase,
  flowId: string,
  fields: TimeoutFields,
  patch: (serverCompletedAt: number) => { sets: string[]; values: unknown[] },
  acknowledgeDuplicate = false,
): Promise<CompletionOutcome> {
  const snapshot = await readSnapshot(db, flowId);
  const nowMs = Date.now();
  if (!snapshot || !snapshot.status || snapshot.status !== snapshot.status.trim().toLowerCase()
    || !validEvidence(snapshot, fields, nowMs)
    || (fields.retryEpoch !== undefined && (!Object.hasOwn(snapshot, 'retry_epoch')
      || (snapshot.retry_epoch ?? 0) !== fields.retryEpoch))) return 'rejected';

  const duplicate = async (current: TimeoutSnapshot): Promise<CompletionOutcome> => {
    const budget = budgetSeconds(current.state_json);
    // The server floors completion seconds. Accept that exact rounded deadline
    // second, not an arbitrary premature fractional timestamp or a grace period.
    if (budget === null || !acknowledgeDuplicate || current.status !== 'failed' || !sameGeneration(snapshot, current)
      || !validEvidence(current, { resultJson: current.result_json }, nowMs)
      || typeof current.completed_at !== 'number' || current.completed_at > nowMs / 1000
      || (current.completed_at < current.started_at + budget
        && current.completed_at !== Math.floor(current.started_at + budget))) return 'rejected';
    const fence = snapshotFence(db, flowId, current);
    // No-op acknowledgement, atomically checking the entire selected snapshot.
    // SELECT avoids MySQL affectedRows differences for an unchanged UPDATE.
    const rows = await db.query(`SELECT status FROM flow_runs WHERE ${fence.sql}`, fence.params);
    return rows.length === 1 ? 'duplicate' : 'rejected';
  };
  if (['succeeded', 'failed', 'cancelled', 'canceled'].includes(snapshot.status)) return duplicate(snapshot);
  const fence = snapshotFence(db, flowId, snapshot);
  const update = patch(Math.floor(nowMs / 1000));
  const result = await db.exec(`UPDATE flow_runs SET ${update.sets.join(', ')} WHERE ${fence.sql}`,
    [...update.values, ...fence.params]);
  if (result.affectedRows > 0) return 'updated';
  if (!acknowledgeDuplicate) return 'rejected';
  // A simultaneous true timeout can be acknowledged, but a changed policy,
  // start, or raw epoch must never be reinterpreted as a new eligible run.
  const current = await readSnapshot(db, flowId);
  return current ? duplicate(current) : 'rejected';
}
