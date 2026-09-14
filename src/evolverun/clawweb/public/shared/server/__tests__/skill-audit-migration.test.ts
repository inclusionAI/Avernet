import { expect, it } from 'vitest';
import Database from 'better-sqlite3';
import { migrations } from '../schema.js';
import { dialectFor } from '../db/dialect.js';

it('creates an empty audit table without projecting existing versions or tasks', () => {
  const db = new Database(':memory:');
  try {
    db.exec("CREATE TABLE ce_skill_versions (version_id TEXT); INSERT INTO ce_skill_versions VALUES ('historical'); CREATE TABLE ce_tasks (task_id TEXT); INSERT INTO ce_tasks VALUES ('historical-task')");
    const migration = migrations.find(item => item.version === 123)!;
    for (const sql of migration.sql) db.exec(sql);
    expect(db.prepare('SELECT * FROM ce_skill_audit_events').all()).toEqual([]);
    expect(db.prepare('SELECT * FROM ce_skill_versions').all()).toEqual([{ version_id: 'historical' }]);
    expect(db.prepare('PRAGMA index_list(ce_skill_audit_events)').all().length).toBeGreaterThanOrEqual(4);
  } finally { db.close(); }
});

it.each(['mysql', 'zdas'] as const)('renders the audit migration for %s without SQLite-only syntax', (type) => {
  const ddl = migrations.find(item => item.version === 123)!.sql.map(sql => dialectFor(type).renderDdl(sql)).join('\n');
  expect(ddl).toContain('AUTO_INCREMENT');
  expect(ddl).toContain('idempotency_key');
  expect(ddl).not.toMatch(/AUTOINCREMENT|unixepoch\(\)/i);
});
