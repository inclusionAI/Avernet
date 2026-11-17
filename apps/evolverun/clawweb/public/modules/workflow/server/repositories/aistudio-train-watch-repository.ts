import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import {
  assertWatchCas, assertWatchRecord, isIdentity, isOwnerBotId, isEpochSeconds, isPositiveInteger,
  WATCH_IDENTITY_FIELDS, assertWatchLeaseOwner, assertWatchDueCursor, type WatchDueCursor, type WatchRecord, type AIStudioTrainWatchStore,
} from '../contracts/aistudio-train.js';
import {
  assertAIStudioStateSchema, aistudioStateKey, watchStateDescription, WATCH_STATE_OWNER, STATE_COLUMNS, exactStateSql, stateLockSql,
  parseAIStudioState, stateVersion, uniqueStateConflict, type AIStudioStateRow,
} from './aistudio-train-state.js';

const keyOf = (w: Pick<WatchRecord,'flowId'|'nodeId'|'attempt'>) => aistudioStateKey('aisw:',[w.flowId,w.nodeId,w.attempt]);
function fromRow(row: AIStudioStateRow): WatchRecord {
  assertWatchHeader(row);
  const value = parseAIStudioState(row,'aisw:',row.description!);
  assertWatchRecord(value);
  if (keyOf(value) !== row.config_key || value.version !== stateVersion(row) || row.description !== watchStateDescription(value)) throw new Error('Corrupt AIStudio watch identity/version');
  return value;
}
function assertWatchHeader(row: Omit<AIStudioStateRow,'config_yaml'>): void {
  const parts = row.description?.slice(WATCH_STATE_OWNER.length).match(/^:(active|inactive):(.+)$/);
  if (!/^aisw:[a-z2-7]{52}$/.test(row.config_key) || (row.enabled !== 0 && row.enabled !== '0')
    || !row.description?.startsWith(WATCH_STATE_OWNER) || !parts || !isOwnerBotId(parts[2])) throw new Error('Corrupt AIStudio watch envelope');
  stateVersion(row);
}
const sameIdentity = (left: WatchRecord, right: WatchRecord) => WATCH_IDENTITY_FIELDS.every(key => left[key] === right[key]);
const priority = (w: WatchRecord): 0|1 => w.manualRequestedAt === null ? 1 : 0;
const compare = (a: WatchDueCursor, b: WatchDueCursor) => a.manualPriority - b.manualPriority || a.nextPollAt - b.nextPollAt
  || (a.watchId < b.watchId ? -1 : a.watchId > b.watchId ? 1 : 0);
const cursor = (w: WatchRecord): WatchDueCursor => ({manualPriority:priority(w),nextPollAt:w.nextPollAt,watchId:w.watchId});
export type AIStudioRunTarget = { flowId: string; workflowId: string; ownerBotId: string | null; status: string };
export type AIStudioNodeTarget = { attempt: number; executorType: string | null; status: string };

/** Private disabled v71 config rows, never normal application configuration.
 * Locked OLD snapshots plus row-version CAS avoid dialect-specific JSON writes. */
