import { assertAIStudioStateSchema, aistudioStateKey, AUDIT_STATE_OWNER, STATE_COLUMNS, exactStateSql, stateLockSql, parseAIStudioState, stateVersion, uniqueStateConflict, type AIStudioStateRow } from './aistudio-train-state.js';
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
type AuditMarker = {identity:string;eventRowId:number|null};
function fromMarker(row: AIStudioStateRow, key: string): AuditMarker {
  const value = parseAIStudioState(row,'aisa:',AUDIT_STATE_OWNER) as AuditMarker;
  const version = stateVersion(row);
  if (row.config_key !== key || !value || typeof value !== 'object' || Array.isArray(value)
    || Object.keys(value).length !== 2 || typeof value.identity !== 'string'
    || (version === 1 ? value.eventRowId !== null : version !== 2 || !isPositiveInteger(value.eventRowId))) {
    throw new Error('Corrupt AIStudio manual audit marker');
  }
  return value;
}
/** Marker and flow_events writes commit together. A generic best-effort insert
 * cannot be used here: swallowed failures must roll back the marker. */
export class AIStudioTrainManualAuditRepository {
  constructor(private readonly db: IDatabase) {
    if (db.dbType === 'noop') throw new Error('AIStudio audits require durable SQL persistence');
  }
  async isReady(): Promise<boolean> {
    try {
      await assertAIStudioStateSchema(this.db);
      await this.db.query('SELECT id,event_id,flow_id,workflow_id,node_id,event_type,attempt,time,data_json,error_text,gmt_create,gmt_modified FROM flow_events LIMIT 0');
      return true;
    } catch { return false; }
  }
  async insertAIStudioTrainManualAudit(event: FlowEventInsert): Promise<boolean> {
    const identity = manualAuditIdentity(event);
    const key = aistudioStateKey('aisa:',[event.id]);
    try {
      return await this.db.transaction(async tx => {
        await assertAIStudioStateSchema(tx);
        const now = tx.dialect.now();
        const marker = await tx.exec(`INSERT INTO cm_app_config
          (config_key,config_yaml,version,enabled,description,gmt_create,gmt_modified) VALUES (?,?,1,0,?,?,?)`,
        [key,JSON.stringify({identity,eventRowId:null}),AUDIT_STATE_OWNER,now,now]);
        if (marker.affectedRows !== 1) throw new Error('AIStudio manual audit marker not confirmed');
        const exact = exactStateSql(tx,'event_id');
        const lock = stateLockSql(tx);
        // Old audits may predate the config marker. Adopt only exact persisted
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
        const bound = await tx.exec(`UPDATE cm_app_config SET config_yaml = ?,version = 2,gmt_modified = ?
          WHERE ${exactStateSql(tx,'config_key')} AND version = 1 AND enabled = 0 AND ${exactStateSql(tx,'description')}`,
        [JSON.stringify({identity,eventRowId:rowId}),now,key,AUDIT_STATE_OWNER]);
        if (bound.affectedRows !== 1) throw new Error('AIStudio manual audit event binding not confirmed');
        return true;
      });
    } catch (error) {
      if (!uniqueStateConflict(error)) throw error;
      // The failed transaction is rolled back first. A new transaction avoids a
      // stale REPEATABLE READ snapshot after a concurrent MySQL marker insert.
      return this.db.transaction(async tx => {
        const lock = stateLockSql(tx);
        const exact = exactStateSql(tx,'event_id');
        const markers = await tx.query<AIStudioStateRow>(
          `SELECT ${STATE_COLUMNS} FROM cm_app_config WHERE ${exactStateSql(tx,'config_key')}${lock}`,[key]);
        if (markers.length !== 1) throw new Error('AIStudio manual audit marker unavailable');
        const marker = fromMarker(markers[0],key);
        if (marker.identity !== identity || !isPositiveInteger(marker.eventRowId)) throw new Error('AIStudio manual audit identity conflict');
        const rows = await tx.query<FlowEventRow>(`SELECT * FROM flow_events WHERE id = ? AND ${exact}${lock}`,[marker.eventRowId,event.id]);
        if (rows.length !== 1 || rowIdentity(rows[0]) !== identity) throw new Error('AIStudio manual audit event unavailable');
        return true;
      });
    }
  }
}
