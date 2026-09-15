import { expect, it } from 'vitest';
import Database from 'better-sqlite3';
import { migrations } from '../schema.js';
import { dialectFor } from '../db/dialect.js';

it('creates an empty audit table without projecting existing versions or tasks', () => {
  const db = new Database(':memory:');
  try {
    db.exec("CREATE TABLE ce_skill_versions (version_id TEXT); INSERT INTO ce_skill_versions VALUES ('historical'); CREATE TABLE ce_tasks (task_id TEXT); INSERT INTO ce_tasks VALUES ('historical-task')");
    const migration = migrations.find(item => item.description === 'Persist operation-time Skill audit records without historical backfill')!;
    for (const sql of migration.sql) db.exec(dialectFor('sqlite').renderDdl(sql));
    expect(db.prepare('SELECT * FROM ce_skill_audit_events').all()).toEqual([]);
    expect(db.prepare('SELECT * FROM ce_skill_versions').all()).toEqual([{ version_id: 'historical' }]);
    expect(db.prepare('PRAGMA index_list(ce_skill_audit_events)').all().length).toBeGreaterThanOrEqual(4);
  } finally { db.close(); }
});

it.each(['mysql', 'zdas'] as const)('renders the audit migration for %s without SQLite-only syntax', (type) => {
  const ddl = migrations.find(item => item.description === 'Persist operation-time Skill audit records without historical backfill')!.sql.map(sql => dialectFor(type).renderDdl(sql)).join('\n');
  expect(ddl).toContain('AUTO_INCREMENT');
  expect(ddl).toContain('idempotency_key');
  expect(ddl).not.toMatch(/AUTOINCREMENT|unixepoch\(\)/i);
});

it('normalizes historical lifecycle rows into one business event per Skill task', () => {
  const db = new Database(':memory:');
  try {
    db.exec(`
      CREATE TABLE ce_skill_audit_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT, idempotency_key TEXT,
        asset_id TEXT, owner_user_id TEXT, bot_id TEXT, ocb_skill_id TEXT,
        display_name TEXT, description TEXT, task_id TEXT, version_id TEXT,
        version_no INTEGER, event_type TEXT, actor_id TEXT, actor_type TEXT,
        result TEXT, detail_json TEXT, gmt_create INTEGER
      );
      CREATE TABLE ce_tasks (
        task_id TEXT PRIMARY KEY, task_name TEXT, task_type TEXT, status TEXT,
        gmt_modified INTEGER
      );
      CREATE TABLE ce_skill_versions (
        version_id TEXT, asset_id TEXT, version_no INTEGER, source_task_id TEXT
      );
      CREATE TABLE ce_stage_interactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, interaction_id TEXT, task_id TEXT, step_id TEXT, status TEXT
      );
      CREATE TABLE ce_steps (
        step_id TEXT PRIMARY KEY, task_id TEXT, status TEXT
      );
      INSERT INTO ce_skill_audit_events VALUES
        (1, 'REGISTER', 'REGISTER-KEY', 'ASSET', 'OWNER', 'BOT', 'SKILL', 'Skill', 'desc', NULL, 'V1', 1, 'registered', 'OWNER', 'user', 'succeeded', NULL, 10),
        (2, 'START', 'START-KEY', 'ASSET', 'OWNER', 'BOT', 'SKILL', 'Skill', 'desc', 'TASK', 'V1', 1, 'evolution_started', 'OWNER', 'user', 'pending', NULL, 20),
        (3, 'FINISH', 'FINISH-KEY', 'ASSET', 'OWNER', 'BOT', 'SKILL', 'Skill', 'desc', 'TASK', 'V1', 1, 'evolution_finished', NULL, 'system', 'waiting_acceptance', '{"testBench":{"taskId":"TASK"}}', 30),
        (4, 'APPLIED', 'APPLIED-KEY', 'ASSET', 'OWNER', 'BOT', 'SKILL', 'Skill', 'desc', 'TASK', 'V2', 2, 'version_applied', 'OWNER', 'user', 'succeeded', '{"testBench":{"taskId":"TASK","stepId":"OPT","round":1,"scoreComparison":null}}', 40);
      INSERT INTO ce_tasks VALUES ('TASK', '优化 · Skill', 'full', 'running', 50);
      INSERT INTO ce_skill_versions VALUES ('V2', 'ASSET', 2, 'TASK');
      INSERT INTO ce_steps VALUES ('STEP', 'TASK', 'waiting_context');
      INSERT INTO ce_stage_interactions VALUES (1, 'HITL', 'TASK', 'STEP', 'waiting');
    `);
    const migration = migrations.find(item => item.description === 'Normalize Skill history into one business event per task')!;
    for (const sql of migration.sql) db.exec(dialectFor('sqlite').renderDdl(sql));
    // The data copy is safe to retry after an interrupted migration runner.
    for (const sql of migration.sql) db.exec(dialectFor('sqlite').renderDdl(sql));
    expect(db.prepare(`SELECT event_type, status, outcome, version_from_id, version_from_no,
      version_to_id, version_to_no, waiting_interaction_id, started_at, completed_at
      FROM ce_skill_events ORDER BY id`).all()).toEqual([
      { event_type: 'registered', status: 'completed', outcome: 'succeeded', version_from_id: 'V1',
        version_from_no: 1, version_to_id: null, version_to_no: null, waiting_interaction_id: null,
        started_at: 10, completed_at: 10 },
      { event_type: 'optimization', status: 'waiting_user_input', outcome: 'succeeded', version_from_id: 'V1',
        version_from_no: 1, version_to_id: 'V2', version_to_no: 2, waiting_interaction_id: 'HITL',
        started_at: 20, completed_at: null },
    ]);
    expect(db.prepare("SELECT COUNT(*) AS count FROM ce_skill_events WHERE task_id = 'TASK'").get()).toEqual({ count: 1 });
    expect(db.prepare('SELECT COUNT(*) AS count FROM ce_skill_audit_events').get()).toEqual({ count: 4 });
  } finally { db.close(); }
});

it.each(['mysql', 'zdas'] as const)('renders the normalized Skill event schema for %s', (type) => {
  const migration = migrations.find(item => item.description === 'Normalize Skill history into one business event per task')!;
  const rendered = migration.sql.map(sql => dialectFor(type).renderDdl(sql)).join('\n');
  expect(rendered).toContain('CREATE TABLE IF NOT EXISTS ce_skill_events');
  expect(rendered).toContain('AUTO_INCREMENT');
  expect(rendered).not.toMatch(/AUTOINCREMENT|unixepoch\(\)/i);
});
