import type { IDatabase, Row } from '@avernet/clawweb-shared/server/db';
import {
  assertWatchCas, assertWatchRecord, isIdentity, isOwnerBotId, isEpochSeconds, isPositiveInteger,
  WATCH_FIELDS, WATCH_IDENTITY_FIELDS, assertWatchLeaseOwner, assertWatchDueCursor, type WatchDueCursor, type WatchRecord, type AIStudioTrainWatchStore,
} from '../contracts/aistudio-train.js';

const table = 'aistudio_train_watches';
const column = (key: string) => key.replace(/[A-Z]/g, c => `_${c.toLowerCase()}`);
const quote = (key: string) => `\`${column(key)}\``;
const numbers = new Set(['attempt','submittedAt','firstPollAt','nextPollAt','timeoutAt','pollIntervalSeconds',
  'lastPolledAt','errorCount','leaseExpiresAt','resolutionStartedAt','resolvedAt','manualRequestedAt','lastManualRequestedAt','version']);
function fromRow(row: Row): WatchRecord {
  const value: Record<string, unknown> = {};
  for (const key of WATCH_FIELDS) {
    const raw = row[column(key)];
    value[key] = key === 'canceled' ? raw === 1 || raw === true || raw === '1'
      : numbers.has(key) && raw !== null ? Number(raw) : raw;
  }
  assertWatchRecord(value); return value;
}
const values = (w: WatchRecord, keys: readonly (keyof WatchRecord)[] = WATCH_FIELDS) =>
  keys.map(key => key === 'canceled' ? Number(w[key]) : w[key]);
function uniqueConflict(error: unknown): boolean {
  const e = error as { code?: string; errno?: number };
  return e?.code === 'SQLITE_CONSTRAINT_UNIQUE' || e?.code === 'SQLITE_CONSTRAINT_PRIMARYKEY' || e?.code === 'ER_DUP_ENTRY' || e?.errno === 1062;
}
export type AIStudioRunTarget = { flowId: string; workflowId: string; ownerBotId: string | null; status: string };
export type AIStudioNodeTarget = { attempt: number; executorType: string | null; status: string };

