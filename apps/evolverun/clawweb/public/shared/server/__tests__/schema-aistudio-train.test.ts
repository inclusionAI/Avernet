import Database from 'better-sqlite3';
import { describe, expect, it } from 'vitest';
import { aistudioTrainDdl, aistudioTrainMigrations } from '../schema-aistudio-train.js';
import { workflowSnapshotMigrations } from '../schema-workflow-snapshot.js';
import { readFileSync } from 'node:fs';
import { SqliteDatabase } from '../db/sqlite-database.js';
import { runMigrations, allMigrations } from '../db.js';

describe('AIStudio schema migration', () => {
  it('uses the next free migration after all latest-dev registrations through 154 and is registered in the runner', () => {
    const all = allMigrations;
    expect(all.filter(m => m.version === 155)).toHaveLength(1);
    expect(Math.max(...all.filter(m => !aistudioTrainMigrations.includes(m)).map(m => m.version))).toBe(154);
    expect(workflowSnapshotMigrations.filter(m => m.version === 147 || m.version === 148)).toHaveLength(2);
    expect(aistudioTrainMigrations[0].version).toBe(155);
    expect(readFileSync(new URL('../db/migrations-registry.ts',import.meta.url),'utf8')).toMatch(/\.\.\.aistudioTrainMigrations/);
  });
  it('exports one sorted registry shared by migrations and regression tests', () => {
    const versions=allMigrations.map(m => m.version);
    expect(versions).toEqual([...versions].sort((a,b) => a-b));
    expect(allMigrations.filter(m => m.version === 155)).toHaveLength(1);
  });
  it('creates the watch table with mandatory gmt metadata and SQLite indexes', () => {
    const raw = new Database(':memory:');
    try {
      for (const ddl of aistudioTrainDdl('sqlite')) raw.exec(ddl);
      const columns = raw.prepare('PRAGMA table_info(aistudio_train_watches)').all() as {name:string}[];
      expect(columns.map(c => c.name)).toEqual(expect.arrayContaining(['id','gmt_create','gmt_modified']));
      expect(raw.prepare('PRAGMA index_list(aistudio_train_watches)').all().length).toBeGreaterThanOrEqual(4);
    } finally { raw.close(); }
  });
  it('runs v155 through the actual migration runner and can replay DDL without losing rows', async () => {
    const raw=new Database(':memory:'); const db=new SqliteDatabase(raw);
    try {
      raw.exec("CREATE TABLE schema_version(version INTEGER,description TEXT); INSERT INTO schema_version VALUES(154,'latest dev');");
      await runMigrations(db,'sqlite');
      expect(raw.prepare('SELECT MAX(version) AS v FROM schema_version').get()).toEqual({v:156});
      expect(raw.prepare("SELECT name FROM sqlite_master WHERE name='aistudio_train_watches'").get()).toBeTruthy();
      await aistudioTrainMigrations[0].migrate!(db);
      await runMigrations(db,'sqlite');
      expect(raw.prepare('SELECT COUNT(*) AS n FROM schema_version WHERE version=155').get()).toEqual({n:1});
    } finally { raw.close(); }
  });
  it.each(['mysql','zdas'] as const)('renders %s with inline indexes and epoch BIGINT columns, gmt TIMESTAMP only', type => {
    const statements = aistudioTrainDdl(type); expect(statements).toHaveLength(1);
    const ddl = statements[0];
    expect(ddl).toMatch(/INDEX idx_aistudio_watch_due/); expect(ddl).not.toMatch(/CREATE INDEX/);
    expect(ddl).toMatch(/submitted_at BIGINT NOT NULL/); expect(ddl).toMatch(/lease_expires_at BIGINT/);
    expect(ddl).toMatch(/gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP/);
    expect(ddl).toMatch(/flow_id VARCHAR\(255\).*ascii_bin/); expect(ddl).toMatch(/UNIQUE.*flow_id, node_id, attempt/);
  });
});
