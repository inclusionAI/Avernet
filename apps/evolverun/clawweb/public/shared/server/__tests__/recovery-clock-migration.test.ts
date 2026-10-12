import { expect, it } from 'vitest';
import Database from 'better-sqlite3';
import { migrations } from '../schema.js';
import { sqliteDialect, mysqlDialect, zdasDialect } from '../db/dialect.js';

it('adds recovery tables to existing runs without changing history, and is repeatable', () => {
  const db = new Database(':memory:');
  try {
    db.exec("CREATE TABLE flow_runs (flow_id TEXT, started_at INTEGER, status TEXT); INSERT INTO flow_runs VALUES ('old',123,'failed')");
    const migration = migrations.find(m => m.version === 140)!;
    expect(migration).toBeDefined();
    for (let i = 0; i < 2; i++) for (const ddl of migration.sql) db.exec(sqliteDialect.renderDdl(ddl));
    expect(db.prepare('SELECT * FROM flow_runs').get()).toEqual({ flow_id: 'old', started_at: 123, status: 'failed' });
    db.prepare(`INSERT INTO flow_recovery_clocks (flow_id,owner_instance_id,boot_id,timeout_epoch,clock_seq,elapsed_ms,phase)
      VALUES (?,?,?,?,?,?,?)`).run('old','owner','boot',0,1,2**40,'recovering');
    expect(db.prepare('SELECT elapsed_ms FROM flow_recovery_clocks').get()).toEqual({ elapsed_ms: 2**40 });
    expect(() => db.prepare(`INSERT INTO flow_recovery_clocks (flow_id,owner_instance_id,boot_id,timeout_epoch,clock_seq,elapsed_ms,phase)
      VALUES (?,?,?,?,?,?,?)`).run('old','other','boot',0,1,0,'recovering')).toThrow('UNIQUE');
  } finally { db.close(); }
});
it.each([mysqlDialect, zdasDialect])('renders deployable $name DDL with bigint clocks and bounded unique keys', dialect => {
  const sql = migrations.find(m => m.version === 140)!.sql.map(ddl => dialect.renderDdl(ddl)).join('\n');
  expect(sql).toContain('elapsed_ms BIGINT');
  expect(sql).toContain('ON UPDATE CURRENT_TIMESTAMP');
  expect(sql).not.toMatch(/AUTOINCREMENT|unixepoch\(\)|CREATE UNIQUE INDEX/);
  expect(sql).toContain('UNIQUE (idempotency_key)');
  expect(sql).toContain('idempotency_key VARCHAR(64)');
});