/** SQL-only port; no HTTP, environment lookup, network calls or implicit retries. */
export class AIStudioTrainWatchRepository implements AIStudioTrainWatchStore {
  constructor(private readonly db: IDatabase) {
    if (db.dbType === 'noop') throw new Error('AIStudio watches require durable SQL persistence');
  }
  async isReady(): Promise<boolean> {
    try { await this.db.query(`SELECT id, gmt_create, gmt_modified, ${WATCH_FIELDS.map(quote).join(', ')} FROM ${table} LIMIT 0`); return true; }
    catch { return false; }
  }
  async create(record: WatchRecord): Promise<WatchRecord> {
    assertWatchRecord(record);
    try {
      const result = await this.db.exec(`INSERT INTO ${table} (${WATCH_FIELDS.map(quote).join(', ')}) VALUES (${WATCH_FIELDS.map(() => '?').join(', ')})`, values(record));
      if (result.affectedRows !== 1) throw new Error('AIStudio watch insert not confirmed');
      return structuredClone(record);
    } catch (error) {
      // Only a real uniqueness failure may reuse an already persisted identity.
      if (!uniqueConflict(error)) throw error;
      const existing = await this.find(record.flowId, record.nodeId, record.attempt);
      if (existing && existing.recordId === record.recordId && existing.workflowId === record.workflowId
        && existing.ownerBotId === record.ownerBotId && existing.mode === record.mode) return existing;
      throw error;
    }
  }
  async find(flowId: string, nodeId: string, attempt?: number): Promise<WatchRecord | null> {
    if (!isIdentity(flowId) || !isIdentity(nodeId) || (attempt !== undefined && !isPositiveInteger(attempt))) throw new Error('Invalid AIStudio find');
    const rows = await this.db.query<Row>(`SELECT * FROM ${table} WHERE flow_id = ? AND node_id = ?${attempt === undefined ? '' : ' AND attempt = ?'} ORDER BY attempt DESC LIMIT 1`, [flowId,nodeId,...(attempt === undefined ? [] : [attempt])]);
    return rows.length ? fromRow(rows[0]) : null;
  }
  async findDue(ownerBotId: string, now: number, limit: number, after?: WatchDueCursor): Promise<WatchRecord[]> {
    if (!isOwnerBotId(ownerBotId) || !isEpochSeconds(now) || !isPositiveInteger(limit) || limit > 1000) throw new Error('Invalid AIStudio due query');
    if (after !== undefined) assertWatchDueCursor(after);
    const priority = 'CASE WHEN manual_requested_at IS NOT NULL THEN 0 ELSE 1 END';
    const cursorSql = after === undefined ? '' : ` AND (${priority} > ? OR (${priority} = ? AND (next_poll_at > ? OR (next_poll_at = ? AND watch_id > ?))))`;
    const cursorParams = after === undefined ? [] : [after.manualPriority,after.manualPriority,after.nextPollAt,after.nextPollAt,after.watchId];
    // Exclude deleted runs BEFORE ordering/LIMIT: high-priority orphan mailbox
    // rows must not consume every page. Existing-but-reassigned targets remain
    // visible to the route's strict owner/workflow check (and are denied there).
    const flowMatch = this.db.dbType === 'sqlite' ? `r.flow_id = ${table}.flow_id` : `BINARY r.flow_id = BINARY ${table}.flow_id`;
    const rows = await this.db.query<Row>(`SELECT * FROM ${table}
      WHERE owner_bot_id = ? AND canceled = 0 AND phase IN ('waiting', 'polling')
      AND EXISTS (SELECT 1 FROM flow_runs r WHERE ${flowMatch})
      AND (lease_expires_at IS NULL OR lease_expires_at <= ?)
      AND (timeout_at <= ? OR manual_requested_at IS NOT NULL OR (first_poll_at <= ? AND next_poll_at <= ?))
      ${cursorSql}
      ORDER BY ${priority}, next_poll_at, watch_id LIMIT ?`, [ownerBotId,now,now,now,now,...cursorParams,limit]);
    return rows.map(fromRow);
  }
  async compareAndSwap(expectedVersion: number, next: WatchRecord, leaseOwner?: string): Promise<boolean> {
    assertWatchCas(expectedVersion,next);
    if (leaseOwner !== undefined) assertWatchLeaseOwner(leaseOwner);
    const guard = leaseOwner === undefined ? '' : this.db.dbType === 'sqlite'
      ? ' AND lease_owner = ? COLLATE BINARY AND lease_expires_at > unixepoch()'
      : ' AND BINARY lease_owner = BINARY ? AND lease_expires_at > UNIX_TIMESTAMP()';
    const mutable = WATCH_FIELDS.filter(key => !(WATCH_IDENTITY_FIELDS as readonly string[]).includes(key));
    const result = await this.db.exec(`UPDATE ${table} SET ${mutable.map(quote).map(k => `${k} = ?`).join(', ')}, gmt_modified = ?
      WHERE ${WATCH_IDENTITY_FIELDS.map(quote).map(k => `${k} = ?`).join(' AND ')} AND version = ?${guard}`,
    [...values(next,mutable),this.db.dialect.now(),...values(next,WATCH_IDENTITY_FIELDS),expectedVersion,...(leaseOwner === undefined ? [] : [leaseOwner])]);
    return result.affectedRows === 1;
  }
  /** User mailbox writes touch only these three fields (+version/audit).
   * Current node attempt/type/status and run ownership are fenced in the SAME
   * UPDATE, so a concurrent rerun cannot enqueue work on an obsolete attempt. */
  async requestManualPoll(expectedVersion: number, next: WatchRecord): Promise<boolean> {
    assertWatchCas(expectedVersion,next);
    const now = next.manualRequestedAt;
    // Legacy run/node tables may use a case-insensitive MySQL collation.
    const exact = (left: string,right: string) => this.db.dbType === 'sqlite' ? `${left} = ${right}` : `BINARY ${left} = BINARY ${right}`;
    if (now === null || next.lastManualRequestedAt !== now || !next.manualRequestedBy?.trim()) throw new Error('Invalid manual poll mailbox');
    const result = await this.db.exec(`UPDATE ${table} SET manual_requested_at = ?, manual_requested_by = ?, last_manual_requested_at = ?, version = ?, gmt_modified = ?
      WHERE ${WATCH_IDENTITY_FIELDS.map(quote).map(k => `${k} = ?`).join(' AND ')} AND version = ?
      AND canceled = 0 AND phase IN ('waiting','polling') AND timeout_at > ? AND manual_requested_at IS NULL
      AND (last_manual_requested_at IS NULL OR last_manual_requested_at <= ?)
      AND EXISTS (SELECT 1 FROM flow_runs r WHERE ${exact('r.flow_id',`${table}.flow_id`)} AND ${exact('r.workflow_id',`${table}.workflow_id`)}
        AND ${exact('r.origin_bot_id',`${table}.owner_bot_id`)} AND r.status IN ('running','waiting'))
      AND EXISTS (SELECT 1 FROM node_executions n WHERE ${exact('n.flow_id',`${table}.flow_id`)} AND ${exact('n.node_id',`${table}.node_id`)}
        AND n.attempt = ${table}.attempt AND n.executor_type = 'aistudio-train' AND n.status = 'waiting'
        AND NOT EXISTS (SELECT 1 FROM node_executions newer WHERE ${exact('newer.flow_id','n.flow_id')} AND ${exact('newer.node_id','n.node_id')} AND newer.attempt > n.attempt))`,
      [now,next.manualRequestedBy,now,next.version,this.db.dialect.now(),...values(next,WATCH_IDENTITY_FIELDS),expectedVersion,now,now - 60]);
    return result.affectedRows === 1;
  }
  /** Raw ownership is deliberately not projected to a bot-id-only permission. */
  async findRunTarget(flowId: string): Promise<AIStudioRunTarget | null> {
    const rows = await this.db.query<{ flow_id: string; workflow_id: string; origin_bot_id: string | null; status: string }>(
      'SELECT flow_id, workflow_id, origin_bot_id, status FROM flow_runs WHERE flow_id = ? LIMIT 1',[flowId]);
    const row = rows[0]; return row ? { flowId:row.flow_id, workflowId:row.workflow_id, ownerBotId:row.origin_bot_id, status:row.status } : null;
  }
  async findNodeTarget(flowId: string, nodeId: string): Promise<AIStudioNodeTarget | null> {
    const rows = await this.db.query<{ attempt: number; executor_type: string | null; status: string }>(
      'SELECT attempt, executor_type, status FROM node_executions WHERE flow_id = ? AND node_id = ? ORDER BY attempt DESC, id DESC LIMIT 1',[flowId,nodeId]);
    return rows[0] ? { attempt:Number(rows[0].attempt),executorType:rows[0].executor_type,status:rows[0].status } : null;
  }
}
