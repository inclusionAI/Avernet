import Database from 'better-sqlite3';
import { describe, expect, it } from 'vitest';
import { SqliteDatabase, runMigrations } from '../db.js';
import { feature, upstream, type HistoricalMigration } from './fixtures/evolve-merge-history.js';

type Row = Record<string, unknown>;

const featureDescriptions = [
  'Add Stage Skill extensions, HITL audit, and registered Skill version assets',
  'Keep Stage Skill registration and integration-test verification as separate states',
  'Persist custom Stage development before package upload',
  'Persist operation-time Skill audit records without historical backfill',
];
const upstreamDescription = 'Add owner_id to workflow_specs so save can persist the current user alongside deploy history';
const stageTables = [
  'ce_stage_skill_implementations',
  'ce_stage_extension_runs',
  'ce_stage_interactions',
  'ce_skill_assets',
  'ce_skill_versions',
  'ce_stage_developments',
  'ce_skill_audit_events',
] as const;

// The fixture documents the minimal simulated <=119 baseline. Execute all
// captured SQL strictly: malformed history must fail rather than silently pass.
async function applyHistory(db: SqliteDatabase, history: HistoricalMigration[], through: number) {
  await db.exec(`CREATE TABLE schema_version (
    id INTEGER PRIMARY KEY AUTOINCREMENT, version INTEGER NOT NULL,
    description TEXT, gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
    gmt_modified INTEGER NOT NULL DEFAULT (unixepoch()))`);
  for (const migration of history) {
    if (migration.version > through) continue;
    for (const sql of migration.sql) {
      await db.exec(db.dialect.renderDdl(sql));
    }
    await db.exec('INSERT INTO schema_version (version, description) VALUES (?, ?)',
      [migration.version, migration.description]);
  }
}

async function columns(db: SqliteDatabase, table: string) {
  return (await db.query<{ name: string }>(`PRAGMA table_info(${table})`)).map(row => row.name);
}

async function seedWorkflow(db: SqliteDatabase, hasOwner: boolean) {
  await db.exec(`INSERT INTO workflow_specs
    (workflow_id, pack_id, spec_json, title, version, gmt_create, gmt_modified)
    VALUES ('merge-workflow', 'merge-pack', '{"nodes":["keep-me"]}', 'Keep workflow', 7, 100, 101)`);
  if (hasOwner) {
    await db.exec("UPDATE workflow_specs SET owner_id = 'existing-owner' WHERE workflow_id = 'merge-workflow'");
  }
}

