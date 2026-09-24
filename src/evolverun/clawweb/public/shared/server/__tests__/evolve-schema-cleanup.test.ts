import Database from 'better-sqlite3';
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { SqliteDatabase, runMigrations } from '../db.js';
import { migrations } from '../schema.js';
import { cleanupEvolveSchema } from '../migrations/evolve-schema-cleanup.js';
import { evolveTables } from '../migrations/evolve-schema.js';

async function legacyDatabase() {
  const db = new SqliteDatabase(new Database(':memory:'));
  await db.exec('CREATE TABLE schema_version (id INTEGER PRIMARY KEY AUTOINCREMENT, version INTEGER NOT NULL, description TEXT, gmt_create INTEGER, gmt_modified INTEGER)');
  for (const migration of migrations.filter(item => item.version <= 134 && !item.mysqlOnly)) {
    for (const sql of migration.sql) {
      try { await db.exec(db.dialect.renderDdl(sql)); }
      catch (error) {
        if (!/duplicate column name|already exists|no such column/.test(String(error))) throw error;
      }
    }
  }
  return db;
}

const tableNames = evolveTables.map(sql => /CREATE TABLE IF NOT EXISTS (\w+)/.exec(sql)![1]);

async function seed(db: SqliteDatabase) {
  await db.exec(`INSERT INTO ce_stage_developments
    (stage_skill_id, owner_user_id, display_name, flow_key, stage_key, extension_mode, gmt_create, gmt_modified)
    VALUES ('STAGESKILL-OLD', 'owner', 'name', 'skill_hardening', 'hardening', 'replace', 100, 101)`);
  await db.exec(`INSERT INTO ce_stage_skill_implementations
    (stage_skill_id, implementation_id, owner_user_id, display_name, stage_key, extension_mode,
      version_no, package_ref, package_sha256, static_validation_json)
    VALUES ('STAGESKILL-OLD', 'IMPL-OLD', 'owner', 'name', 'hardening', 'replace', 1, 'package', 'digest', '{}')`);
  await db.exec(`INSERT INTO ce_skill_assets
    (asset_id, owner_user_id, bot_id, external_skill_id, display_name, current_package_ref, current_package_sha256)
    VALUES ('ASSET', 'owner', 'bot', 'external', 'skill', 'package', 'digest')`);
  await db.exec(`INSERT INTO ce_skill_versions
    (version_id, asset_id, version_no, package_ref, package_sha256, status)
    VALUES ('VERSION', 'ASSET', 1, 'package', 'digest', 'baseline')`);
  await db.exec(`INSERT INTO ce_skill_events
    (event_id, business_key, asset_id, owner_user_id, bot_id, external_skill_id, display_name,
      event_type, status, actor_type, version_from_id, version_from_no, started_at)
    VALUES ('EVENT', 'register:ASSET', 'ASSET', 'owner', 'bot', 'external', 'skill',
      'registered', 'completed', 'user', 'VERSION', 1, 100)`);
  await db.exec(`INSERT INTO ce_tasks
    (task_id, task_name, task_type, user_id, bot_id, config_json, created_by)
    VALUES ('TASK', 'task', 'hardening', 'owner', 'bot', ?, 'owner')`, [JSON.stringify({
    stageExtensions: { hardening: { replace: { implementationId: 'IMPL-OLD', stageSkillId: 'STAGESKILL-OLD' } } },
    businessInput: { stageSkillId: 'STAGESKILL-OLD' },
  })]);
  await db.exec(`INSERT INTO ce_app_config (config_key, config_json) VALUES ('skill_task_stage_bindings', ?)`,
    [JSON.stringify({ bindings: [{ stageSkillId: 'STAGESKILL-OLD' }] })]);
}