export class AIStudioTrainWatchRepository implements AIStudioTrainWatchStore {
  constructor(private readonly db: IDatabase) {
    if (db.dbType === 'noop') throw new Error('AIStudio watches require durable SQL persistence');
  }
  async isReady(): Promise<boolean> {
    try {
      await assertAIStudioStateSchema(this.db);
      return true;
    } catch { return false; }
  }
  private async readRow(db: IDatabase, key: string, lock = false): Promise<AIStudioStateRow|null> {
    const rows = await db.query<AIStudioStateRow>(`SELECT ${STATE_COLUMNS} FROM cm_app_config WHERE ${exactStateSql(db,'config_key')}${lock ? stateLockSql(db) : ''}`,[key]);
    if (rows.length > 1) throw new Error('Ambiguous AIStudio watch state');
    return rows[0] ?? null;
  }
  private async scan(db: IDatabase, lock = false): Promise<WatchRecord[]> {
    // No LIMIT here: unrelated owners, deleted runs and corrupt rows cannot hide
    // a live page or turn persistence corruption into a false empty success.
    const rows = await db.query<AIStudioStateRow>(`SELECT ${STATE_COLUMNS} FROM cm_app_config WHERE config_key LIKE 'aisw:%'${lock ? stateLockSql(db) : ''}`);
    return rows.map(fromRow);
  }
  async create(record: WatchRecord): Promise<WatchRecord> {
    assertWatchRecord(record);
    if (record.version !== 1) throw new Error('AIStudio watch creation requires version 1');
    try {
      return await this.db.transaction(async tx => {
        await assertAIStudioStateSchema(tx);
        // Retain the prior watchId collision contract without a new index/table.
        const rows = await this.scan(tx,true);
        if (rows.some(w => w.watchId === record.watchId && keyOf(w) !== keyOf(record))) throw new Error('AIStudio watch ID conflict');
        const now = tx.dialect.now();
        const result = await tx.exec(`INSERT INTO cm_app_config
          (config_key,config_yaml,version,enabled,description,gmt_create,gmt_modified) VALUES (?,?,1,0,?,?,?)`,
        [keyOf(record),JSON.stringify(record),watchStateDescription(record),now,now]);
        if (result.affectedRows !== 1) throw new Error('AIStudio watch insert not confirmed');
        return structuredClone(record);
      });
    } catch (error) {
      if (!uniqueStateConflict(error)) throw error;
      const existing = await this.find(record.flowId,record.nodeId,record.attempt);
      if (existing && existing.recordId === record.recordId && existing.workflowId === record.workflowId
        && existing.ownerBotId === record.ownerBotId && existing.mode === record.mode) return existing;
      throw error;
    }
  }
  async find(flowId: string, nodeId: string, attempt?: number): Promise<WatchRecord|null> {
    if (!isIdentity(flowId) || !isIdentity(nodeId) || (attempt !== undefined && !isPositiveInteger(attempt))) throw new Error('Invalid AIStudio find');
    if (attempt !== undefined) {
      const row = await this.readRow(this.db,keyOf({flowId,nodeId,attempt}));
      return row ? fromRow(row) : null;
    }
    const rows = (await this.scan(this.db)).filter(w => w.flowId === flowId && w.nodeId === nodeId);
    return rows.sort((a,b) => b.attempt - a.attempt)[0] ?? null;
  }
  async findDue(ownerBotId: string, now: number, limit: number, after?: WatchDueCursor): Promise<WatchRecord[]> {
    if (!isOwnerBotId(ownerBotId) || !isEpochSeconds(now) || !isPositiveInteger(limit) || limit > 1000) throw new Error('Invalid AIStudio due query');
    if (after !== undefined) assertWatchDueCursor(after);
    // Existing storage has no scheduling index. Validate small metadata first,
    // then hydrate only this owner's active records: terminal/foreign JSON is
    // never transferred or parsed on each poll. Metadata scanning remains O(N).
    const headers = await this.db.query<Omit<AIStudioStateRow,'config_yaml'>>(
      "SELECT config_key,version,enabled,description FROM cm_app_config WHERE config_key LIKE 'aisw:%'");
    for (const row of headers) assertWatchHeader(row);
    const rows = await this.db.query<AIStudioStateRow>(`SELECT ${STATE_COLUMNS} FROM cm_app_config
      WHERE config_key LIKE 'aisw:%' AND ${exactStateSql(this.db,'description')}`,
    [watchStateDescription({ownerBotId,phase:'waiting',canceled:false})]);
    const candidates = rows.map(fromRow).filter(w => w.ownerBotId === ownerBotId && !w.canceled
      && ['waiting','polling'].includes(w.phase) && (w.leaseExpiresAt === null || w.leaseExpiresAt <= now)
      && (w.timeoutAt <= now || w.manualRequestedAt !== null || (w.firstPollAt <= now && w.nextPollAt <= now))
      && (after === undefined || compare(cursor(w),after) > 0));
    const live: WatchRecord[] = [];
    const runs = new Map<string,boolean>();
    for (const w of candidates) {
      if (!runs.has(w.flowId)) runs.set(w.flowId,(await this.findRunTarget(w.flowId)) !== null);
      if (runs.get(w.flowId)) live.push(w);
    }
    return live.sort((a,b) => compare(cursor(a),cursor(b))).slice(0,limit);
  }
  async compareAndSwap(expectedVersion: number, next: WatchRecord, leaseOwner?: string): Promise<boolean> {
    assertWatchCas(expectedVersion,next);
    if (leaseOwner !== undefined) assertWatchLeaseOwner(leaseOwner);
    return this.db.transaction(async tx => {
      await assertAIStudioStateSchema(tx);
      const row = await this.readRow(tx,keyOf(next),true);
      const old = row ? fromRow(row) : null;
      if (!old || old.version !== expectedVersion || !sameIdentity(old,next)) return false;
      // Use the locked OLD lease, not caller-supplied next values or JS time.
      const guard = leaseOwner === undefined ? '' : ` AND ${exactStateSql(tx,'?')} AND ? > ${tx.dbType === 'sqlite' ? 'unixepoch()' : 'UNIX_TIMESTAMP()'}`;
      const result = await tx.exec(`UPDATE cm_app_config SET config_yaml = ?,version = ?,description = ?,gmt_modified = ?
        WHERE ${exactStateSql(tx,'config_key')} AND version = ? AND enabled = 0 AND ${exactStateSql(tx,'description')} AND ${exactStateSql(tx,'config_yaml')}${guard}`,
      [JSON.stringify(next),next.version,watchStateDescription(next),tx.dialect.now(),keyOf(old),expectedVersion,row!.description,row!.config_yaml,
        ...(leaseOwner === undefined ? [] : [old.leaseOwner,leaseOwner,old.leaseExpiresAt])]);
      return result.affectedRows === 1;
    });
  }
  /** Mailbox writes cannot overwrite runtime fields, even with a forged payload.
   * Latest node attempt/type/wait status and exact run ownership are checked by
   * the same SQL UPDATE that advances the locked config-row version. */
  async requestManualPoll(expectedVersion: number, next: WatchRecord): Promise<boolean> {
    assertWatchCas(expectedVersion,next);
    const now = next.manualRequestedAt;
    if (now === null || next.lastManualRequestedAt !== now || !next.manualRequestedBy?.trim()) throw new Error('Invalid manual poll mailbox');
    return this.db.transaction(async tx => {
      await assertAIStudioStateSchema(tx);
      const row = await this.readRow(tx,keyOf(next),true);
      const old = row ? fromRow(row) : null;
      if (!old || old.version !== expectedVersion || !sameIdentity(old,next) || old.canceled
        || !['waiting','polling'].includes(old.phase) || old.timeoutAt <= now || old.manualRequestedAt !== null
        || (old.lastManualRequestedAt !== null && old.lastManualRequestedAt > now - 60)) return false;
      const updated = {...old,manualRequestedAt:now,manualRequestedBy:next.manualRequestedBy,lastManualRequestedAt:now,version:next.version};
      const exact = (left: string, right = '?') => exactStateSql(tx,left,right);
      const result = await tx.exec(`UPDATE cm_app_config SET config_yaml = ?,version = ?,description = ?,gmt_modified = ?
        WHERE ${exact('config_key')} AND version = ? AND enabled = 0 AND ${exact('description')} AND ${exact('config_yaml')}
        AND EXISTS (SELECT 1 FROM flow_runs r WHERE ${exact('r.flow_id')} AND ${exact('r.workflow_id')}
          AND ${exact('r.origin_bot_id')} AND r.status IN ('running','waiting'))
        AND EXISTS (SELECT 1 FROM node_executions n WHERE ${exact('n.flow_id')} AND ${exact('n.node_id')}
          AND n.attempt = ? AND ${exact('n.executor_type')} AND ${exact('n.status')}
          AND NOT EXISTS (SELECT 1 FROM node_executions newer WHERE ${exact('newer.flow_id','n.flow_id')}
            AND ${exact('newer.node_id','n.node_id')} AND (newer.attempt > n.attempt OR (newer.attempt = n.attempt AND newer.id > n.id))))`,
      [JSON.stringify(updated),updated.version,watchStateDescription(updated),tx.dialect.now(),keyOf(old),expectedVersion,row!.description,row!.config_yaml,
        old.flowId,old.workflowId,old.ownerBotId,old.flowId,old.nodeId,old.attempt,'aistudio-train','waiting']);
      return result.affectedRows === 1;
    });
  }
  async findRunTarget(flowId: string): Promise<AIStudioRunTarget|null> {
    const rows = await this.db.query<{flow_id:string;workflow_id:string;origin_bot_id:string|null;status:string}>(
      `SELECT flow_id,workflow_id,origin_bot_id,status FROM flow_runs WHERE ${exactStateSql(this.db,'flow_id')} LIMIT 1`,[flowId]);
    const row = rows[0];
    return row ? {flowId:row.flow_id,workflowId:row.workflow_id,ownerBotId:row.origin_bot_id,status:row.status} : null;
  }
  async findNodeTarget(flowId: string, nodeId: string): Promise<AIStudioNodeTarget|null> {
    const rows = await this.db.query<{attempt:number;executor_type:string|null;status:string}>(
      `SELECT attempt,executor_type,status FROM node_executions WHERE ${exactStateSql(this.db,'flow_id')} AND ${exactStateSql(this.db,'node_id')} ORDER BY attempt DESC,id DESC LIMIT 1`,[flowId,nodeId]);
    return rows[0] ? {attempt:Number(rows[0].attempt),executorType:rows[0].executor_type,status:rows[0].status} : null;
  }
}