async function seedStages(db: SqliteDatabase, through: number) {
  await db.exec(`INSERT INTO ce_stage_skill_implementations
    (stage_skill_id, implementation_id, owner_user_id, display_name, stage_key,
     extension_mode, version_no, package_ref, package_sha256, static_validation_json,
     integration_test_task_id, gmt_create, gmt_modified)
    VALUES ('stage-keep', 'impl-keep', 'user-keep', 'Keep stage', 'plan', 'replace',
      3, 'fixture:stage', 'stage-digest', '{"valid":true}', 'test-task-keep', 100, 101)`);
  await db.exec(`INSERT INTO ce_stage_extension_runs
    (step_id, task_id, stage_key, extension_mode, implementation_id, initial_input_json, gmt_create)
    VALUES ('step-keep', 'task-keep', 'plan', 'replace', 'impl-keep', '{"keep":true}', 102)`);
  await db.exec(`INSERT INTO ce_stage_interactions
    (interaction_id, task_id, step_id, attempt_no, status, request_json, response_json, gmt_create, gmt_modified)
    VALUES ('interaction-keep', 'task-keep', 'step-keep', 2, 'answered',
      '{"question":"keep"}', '{"answer":"keep"}', 103, 104)`);
  await db.exec(`INSERT INTO ce_skill_assets
    (asset_id, owner_user_id, bot_id, ocb_skill_id, display_name, current_version_no,
     current_package_ref, current_package_sha256, gmt_create, gmt_modified)
    VALUES ('asset-keep', 'user-keep', 'bot-keep', 'skill-keep', 'Keep skill', 3,
      'fixture:skill', 'skill-digest', 105, 106)`);
  await db.exec(`INSERT INTO ce_skill_versions
    (version_id, asset_id, version_no, package_ref, package_sha256, source_task_id,
     baseline_package_ref, baseline_package_sha256, status, gmt_create)
    VALUES ('version-keep', 'asset-keep', 3, 'fixture:skill', 'skill-digest', 'task-keep',
      'fixture:baseline', 'baseline-digest', 'registered', 107)`);
  if (through >= 121) {
    await db.exec("UPDATE ce_stage_skill_implementations SET integration_test_status = 'passed'");
  }
  if (through >= 122) {
    await db.exec("UPDATE ce_skill_assets SET description = 'Historical description'");
    await db.exec(`INSERT INTO ce_stage_developments
      (stage_skill_id, owner_user_id, display_name, flow_key, stage_key, extension_mode, gmt_create, gmt_modified)
      VALUES ('development-keep', 'user-keep', 'Keep development', 'evolve', 'plan', 'replace', 108, 109)`);
  }
  if (through >= 123) {
    await db.exec(`INSERT INTO ce_skill_audit_events
      (event_id, idempotency_key, asset_id, owner_user_id, bot_id, ocb_skill_id,
       display_name, description, task_id, version_id, version_no, event_type,
       actor_id, actor_type, result, detail_json, gmt_create)
      VALUES ('event-keep', 'idempotency-keep', 'asset-keep', 'user-keep', 'bot-keep',
        'skill-keep', 'Keep skill', 'Historical audit', 'task-keep', 'version-keep', 3,
        'registered', 'user-keep', 'user', 'success', '{"keep":true}', 110)`);
  }
}

async function rows(db: SqliteDatabase, tables: readonly string[]) {
  return Object.fromEntries(await Promise.all(tables.map(async table => [
    table, await db.query<Row>(`SELECT * FROM ${table} ORDER BY rowid`),
  ] as const)));
}

async function assertMergedSchema(db: SqliteDatabase) {
  expect(await columns(db, 'workflow_specs')).toContain('owner_id');
  expect(await db.query('PRAGMA index_info(idx_workflow_specs_owner_id)')).toEqual([
    expect.objectContaining({ name: 'owner_id' }),
  ]);
  for (const table of stageTables) {
    expect(await columns(db, table), table).not.toHaveLength(0);
  }
  expect(await columns(db, 'ce_stage_skill_implementations')).toContain('integration_test_status');
  expect(await columns(db, 'ce_skill_assets')).toContain('description');
  // Do not cap MAX(version) at 125: an independent Space migration may follow.
  expect(await db.query('SELECT version FROM schema_version WHERE version = 125')).toEqual([{ version: 125 }]);
  // Pre-merge history already contains duplicate v36/v99 entries. Constrain
  // uniqueness to this merge range; rerun equality covers the entire ledger.
  expect(await db.query(`SELECT version FROM schema_version WHERE version >= 120
    GROUP BY version HAVING COUNT(*) > 1`)).toEqual([]);
}

async function assertIdempotent(db: SqliteDatabase) {
  const tables = ['schema_version', 'workflow_specs', ...stageTables];
  const before = await rows(db, tables);
  const schema = await db.query('SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name');
  await runMigrations(db, 'sqlite');
  expect(await rows(db, tables)).toEqual(before);
  expect(await db.query('SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name')).toEqual(schema);
  await assertMergedSchema(db);
}

