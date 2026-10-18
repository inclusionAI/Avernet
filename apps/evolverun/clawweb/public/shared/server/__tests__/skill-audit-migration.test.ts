import { expect, it } from 'vitest';
import Database from 'better-sqlite3';
import { migrations } from '../schema.js';
import { SqliteDatabase } from '../db/sqlite-database.js';
import { dialectFor } from '../db/dialect.js';

interface SkillAuditTestMigration {
  migrate?: (db: SqliteDatabase) => Promise<void>;
  sql: string[];
}

async function applyMigration(db: SqliteDatabase, migration: SkillAuditTestMigration) {
  if (migration.migrate) await migration.migrate(db);
  for (const sql of migration.sql) await db.exec(db.dialect.renderDdl(sql));
}

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

it('normalizes historical lifecycle rows into one business event per Skill task', async () => {
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
    const database = new SqliteDatabase(db);
    // Dynamic drift reconciliation and the data copy are both safe after an
    // interrupted migration runner.
    await applyMigration(database, migration);
    await applyMigration(database, migration);
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

it('recovers a pre-baseline Skill snapshot and renames identifiers idempotently', async () => {
  const db = new Database(':memory:');
  const database = new SqliteDatabase(db);
  const column = async (table: string, name: string) => {
    const row = (await database.query<{ name: string; [key: string]: unknown }>(`PRAGMA table_info(${table})`))
      .find(item => item.name === name);
    return row !== undefined;
  };

  try {
    db.exec(`
      CREATE TABLE ce_skill_assets (
        id INTEGER PRIMARY KEY AUTOINCREMENT, asset_id TEXT NOT NULL, owner_user_id TEXT NOT NULL,
        bot_id TEXT NOT NULL, external_skill_id TEXT NOT NULL, display_name TEXT NOT NULL,
        current_version_no INTEGER NOT NULL DEFAULT 1, gmt_create INTEGER NOT NULL,
        gmt_modified INTEGER NOT NULL
      );
      CREATE TABLE ce_skill_audit_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT, idempotency_key TEXT NOT NULL,
        asset_id TEXT NOT NULL, owner_user_id TEXT NOT NULL, bot_id TEXT NOT NULL,
        external_skill_id TEXT NOT NULL, display_name TEXT NOT NULL, description TEXT,
        task_id TEXT, version_id TEXT, version_no INTEGER, event_type TEXT NOT NULL,
        actor_id TEXT, actor_type TEXT NOT NULL, result TEXT NOT NULL, detail_json TEXT,
        gmt_create INTEGER NOT NULL
      );
      CREATE TABLE ce_skill_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT, business_key TEXT NOT NULL,
        asset_id TEXT NOT NULL, owner_user_id TEXT NOT NULL, bot_id TEXT NOT NULL,
        external_skill_id TEXT NOT NULL, display_name TEXT NOT NULL, description TEXT,
        task_id TEXT, event_type TEXT NOT NULL, status TEXT NOT NULL, outcome TEXT,
        actor_id TEXT, actor_type TEXT NOT NULL, version_from_id TEXT, version_to_id TEXT,
        waiting_interaction_id TEXT, summary TEXT, detail_json TEXT, started_at INTEGER,
        completed_at INTEGER, gmt_create INTEGER NOT NULL, gmt_modified INTEGER NOT NULL
      );
      CREATE TABLE ce_tasks (
        task_id TEXT PRIMARY KEY, task_name TEXT, task_type TEXT, status TEXT, gmt_modified INTEGER
      );
      CREATE TABLE ce_skill_versions (
        version_id TEXT, asset_id TEXT, version_no INTEGER, source_task_id TEXT
      );
      CREATE TABLE ce_stage_interactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, interaction_id TEXT, task_id TEXT, step_id TEXT, status TEXT
      );
      CREATE TABLE ce_steps (step_id TEXT PRIMARY KEY, task_id TEXT, status TEXT);
      INSERT INTO ce_skill_assets VALUES
        (1, 'ASSET', 'OWNER', 'BOT', 'SKILL', 'Skill', 1, 1, 1);
      INSERT INTO ce_skill_audit_events VALUES
        (1, 'REGISTER-KEY', 'ASSET', 'OWNER', 'BOT', 'SKILL', 'Skill', 'desc', NULL, 'V1', 1, 'registered', 'OWNER', 'user', 'succeeded', NULL, 10),
        (2, 'START-KEY', 'ASSET', 'OWNER', 'BOT', 'SKILL', 'Skill', 'desc', 'TASK', 'V1', 1, 'evolution_started', 'OWNER', 'user', 'pending', NULL, 20),
        (3, 'APPLIED-KEY', 'ASSET', 'OWNER', 'BOT', 'SKILL', 'Skill', 'desc', 'TASK', 'V2', 2, 'version_applied', 'OWNER', 'user', 'succeeded', NULL, 30);
      INSERT INTO ce_skill_events
        (id, business_key, asset_id, owner_user_id, bot_id, external_skill_id, display_name, description,
         task_id, event_type, status, outcome, actor_id, actor_type, version_from_id, version_to_id,
         waiting_interaction_id, summary, detail_json, started_at, completed_at, gmt_create, gmt_modified)
        VALUES (1, 'existing', 'ASSET', 'OWNER', 'BOT', 'SKILL', 'Skill', NULL, NULL, 'registered', 'completed', 'registered', 'OWNER', 'user', NULL, NULL, NULL, NULL, NULL, 1, 1, 1, 1);
      INSERT INTO ce_tasks VALUES ('TASK', '优化 · Skill', 'full', 'completed', 40);
      INSERT INTO ce_skill_versions VALUES ('V2', 'ASSET', 2, 'TASK');
    `);

    const normalization = migrations.find(item => item.version === 128)!;
    const identifierRename = migrations.find(item => item.version === 132)!;
    await applyMigration(database, normalization);
    await applyMigration(database, normalization);

    expect(await column('ce_skill_audit_events', 'event_id')).toBe(true);
    expect(await column('ce_skill_events', 'event_id')).toBe(true);
    expect(await column('ce_skill_events', 'version_from_no')).toBe(true);
    expect(await column('ce_skill_events', 'version_to_no')).toBe(true);
    expect(await database.query('SELECT event_id FROM ce_skill_events ORDER BY id')).toEqual([
      // Existing post-v128 rows did not have this migration-scoped identifier.
      // It is added only to keep v128 statements executable; v134 later removes it.
      { event_id: null },
      { event_id: 'REGISTER-KEY' },
      { event_id: 'START-KEY' },
    ]);
    expect(await database.query('SELECT ocb_skill_id FROM ce_skill_events ORDER BY id')).toHaveLength(3);

    await applyMigration(database, identifierRename);
    await applyMigration(database, identifierRename);

    for (const table of ['ce_skill_assets', 'ce_skill_audit_events', 'ce_skill_events']) {
      expect(await column(table, 'external_skill_id')).toBe(true);
      expect(await column(table, 'ocb_skill_id')).toBe(false);
    }
  } finally {
    db.close();
  }
});
