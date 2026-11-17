import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import type { FlowEventInsert, FlowEventRow } from './event-repository.js';
import { isIdentity, isEpochSeconds, isPositiveInteger } from '../contracts/aistudio-train.js';

/** Exactly the same canonical identity array as ClawMind's atomic event store.
 * Re-query time/status are observations, not request identity. First event wins. */
export function manualAuditIdentity(event: FlowEventInsert): string {
  const data = event.data;
  if (!isIdentity(event.id) || !isIdentity(event.flowId) || !isIdentity(event.workflowId) || !isIdentity(event.nodeId)
    || event.type !== 'node_progress' || !isPositiveInteger(event.attempt) || !isEpochSeconds(event.time)
    || !data || data.source !== 'aistudio-train' || data.manual !== true || data.pollSource !== 'manual'
    || typeof data.userId !== 'string' || !data.userId.trim() || data.userId.length > 255
    || !isIdentity(data.watchId,64) || !isEpochSeconds(data.requestedAt) || typeof data.rawStatus !== 'string' || data.rawStatus.length > 255
    || event.id !== `aistudio_manual_${data.watchId}_${data.requestedAt}`) throw new Error('Invalid AIStudio manual audit identity');
  return JSON.stringify([event.id,event.flowId,event.workflowId,event.nodeId,event.type,event.attempt,
    data.source,data.manual,data.pollSource,data.userId,data.watchId,data.requestedAt]);
}
function rowIdentity(row: FlowEventRow): string {
  return manualAuditIdentity({id:row.event_id,flowId:row.flow_id,workflowId:row.workflow_id,nodeId:row.node_id,
    type:row.event_type,attempt:Number(row.attempt),time:Number(row.time),data:JSON.parse(row.data_json ?? 'null')});
}
function uniqueConflict(error: unknown): boolean {
  const e = error as {code?:string;errno?:number};
  return e?.code === 'SQLITE_CONSTRAINT_UNIQUE' || e?.code === 'ER_DUP_ENTRY' || e?.errno === 1062;
}
/** Marker and flow_events writes commit together. A generic best-effort insert
 * cannot be used here: swallowed failures must roll back the marker. */
export class AIStudioTrainManualAuditRepository {
  constructor(private readonly db: IDatabase) {
    if (db.dbType === 'noop') throw new Error('AIStudio audits require durable SQL persistence');
  }
  async isReady(): Promise<boolean> {
    try {
      await this.db.query('SELECT id,event_id,identity_json,event_row_id,gmt_create,gmt_modified FROM aistudio_train_manual_audits LIMIT 0');
      await this.db.query('SELECT id,event_id,flow_id,workflow_id,node_id,event_type,attempt,time,data_json,error_text,gmt_create,gmt_modified FROM flow_events LIMIT 0');
      return true;
    } catch { return false; }
  }
  async insertAIStudioTrainManualAudit(event: FlowEventInsert): Promise<boolean> {
    const identity = manualAuditIdentity(event);
    try {
      return await this.db.transaction(async tx => {
        const marker = await tx.exec('INSERT INTO aistudio_train_manual_audits (event_id,identity_json) VALUES (?,?)',[event.id,identity]);
        if (marker.affectedRows !== 1) throw new Error('AIStudio manual audit marker not confirmed');
        const now = tx.dialect.now();
        const exact = tx.dbType === 'sqlite' ? 'event_id = ? COLLATE BINARY' : 'BINARY event_id = BINARY ?';
        const lock = tx.dbType === 'sqlite' ? '' : ' FOR UPDATE';
        // Old audits may predate the marker table. Adopt only exact persisted
        // identities without scanning a page or modifying historical rows.
        const legacy = await tx.query<FlowEventRow>(`SELECT * FROM flow_events WHERE ${exact}${lock}`,[event.id]);
        let rowId: number;
        if (legacy.length) {
          if (legacy.some(row => rowIdentity(row) !== identity)) throw new Error('AIStudio manual audit legacy identity conflict');
          rowId = Number(legacy[0].id);
        } else {
          const result = await tx.exec(`INSERT INTO flow_events
            (event_id,flow_id,workflow_id,node_id,event_type,attempt,time,data_json,error_text,gmt_create,gmt_modified)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)`,[event.id,event.flowId,event.workflowId,event.nodeId,event.type,event.attempt,event.time,
            JSON.stringify(event.data),event.error ?? null,now,now]);
          if (result.affectedRows !== 1 || !isPositiveInteger(result.insertId)) throw new Error('AIStudio manual audit event not confirmed');
          rowId = result.insertId;
        }
        if (!isPositiveInteger(rowId)) throw new Error('AIStudio manual audit row unavailable');
        const bound = await tx.exec('UPDATE aistudio_train_manual_audits SET event_row_id = ? WHERE event_id = ? AND event_row_id IS NULL',[rowId,event.id]);
        if (bound.affectedRows !== 1) throw new Error('AIStudio manual audit event binding not confirmed');
        return true;
      });
    } catch (error) {
      if (!uniqueConflict(error)) throw error;
      // The failed transaction is rolled back first. A new transaction avoids a
      // stale REPEATABLE READ snapshot after a concurrent MySQL marker insert.
      return this.db.transaction(async tx => {
        const lock = tx.dbType === 'sqlite' ? '' : ' FOR UPDATE';
        const exact = tx.dbType === 'sqlite' ? 'event_id = ? COLLATE BINARY' : 'BINARY event_id = BINARY ?';
        const markers = await tx.query<{identity_json:string;event_row_id:number | string | null}>(
          `SELECT identity_json,event_row_id FROM aistudio_train_manual_audits WHERE ${exact}${lock}`,[event.id]);
        const marker = markers[0];
        if (!marker || marker.identity_json !== identity || !isPositiveInteger(Number(marker.event_row_id))) throw new Error('AIStudio manual audit identity conflict');
        const rows = await tx.query<FlowEventRow>(`SELECT * FROM flow_events WHERE id = ? AND ${exact}${lock}`,[Number(marker.event_row_id),event.id]);
        if (rows.length !== 1 || rowIdentity(rows[0]) !== identity) throw new Error('AIStudio manual audit event unavailable');
        return true;
      });
    }
  }
}