describe('Evolve merge migration compatibility (real SQLite)', () => {
  it('fresh install creates both migration lines and preserves seeds on rerun', async () => {
    const db = new SqliteDatabase(new Database(':memory:'));
    try {
      await runMigrations(db, 'sqlite');
      await assertMergedSchema(db);
      await seedWorkflow(db, false);
      expect(await db.query("SELECT owner_id FROM workflow_specs WHERE workflow_id = 'merge-workflow'"))
        .toEqual([{ owner_id: null }]);
      await seedStages(db, 123);
      await db.exec("UPDATE workflow_specs SET owner_id = 'new-owner' WHERE workflow_id = 'merge-workflow'");
      await assertIdempotent(db);
    } finally {
      await db.close();
    }
  });

  it.each([
    { line: 'upstream', through: 120 },
    { line: 'feature', through: 120 },
    { line: 'feature', through: 121 },
    { line: 'feature', through: 122 },
    { line: 'feature', through: 123 },
  ] as const)('upgrades $line history through v$through without losing seeds and is idempotent', async ({ line, through }) => {
    const db = new SqliteDatabase(new Database(':memory:'));
    try {
      const isFeature = line === 'feature';
      const history = isFeature ? feature : upstream;
      expect(history.filter(m => m.version >= 120).map(m => m.description))
        .toEqual(isFeature ? featureDescriptions : [upstreamDescription]);
      await applyHistory(db, history, through);
      expect(await db.query('SELECT MAX(version) AS version FROM schema_version')).toEqual([{ version: through }]);
      const workflowColumns = await columns(db, 'workflow_specs');
      expect(workflowColumns.includes('owner_id')).toBe(!isFeature);
      const historicalTables = ['workflow_specs'];
      await seedWorkflow(db, !isFeature);
      if (isFeature) {
        expect((await columns(db, 'ce_stage_skill_implementations')).includes('integration_test_status')).toBe(through >= 121);
        expect((await columns(db, 'ce_skill_assets')).includes('description')).toBe(through >= 122);
        expect((await columns(db, 'ce_stage_developments')).length > 0).toBe(through >= 122);
        expect((await columns(db, 'ce_skill_audit_events')).length > 0).toBe(through >= 123);
        await seedStages(db, through);
        historicalTables.push(...stageTables.slice(0, 5));
        if (through >= 122) historicalTables.push('ce_stage_developments');
        if (through >= 123) historicalTables.push('ce_skill_audit_events');
      } else {
        for (const table of stageTables) expect(await columns(db, table)).toEqual([]);
      }
      const before = await rows(db, historicalTables);
      const ledger = await db.query('SELECT * FROM schema_version WHERE version <= ? ORDER BY id', [through]);

      await runMigrations(db, 'sqlite');

      await assertMergedSchema(db);
      expect(await db.query('SELECT * FROM schema_version WHERE version <= ? ORDER BY id', [through])).toEqual(ledger);
      for (const [table, seeds] of Object.entries(before)) {
        // Existing columns and values must survive; later migrations may add columns.
        expect(await db.query(`SELECT * FROM ${table} ORDER BY rowid`), table)
          .toEqual(seeds.map(seed => expect.objectContaining(seed)));
      }
      expect(await db.query("SELECT owner_id FROM workflow_specs WHERE workflow_id = 'merge-workflow'"))
        .toEqual([{ owner_id: isFeature ? null : 'existing-owner' }]);
      if (isFeature) {
        expect(await db.query('SELECT integration_test_status FROM ce_stage_skill_implementations'))
          .toEqual([{ integration_test_status: through >= 121 ? 'passed' : 'untested' }]);
        expect(await db.query('SELECT description FROM ce_skill_assets'))
          .toEqual([{ description: through >= 122 ? 'Historical description' : null }]);
      }
      if (!isFeature || through < 123) {
        // The audit migration must never synthesize events from old assets/versions.
        expect(await db.query('SELECT * FROM ce_skill_audit_events')).toEqual([]);
      }
      if (!isFeature) await seedStages(db, 123);
      await db.exec("UPDATE workflow_specs SET owner_id = 'owner-after-merge' WHERE workflow_id = 'merge-workflow'");
      expect(await db.query("SELECT owner_id FROM workflow_specs WHERE workflow_id = 'merge-workflow'"))
        .toEqual([{ owner_id: 'owner-after-merge' }]);
      await assertIdempotent(db);
    } finally {
      await db.close();
    }
  });
});
