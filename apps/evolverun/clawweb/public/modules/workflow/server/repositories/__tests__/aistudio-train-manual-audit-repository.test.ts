import Database from 'better-sqlite3';
import { afterEach, describe, expect, it } from 'vitest';
import { SqliteDatabase } from '@avernet/clawweb-shared/server/db/sqlite-database';
import { aistudioTrainManualAuditDdl } from '@avernet/clawweb-shared/server/schema-aistudio-train-manual-audit';
import { AIStudioTrainManualAuditRepository } from '../aistudio-train-manual-audit-repository.js';
const opened: Database.Database[]=[];
afterEach(() => { for(const db of opened.splice(0)) db.close(); });
import { manualAuditEvent } from './aistudio-train-manual-audit.fixture.js';
function fixture() {
  const raw=new Database(':memory:'); opened.push(raw);
  raw.exec(`CREATE TABLE flow_events(id INTEGER PRIMARY KEY AUTOINCREMENT,event_id TEXT NOT NULL,flow_id TEXT NOT NULL,
    workflow_id TEXT NOT NULL,node_id TEXT,event_type TEXT NOT NULL,attempt INTEGER,time BIGINT NOT NULL,data_json TEXT,error_text TEXT,
    gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),gmt_modified INTEGER NOT NULL DEFAULT (unixepoch()))`);
  for(const ddl of aistudioTrainManualAuditDdl('sqlite')) raw.exec(ddl);
  const db=new SqliteDatabase(raw); const repo=new AIStudioTrainManualAuditRepository(db);
  return {raw,db,repo};
}
describe('atomic exactly-once manual audit without legacy event uniqueness', () => {
  it('keeps one event and marker through concurrent retries and more than 1000 subsequent events', async () => {
    const {raw,repo}=fixture(); const event=manualAuditEvent();
    expect(await Promise.all(Array.from({length:8},() => repo.insertAIStudioTrainManualAudit(event)))).toEqual(Array(8).fill(true));
    const insert=raw.prepare("INSERT INTO flow_events(event_id,flow_id,workflow_id,event_type,time) VALUES(?,'flow-1','wf-1','node_progress',999)");
    for(let i=0;i<1001;i++) insert.run(`noise-${i}`);
    expect(await repo.insertAIStudioTrainManualAudit({...event,time:999,data:{...event.data,rawStatus:'success'}})).toBe(true);
    expect(raw.prepare('SELECT COUNT(*) AS n FROM flow_events WHERE event_id=?').get(event.id)).toEqual({n:1});
    const marker=raw.prepare('SELECT event_row_id,identity_json FROM aistudio_train_manual_audits').get() as {event_row_id:number;identity_json:string};
    expect(raw.prepare('SELECT event_id FROM flow_events WHERE id=?').get(marker.event_row_id)).toEqual({event_id:event.id});
    expect(marker.identity_json).toBe(JSON.stringify([event.id,'flow-1','wf-1','train','node_progress',1,'aistudio-train',true,'manual','owner','watch-1',200]));
    // The generic event table still accepts historical duplicate identifiers.
    insert.run('legacy'); insert.run('legacy');
    expect(raw.prepare("SELECT COUNT(*) AS n FROM flow_events WHERE event_id='legacy'").get()).toEqual({n:2});
  });
  it('adopts an exact pre-marker event without duplicating history and rejects legacy identity conflicts', async () => {
    const {raw,repo}=fixture(); const e=manualAuditEvent();
    raw.prepare(`INSERT INTO flow_events(event_id,flow_id,workflow_id,node_id,event_type,attempt,time,data_json) VALUES(?,?,?,?,?,?,?,?)`)
      .run(e.id,e.flowId,e.workflowId,e.nodeId,e.type,e.attempt,e.time,JSON.stringify(e.data));
    expect(await repo.insertAIStudioTrainManualAudit(e)).toBe(true);
    expect(raw.prepare('SELECT COUNT(*) AS n FROM flow_events WHERE event_id=?').get(e.id)).toEqual({n:1});
    raw.exec('DELETE FROM aistudio_train_manual_audits');raw.exec("UPDATE flow_events SET node_id='other'");
    await expect(repo.insertAIStudioTrainManualAudit(e)).rejects.toThrow();
    expect(raw.prepare('SELECT COUNT(*) AS n FROM aistudio_train_manual_audits').get()).toEqual({n:0});
  });
  it('rolls the marker back when real event insertion fails; retry can persist once after recovery', async () => {
    const {raw,repo}=fixture();
    raw.exec("CREATE TRIGGER fail_event BEFORE INSERT ON flow_events BEGIN SELECT RAISE(FAIL,'write unavailable'); END");
    await expect(repo.insertAIStudioTrainManualAudit(manualAuditEvent())).rejects.toThrow();
    expect(raw.prepare('SELECT COUNT(*) AS n FROM aistudio_train_manual_audits').get()).toEqual({n:0});
    raw.exec('DROP TRIGGER fail_event'); expect(await repo.insertAIStudioTrainManualAudit(manualAuditEvent())).toBe(true);
  });
  it('refuses marker-only, changed identity, or missing/modified original event ACKs', async () => {
    const {raw,repo}=fixture(); const event=manualAuditEvent(); await repo.insertAIStudioTrainManualAudit(event);
    await expect(repo.insertAIStudioTrainManualAudit({...event,data:{...event.data,userId:'other'}})).rejects.toThrow();
    raw.exec("UPDATE flow_events SET node_id='other'");
    await expect(repo.insertAIStudioTrainManualAudit(event)).rejects.toThrow();
    raw.exec('DELETE FROM flow_events');
    await expect(repo.insertAIStudioTrainManualAudit(event)).rejects.toThrow();
    expect(raw.prepare('SELECT COUNT(*) AS n FROM flow_events').get()).toEqual({n:0});
  });
  it('fails closed on missing marker schema or NoOp and never treats failed marker writes as existing success', async () => {
    const {raw,repo,db}=fixture(); expect(await repo.isReady()).toBe(true);
    raw.exec('DROP TABLE aistudio_train_manual_audits'); expect(await repo.isReady()).toBe(false);
    await expect(repo.insertAIStudioTrainManualAudit(manualAuditEvent())).rejects.toThrow();
    expect(() => new AIStudioTrainManualAuditRepository({dbType:'noop'} as any)).toThrow();
  });
});
