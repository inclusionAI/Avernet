import Database from 'better-sqlite3';
import { describe, expect, it } from 'vitest';
import { mysqlDialect, sqliteDialect, zdasDialect } from '../db/dialect.js';
import { healingBootstrapSql, repairBatchMigrations } from '../schema-repair-batch.js';
import { migrations } from '../schema.js';
import type { IDatabase } from '../db.js';

const repairMigration = repairBatchMigrations.find(m => m.version === 136)!;
const createPrefix = 'CREATE TABLE IF NOT EXISTS workflow_healing_outcomes (';

describe('healing outcomes bootstrap', () => {
  it.each([mysqlDialect, zdasDialect])('preserves supported workflow/node IDs and index budgets on $name', async dialect => {
    const statements: string[] = [];
    await repairMigration.migrate!({ dbType: dialect.name, dialect,
      exec: async (sql: string) => { statements.push(sql); } } as unknown as IDatabase);
    for (const ddl of statements) {
      expect(ddl).toMatch(/workflow_id VARCHAR\(190\)/);
      expect(ddl).not.toMatch(/(?:weak_)?node_id VARCHAR\(64\)/);
      if (ddl.includes('CREATE TABLE IF NOT EXISTS workflow_healing_diagnoses (')) {
        expect(ddl).toContain('flow_id VARCHAR(190) NOT NULL');
        expect(ddl).toContain('run_id VARCHAR(190)');
      }
      for (const line of ddl.split('\n').map(line => line.trim())) {
        const index = line.match(/^(?:UNIQUE(?: INDEX \w+)?|INDEX \w+) \((.*)\)/);
        if (!index) continue;
        const bytes = index[1].split(',').reduce((sum, item) => {
          const column = item.trim().match(/^(\w+)(?:\((\d+)\))?$/)!;
          const definition = ddl.match(new RegExp(`^\\s*${column[1]} (VARCHAR|BINARY|BIGINT|TIMESTAMP)(?:\\((\\d+)\\))?`, 'm'))!;
          const length = Number(column[2] ?? definition[2]);
          return sum + (definition[1] === 'VARCHAR' ? length * 4 : definition[1] === 'BINARY' ? length : definition[1] === 'TIMESTAMP' ? 4 : 8);
        }, 0);
        expect(bytes, line).toBeLessThanOrEqual(dialect.maxIndexKeyLength);
      }
    }
  });

  it('precreates ZDAS healing tables with bounded lookup indexes and full-signature identity keys', async () => {
    const statements: string[] = [];
    const db = { dbType: 'zdas', dialect: zdasDialect, exec: async (sql: string) => { statements.push(sql); } } as unknown as IDatabase;
    await repairMigration.migrate!(db);
    const lesson = statements.find(sql => sql.includes('CREATE TABLE IF NOT EXISTS workflow_healing_lessons'))!;
    expect(lesson).toContain('INDEX idx_workflow_healing_lessons_signature (failure_signature(127), failure_mode)');
    for (const table of ['workflow_healing_suggestions', 'workflow_healing_suggestion_actions']) {
      const ddl = statements.find(sql => sql.includes(`CREATE TABLE IF NOT EXISTS ${table} (`))!;
      expect(ddl).toContain('signature_identity BINARY(32) GENERATED ALWAYS AS');
      expect(ddl).toContain('UNIQUE (signature_identity)');
      expect(ddl).not.toMatch(/UNIQUE \(workflow_id, (?:failure_)?signature\)/);
    }
    expect(statements.find(sql => sql.includes('CREATE TABLE IF NOT EXISTS workflow_healing_suggestions (')))
      .toContain('INDEX idx_wh_suggestions_signature (workflow_id(64), failure_signature(127))');
  });

  it('provisions the skipped healing sources and verification fields used by the repair runtime', () => {
    const db = new Database(':memory:');
    try {
      for (const sql of [...healingBootstrapSql, ...repairMigration.sql]) db.exec(sqliteDialect.renderDdl(sql));
      // Match the runtime version reads: both sources must exist even with no history.
      for (const table of ['workflow_healing_diagnoses', 'workflow_healing_suggestions']) {
        expect(db.prepare(`SELECT COUNT(*) AS n, COALESCE(SUM(id), 0) AS v, MAX(gmt_modified) AS t
          FROM ${table} WHERE workflow_id = ?`).get('wf')).toEqual({ n: 0, v: 0, t: null });
      }
      db.exec(`INSERT INTO workflow_healing_suggestions
        (workflow_id, failure_signature, applied_at, verification_status, verification_checked_at, recurrence_count, last_recurrence_at)
        VALUES ('wf', 'timeout', 123, 'recurrence_detected', 456, 1, 456)`);
      expect(db.prepare('SELECT status, verification_status, recurrence_count FROM workflow_healing_suggestions').get())
        .toEqual({ status: 'pending', verification_status: 'recurrence_detected', recurrence_count: 1 });
      db.exec(`INSERT INTO workflow_healing_lessons
        (lesson_id, failure_signature, failure_mode, fix_kind, fix_spec)
        VALUES ('lesson', 'timeout', 'node_timeout', 'patch', '{}')`);
      expect(db.prepare('SELECT source FROM workflow_healing_lessons').get()).toEqual({ source: 'log_analysis' });
      db.exec(`INSERT INTO workflow_healing_suggestion_actions (signature, workflow_id, action)
        VALUES ('timeout', 'wf', 'adopted')`);
      expect(db.prepare('SELECT action FROM workflow_healing_suggestion_actions').get()).toEqual({ action: 'adopted' });
    } finally { db.close(); }
  });

  it('does not alter existing healing source schemas or records when bootstrapping again', () => {
    const db = new Database(':memory:');
    try {
      for (const version of [110, 112, 115, 116, 117, 118]) {
        for (const sql of migrations.find(m => m.version === version)!.sql) db.exec(sqliteDialect.renderDdl(sql));
      }
      db.exec(`INSERT INTO workflow_healing_diagnoses
        (diagnosis_id, flow_id, workflow_id, failure_signature, failure_mode, error_text)
        VALUES ('diagnosis', 'flow', 'wf', 'timeout', 'node_timeout', 'keep')`);
      db.exec(`INSERT INTO workflow_healing_suggestions
        (workflow_id, failure_signature, status, verification_status, recurrence_count)
        VALUES ('wf', 'timeout', 'applied_unverified', 'recurrence_detected', 2)`);
      const snapshot = () => ({
        schema: db.prepare("SELECT name, sql FROM sqlite_master WHERE tbl_name LIKE 'workflow_healing_%' ORDER BY name").all(),
        diagnoses: db.prepare('SELECT * FROM workflow_healing_diagnoses').all(),
        suggestions: db.prepare('SELECT * FROM workflow_healing_suggestions').all(),
      });
      const before = snapshot();
      const creates = healingBootstrapSql;
      expect(creates).toHaveLength(5);
      for (let attempt = 0; attempt < 2; attempt++) {
        for (const sql of creates) db.exec(sqliteDialect.renderDdl(sql));
      }
      expect(snapshot()).toEqual(before);
    } finally { db.close(); }
  });

  it('creates a missing outcomes table before repair audit columns are added', () => {
    const db = new Database(':memory:');
    try {
      for (const sql of [...healingBootstrapSql, ...repairMigration.sql]) db.exec(sqliteDialect.renderDdl(sql));
      db.prepare(`INSERT INTO workflow_healing_outcomes
        (outcome_id, lesson_id, action, repair_item_id, evidence_json, event_key)
        VALUES ('new', 'lesson', 'verify', 'item', '{}', 'event')`).run();
      expect(db.prepare('SELECT outcome_id, repair_item_id, event_key FROM workflow_healing_outcomes').get())
        .toEqual({ outcome_id: 'new', repair_item_id: 'item', event_key: 'event' });
      expect(db.prepare('PRAGMA index_list(workflow_healing_outcomes)').all())
        .toEqual(expect.arrayContaining([expect.objectContaining({ name: 'idx_workflow_healing_outcomes_lesson' })]));
    } finally { db.close(); }
  });

  it('leaves existing outcome rows, custom columns, and indexes unchanged on repeated bootstrap', () => {
    const db = new Database(':memory:');
    try {
      const legacy = migrations.find(m => m.version === 110)!.sql.find(sql => sql.startsWith(createPrefix))!;
      db.exec(sqliteDialect.renderDdl(legacy));
      for (const sql of migrations.find(m => m.version === 117)!.sql) db.exec(sqliteDialect.renderDdl(sql));
      db.exec("ALTER TABLE workflow_healing_outcomes ADD COLUMN custom_note TEXT DEFAULT 'keep'");
      db.exec(`INSERT INTO workflow_healing_outcomes
        (outcome_id, lesson_id, suggestion_id, action, note, gmt_create, gmt_modified)
        VALUES ('existing', NULL, 'suggestion', 'suggestion_apply', 'keep this', 123, 456)`);
      const rows = db.prepare('SELECT * FROM workflow_healing_outcomes').all();
      const schema = db.prepare("SELECT type, name, sql FROM sqlite_master WHERE tbl_name = 'workflow_healing_outcomes' ORDER BY name").all();
      const bootstrap = healingBootstrapSql.find(sql => sql.startsWith(createPrefix));
      expect(bootstrap).toBeDefined();
      db.exec(sqliteDialect.renderDdl(bootstrap!));
      db.exec(sqliteDialect.renderDdl(bootstrap!));
      expect(db.prepare('SELECT * FROM workflow_healing_outcomes').all()).toEqual(rows);
      expect(db.prepare("SELECT type, name, sql FROM sqlite_master WHERE tbl_name = 'workflow_healing_outcomes' ORDER BY name").all()).toEqual(schema);
    } finally { db.close(); }
  });

  it.each([mysqlDialect, zdasDialect])('renders the missing-table bootstrap and inline lesson index for $name', dialect => {
    const statements = [...healingBootstrapSql, ...repairMigration.sql].map(sql => dialect.renderDdl(sql));
    const bootstrapIndex = statements.findIndex(sql => sql.startsWith(createPrefix));
    const alterIndex = statements.findIndex(sql => sql.startsWith('ALTER TABLE workflow_healing_outcomes'));
    expect(bootstrapIndex).toBeGreaterThanOrEqual(0);
    expect(bootstrapIndex).toBeLessThan(alterIndex);
    const ddl = statements[bootstrapIndex];
    expect(ddl).toContain('BIGINT PRIMARY KEY AUTO_INCREMENT');
    expect(ddl).toContain('gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP');
    expect(ddl).toContain('INDEX idx_workflow_healing_outcomes_lesson (lesson_id, gmt_create)');
    expect(ddl).not.toMatch(/AUTOINCREMENT|unixepoch/);
  });
});