describe('Evolve schema simplification', () => {
  it('preserves record IDs, relationships, package snapshots, versions and frozen bindings', async () => {
    const db = await legacyDatabase();
    try {
      await seed(db);
      await cleanupEvolveSchema(db);
      const development = (await db.query<{ id: number }>('SELECT * FROM ce_stage_developments'))[0];
      expect(development.id).toBe(1);
      expect(development).not.toHaveProperty('stage_skill_id');
      expect(await db.query('SELECT stage_skill_id, implementation_id FROM ce_stage_skill_implementations'))
        .toEqual([{ stage_skill_id: '1', implementation_id: 'IMPL-OLD' }]);
      expect(await db.query(`SELECT a.id, v.package_ref, v.package_sha256 FROM ce_skill_assets a
        JOIN ce_skill_versions v ON v.asset_id = a.asset_id AND v.version_no = a.current_version_no`))
        .toEqual([{ id: 1, package_ref: 'package', package_sha256: 'digest' }]);
      expect(await db.query(`SELECT e.id, e.business_key, v.version_no FROM ce_skill_events e
        JOIN ce_skill_versions v ON v.version_id = e.version_from_id`))
        .toEqual([{ id: 1, business_key: 'register:ASSET', version_no: 1 }]);
      const config = JSON.parse((await db.query<{ config_json: string }>('SELECT config_json FROM ce_tasks'))[0].config_json);
      expect(config.stageExtensions.hardening.replace).toEqual({ implementationId: 'IMPL-OLD', stageSkillId: '1' });
      expect(config.businessInput).toEqual({ stageSkillId: 'STAGESKILL-OLD' });
      const binding = JSON.parse((await db.query<{ config_json: string }>('SELECT config_json FROM ce_app_config'))[0].config_json);
      expect(binding.bindings[0].stageSkillId).toBe('1');
      for (const table of tableNames) {
        const columns = await db.query<{ name: string; pk: number }>(`PRAGMA table_info(${table})`);
        expect(columns.find(column => column.name === 'id')?.pk, table).toBe(1);
        expect((await db.query<{ origin: string }>(`PRAGMA index_list(${table})`)).filter(index => index.origin === 'c'), table).toEqual([]);
      }
      const before = await db.query('SELECT * FROM ce_skill_events');
      await cleanupEvolveSchema(db);
      expect(await db.query('SELECT * FROM ce_skill_events')).toEqual(before);
      expect(await db.query("SELECT name FROM sqlite_master WHERE name LIKE '%_v13_'")).toEqual([]);
    } finally { await db.close(); }
  });

  it('refuses to discard a package snapshot that disagrees with its version', async () => {
    const db = await legacyDatabase();
    try {
      await seed(db);
      await db.exec("UPDATE ce_skill_assets SET current_package_ref = 'different'");
      await expect(cleanupEvolveSchema(db)).rejects.toThrow('inconsistent current Skill snapshot');
      expect(await db.query('SELECT current_package_ref FROM ce_skill_assets')).toEqual([{ current_package_ref: 'different' }]);
      expect(await db.query('SELECT stage_skill_id FROM ce_stage_developments')).toEqual([{ stage_skill_id: 'STAGESKILL-OLD' }]);
    } finally { await db.close(); }
  });

  it('rolls back SQLite copies and reference changes if configuration cannot be read', async () => {
    const db = await legacyDatabase();
    try {
      await seed(db);
      await db.exec("UPDATE ce_app_config SET config_json = 'invalid'");
      await expect(cleanupEvolveSchema(db)).rejects.toThrow();
      expect(await db.query('SELECT event_id FROM ce_skill_events')).toEqual([{ event_id: 'EVENT' }]);
      expect(await db.query("SELECT name FROM sqlite_master WHERE name LIKE '%_v13_'")).toEqual([]);
    } finally { await db.close(); }
  });

  it('preserves numeric group IDs, reserves legacy orphan groups and never reuses deleted row IDs', async () => {
    const db = await legacyDatabase();
    try {
      await seed(db);
      await db.exec("UPDATE ce_stage_skill_implementations SET stage_skill_id = '9'");
      await db.exec("UPDATE sqlite_sequence SET seq = 50 WHERE name = 'ce_skill_events'");
      await cleanupEvolveSchema(db);
      expect(await db.query('SELECT stage_skill_id FROM ce_stage_skill_implementations')).toEqual([{ stage_skill_id: '9' }]);
      const created = await db.exec(`INSERT INTO ce_stage_developments
        (owner_user_id, display_name, flow_key, stage_key, extension_mode)
        VALUES ('owner', 'new', 'skill_hardening', 'hardening', 'replace')`, []);
      expect(created.insertId).toBe(10);
      expect(await db.query("SELECT seq FROM sqlite_sequence WHERE name = 'ce_skill_events'")).toEqual([{ seq: 50 }]);
    } finally { await db.close(); }
  });

  it('refuses to discard a version number that disagrees with its referenced version', async () => {
    const db = await legacyDatabase();
    try {
      await seed(db);
      await db.exec('UPDATE ce_skill_events SET version_from_no = 2');
      await expect(cleanupEvolveSchema(db)).rejects.toThrow('inconsistent version snapshot');
      expect(await db.query('SELECT event_id, version_from_no FROM ce_skill_events'))
        .toEqual([{ event_id: 'EVENT', version_from_no: 2 }]);
    } finally { await db.close(); }
  });

  it('ships all nine tables with the same columns and constraints as runtime migrations', async () => {
    const db = new SqliteDatabase(new Database(':memory:'));
    const delivery = new SqliteDatabase(new Database(':memory:'));
    try {
      await runMigrations(db, 'sqlite');
      const ddl = readFileSync(new URL('../../../../../docs/clawweb/evolve-schema-v135/01-new-tables.mysql.sql', import.meta.url), 'utf8');
      for (const statement of ddl.replace(/^--.*$/gm, '').split(';').map(item => item.trim()).filter(Boolean)) {
        await delivery.exec(delivery.dialect.renderDdl(statement));
      }
      for (const table of tableNames) {
        expect(await delivery.query(`PRAGMA table_info(${table})`)).toEqual(await db.query(`PRAGMA table_info(${table})`));
        const keys = async (database: SqliteDatabase) => Promise.all((await database.query<{ name: string; unique: number }>(`PRAGMA index_list(${table})`))
          .filter(index => index.unique).map(index => database.query(`PRAGMA index_info(${index.name})`)));
        expect(await keys(delivery)).toEqual(await keys(db));
      }
    } finally { await db.close(); await delivery.close(); }
  });
});
