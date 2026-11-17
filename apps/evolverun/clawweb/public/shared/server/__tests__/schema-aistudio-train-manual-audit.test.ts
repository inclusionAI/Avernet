import Database from 'better-sqlite3';
import { describe,expect,it } from 'vitest';
import { allMigrations } from '../db.js';
import { aistudioTrainManualAuditMigrations } from '../schema-aistudio-train-manual-audit.js';
import { SqliteDatabase } from '../db/sqlite-database.js';
describe('retired manual audit migration', () => {
  it('retains unique v156 as a no-DDL tombstone without changing pre-existing history', async () => {
    expect(allMigrations.filter(m => m.version===156)).toHaveLength(1);
    const raw=new Database(':memory:');
    try {
      raw.exec("CREATE TABLE flow_events(event_id TEXT); INSERT INTO flow_events VALUES('duplicate'),('duplicate');");
      raw.exec("CREATE TABLE aistudio_train_manual_audits(identity_json TEXT); INSERT INTO aistudio_train_manual_audits VALUES('keep');");
      const schema=raw.prepare('SELECT * FROM sqlite_master ORDER BY name').all();
      for(const m of aistudioTrainManualAuditMigrations) {expect(m.sql).toEqual([]); await m.migrate?.(new SqliteDatabase(raw));}
      expect(raw.prepare('SELECT * FROM sqlite_master ORDER BY name').all()).toEqual(schema);
      expect(raw.prepare('SELECT * FROM flow_events').all()).toEqual([{event_id:'duplicate'},{event_id:'duplicate'}]);
      expect(raw.prepare('SELECT * FROM aistudio_train_manual_audits').all()).toEqual([{identity_json:'keep'}]);
    } finally {raw.close();}
  });
});
