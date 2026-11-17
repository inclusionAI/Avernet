import Database from 'better-sqlite3';
import { mkdtempSync,rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { expect,it } from 'vitest';
import { SqliteDatabase } from '@avernet/clawweb-shared/server/db/sqlite-database';
import { AIStudioTrainWatchRepository } from '../aistudio-train-watch-repository.js';
import { AIStudioTrainManualAuditRepository } from '../aistudio-train-manual-audit-repository.js';
import { createAIStudioStateFixture } from './aistudio-train-state.fixture.js';
import { watchFixture } from '../../contracts/__tests__/aistudio-train.fixture.js';
import { manualAuditEvent } from './aistudio-train-manual-audit.fixture.js';

it('reopens a real PRE-AIS SQLite file with identical watch versions/reuse and exactly-once event bindings', async () => {
  const directory=mkdtempSync(join(tmpdir(),'ais-v71-')); const file=join(directory,'state.db');
  let raw=new Database(file);
  try {
    createAIStudioStateFixture(raw);
    raw.exec(`CREATE TABLE flow_events(id INTEGER PRIMARY KEY AUTOINCREMENT,event_id TEXT,flow_id TEXT,workflow_id TEXT,node_id TEXT,
      event_type TEXT,attempt INTEGER,time BIGINT,data_json TEXT,error_text TEXT,gmt_create INTEGER,gmt_modified INTEGER)`);
    const db=new SqliteDatabase(raw); const watches=new AIStudioTrainWatchRepository(db); const w=watchFixture();
    await watches.create(w); expect(await watches.compareAndSwap(1,{...w,version:2,lastStatus:'running'})).toBe(true);
    await new AIStudioTrainManualAuditRepository(db).insertAIStudioTrainManualAudit(manualAuditEvent());
    await db.close(); raw=new Database(file);
    const restartedDb=new SqliteDatabase(raw); const restarted=new AIStudioTrainWatchRepository(restartedDb);
    expect(await restarted.find(w.flowId,w.nodeId,1)).toEqual({...w,version:2,lastStatus:'running'});
    expect(await restarted.create({...w,watchId:'retry-new-id'})).toEqual({...w,version:2,lastStatus:'running'});
    expect(await restarted.compareAndSwap(1,{...w,version:2})).toBe(false);
    expect(await restarted.compareAndSwap(2,{...w,version:3,phase:'resolved'})).toBe(true);
    await expect(restarted.create({...w,watchId:'different-record',recordId:'different'})).rejects.toThrow();
    const e=manualAuditEvent();
    expect(await new AIStudioTrainManualAuditRepository(restartedDb).insertAIStudioTrainManualAudit({...e,time:999,data:{...e.data,rawStatus:'success'}})).toBe(true);
    expect(raw.prepare('SELECT COUNT(*) AS n FROM flow_events').get()).toEqual({n:1});
    const rows=raw.prepare('SELECT config_key,config_yaml,version,enabled FROM cm_app_config').all() as any[];
    expect(rows).toHaveLength(2); expect(rows.every(row => row.enabled===0)).toBe(true);
    const watchRow=rows.find(row => row.config_key.startsWith('aisw:'));
    expect(watchRow.version).toBe(3); expect(JSON.parse(watchRow.config_yaml).version).toBe(3);
    expect(raw.prepare("SELECT name FROM sqlite_master WHERE name LIKE 'aistudio_train_%'").all()).toEqual([]);
  } finally {if(raw.open) raw.close();rmSync(directory,{recursive:true,force:true});}
});
