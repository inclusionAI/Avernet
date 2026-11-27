import Database from 'better-sqlite3';
import { describe, expect, it } from 'vitest';
import { distributedLockMigrations } from '../schema-distributed-lock.js';
import { aistudioTrainMigrations } from '../schema-aistudio-train.js';
import { workflowSnapshotMigrations } from '../schema-workflow-snapshot.js';
import { readFileSync } from 'node:fs';
import { SqliteDatabase } from '../db/sqlite-database.js';
import { runMigrations, allMigrations } from '../db.js';

describe('AIStudio schema migration', () => {
  it('uses the next free migration after all latest-dev registrations through 154 and is registered in the runner', () => {
    const all = allMigrations;
    expect(all.filter(m => m.version === 155)).toHaveLength(1);
    expect(Math.max(...all.filter(m => m.version < 155).map(m => m.version))).toBe(154);
    expect(workflowSnapshotMigrations.filter(m => m.version === 147 || m.version === 148)).toHaveLength(2);
    expect(aistudioTrainMigrations[0].version).toBe(155);
    expect(readFileSync(new URL('../db/migrations-registry.ts',import.meta.url),'utf8')).toMatch(/\.\.\.aistudioTrainMigrations/);
  });
  it('exports one sorted registry shared by migrations and regression tests', () => {
    const versions=allMigrations.map(m => m.version);
    expect(versions).toEqual([...versions].sort((a,b) => a-b));
    expect(allMigrations.filter(m => m.version === 155)).toHaveLength(1);
  });
  it('runs retired IDs through the actual runner without creating tables, indexes or changing existing rows', async () => {
    const raw=new Database(':memory:'); const db=new SqliteDatabase(raw);
    try {
      raw.exec(`CREATE TABLE schema_version(id INTEGER PRIMARY KEY AUTOINCREMENT,version INTEGER,description TEXT,gmt_create INTEGER DEFAULT (unixepoch()),gmt_modified INTEGER DEFAULT (unixepoch()));
        INSERT INTO schema_version(version,description) VALUES(154,'latest dev');
        CREATE TRIGGER trg_schema_version_update AFTER UPDATE ON schema_version FOR EACH ROW BEGIN UPDATE schema_version SET gmt_modified = (unixepoch()) WHERE id = NEW.id; END;`);
      // Existing installations may still have rejected tables. Retirement must NOT drop or rewrite them.
      raw.exec("CREATE TABLE aistudio_train_watches(payload TEXT); INSERT INTO aistudio_train_watches VALUES('keep');");
      // Provision later additive columns before the snapshot: retired IDs themselves must still be no-DDL.
      raw.exec("CREATE TABLE flow_runs(id INTEGER PRIMARY KEY,gmt_modified INTEGER,parent_flow_id VARCHAR(255) DEFAULT NULL,root_flow_id VARCHAR(255) DEFAULT NULL);");
      raw.exec("CREATE TRIGGER trg_flow_runs_update AFTER UPDATE ON flow_runs FOR EACH ROW BEGIN UPDATE flow_runs SET gmt_modified = (unixepoch()) WHERE id = NEW.id; END;");
      // Provision later additive schema before asserting retired migrations are no-DDL.
      await distributedLockMigrations[0].migrate!(db);
      const schema=raw.prepare('SELECT * FROM sqlite_master ORDER BY name').all();
      await runMigrations(db,'sqlite');
      expect(raw.prepare('SELECT MAX(version) AS v FROM schema_version').get()).toEqual({v:Math.max(...allMigrations.map(m => m.version))});
      expect(raw.prepare('SELECT * FROM sqlite_master ORDER BY name').all()).toEqual(schema);
      expect(raw.prepare('SELECT * FROM aistudio_train_watches').all()).toEqual([{payload:'keep'}]);
      for(const m of aistudioTrainMigrations) {expect(m.sql).toEqual([]); await m.migrate?.(db);}
      await runMigrations(db,'sqlite');
      expect(raw.prepare('SELECT COUNT(*) AS n FROM schema_version WHERE version=155').get()).toEqual({n:1});
      expect(raw.prepare('SELECT COUNT(*) AS n FROM schema_version WHERE version=156').get()).toEqual({n:1});
    } finally { raw.close(); }
  });
  it.each(['sqlite','mysql','zdas','noop'] as const)('retired registrations have zero DDL or callback side effects on %s', async dbType => {
    const calls:string[]=[]; const db={dbType,exec:async (sql:string) => {calls.push(sql);throw new Error('DDL forbidden');},query:async (sql:string) => {calls.push(sql);throw new Error('probe forbidden');}};
    for(const m of aistudioTrainMigrations) {
      expect(m.sql).toEqual([]); expect(m.description).toMatch(/retired/i);
      await m.migrate?.(db as any);
    }
    expect(calls).toEqual([]);
  });
  it('leaves already-applied legacy 155/156 ledger entries and their original descriptions untouched', async () => {
    const raw=new Database(':memory:'); const db=new SqliteDatabase(raw);
    try {
      raw.exec(`CREATE TABLE schema_version(id INTEGER PRIMARY KEY AUTOINCREMENT,version INTEGER,description TEXT,gmt_create INTEGER DEFAULT (unixepoch()),gmt_modified INTEGER DEFAULT (unixepoch()));
        INSERT INTO schema_version(version,description) VALUES(154,'latest dev'),(155,'original watch migration'),(156,'original audit migration');
        CREATE TRIGGER trg_schema_version_update AFTER UPDATE ON schema_version FOR EACH ROW BEGIN UPDATE schema_version SET gmt_modified = (unixepoch()) WHERE id = NEW.id; END;`);
      // Provision later additive columns before the snapshot: retired IDs themselves must still be no-DDL.
      raw.exec("CREATE TABLE flow_runs(id INTEGER PRIMARY KEY,gmt_modified INTEGER,parent_flow_id VARCHAR(255) DEFAULT NULL,root_flow_id VARCHAR(255) DEFAULT NULL);");
      raw.exec("CREATE TRIGGER trg_flow_runs_update AFTER UPDATE ON flow_runs FOR EACH ROW BEGIN UPDATE flow_runs SET gmt_modified = (unixepoch()) WHERE id = NEW.id; END;");
      // Provision later additive schema before asserting retired migrations are no-DDL.
      await distributedLockMigrations[0].migrate!(db);
      const schema=raw.prepare('SELECT * FROM sqlite_master ORDER BY name').all();
      const ledger=raw.prepare('SELECT * FROM schema_version ORDER BY id').all();
      await runMigrations(db,'sqlite');
      expect(raw.prepare('SELECT * FROM schema_version WHERE version <= 156 ORDER BY id').all()).toEqual(ledger);
      expect(raw.prepare('SELECT * FROM sqlite_master ORDER BY name').all()).toEqual(schema);
    } finally {raw.close();}
  });

});
