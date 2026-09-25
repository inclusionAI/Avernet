// @vitest-environment node
import { describe, expect, it } from 'vitest';
import Database from 'better-sqlite3';
import { migrations } from '@avernet/clawweb-shared/server/schema';
import { repairBatchMigrations } from '@avernet/clawweb-shared/server/schema-repair-batch';
import { sqliteDialect, mysqlDialect, zdasDialect } from '@avernet/clawweb-shared/server/db/dialect';

describe('repair-v2 additive schema', () => {
  it('upgrades the real legacy SQLite outcome DDL without dropping its records', () => {
    const db = new Database(':memory:');
    try {
      const legacy = migrations.flatMap(m => m.sql).find(sql => sql.startsWith('CREATE TABLE IF NOT EXISTS workflow_healing_outcomes ('))!;
      db.exec(sqliteDialect.renderDdl(legacy));
      db.prepare("INSERT INTO workflow_healing_outcomes (outcome_id, lesson_id, action) VALUES (?, ?, ?)").run('old', 'lesson', 'verify');
      for (const sql of migrations.find(m => m.version === 117)!.sql) db.exec(sqliteDialect.renderDdl(sql));
      for (const migration of repairBatchMigrations.filter(m => !m.mysqlOnly)) {
        for (const sql of migration.sql) db.exec(sqliteDialect.renderDdl(sql));
      }
      expect(db.prepare('SELECT outcome_id, lesson_id, repair_item_id FROM workflow_healing_outcomes').get()).toEqual({ outcome_id: 'old', lesson_id: 'lesson', repair_item_id: null });
      db.prepare(`INSERT INTO workflow_healing_outcomes (outcome_id, lesson_id, action, repair_item_id, event_key)
        VALUES ('without-task', NULL, 'repair_no_action', 'item', 'event')`).run();
      expect(db.prepare("SELECT source_task_id, source_step_id FROM workflow_healing_outcomes WHERE outcome_id = 'without-task'").get()).toEqual({ source_task_id: null, source_step_id: null });
      expect(() => db.prepare(`INSERT INTO workflow_healing_outcomes (outcome_id, action, event_key)
        VALUES ('duplicate-event', 'repair_no_action', 'event')`).run()).toThrow();
      expect(db.prepare('PRAGMA index_list(workflow_repair_items)').all().some(row => row.unique === 1)).toBe(true);
    } finally { db.close(); }
  });

  it.each([mysqlDialect, zdasDialect])('renders full uniqueness and audit compatibility for $name (DDL contract only)', dialect => {
    const statements = repairBatchMigrations.filter(m => !m.sqliteOnly).flatMap(m => m.sql.map(sql => dialect.renderDdl(sql)));
    // These are dialect output contracts, not proof of execution on a target database.
    expect(statements.some(sql => /UNIQUE INDEX uk_repair_item_identity \(identity_digest\)/.test(sql))).toBe(true);
    expect(statements.some(sql => /UNIQUE INDEX uk_repair_revision_request \(request_key\)/.test(sql))).toBe(true);
    expect(statements.some(sql => /ADD UNIQUE INDEX uk_healing_outcome_event \(event_key\)/.test(sql))).toBe(true);
    expect(statements.some(sql => /MODIFY COLUMN lesson_id VARCHAR\(64\) NULL/.test(sql))).toBe(true);
    expect(statements.some(sql => /MEDIUMTEXT/.test(sql))).toBe(true);
    expect(statements.some(sql => /^SELECT 0/.test(sql))).toBe(false);
  });

  it.each([mysqlDialect, zdasDialect])('renders documented repair item columns for $name', dialect => {
    const ddl = dialect.renderDdl(repairBatchMigrations[0].sql[0]);
    const columns = [
      'item_id', 'workflow_id', 'group_key', 'proposal_key', 'episode_key', 'identity_digest',
      'content_revision', 'previous_item_id', 'content_json', 'source_refs_json', 'state',
      'state_version', 'active_task_id', 'active_revision', 'disposition_json', 'updated_at_ms',
      'gmt_create', 'gmt_modified',
    ];
    for (const column of columns) {
      expect(ddl, `${column} should have a database comment`).toMatch(
        new RegExp(`\\b${column}\\b[^,\\n]*\\bCOMMENT\\s+'[^']+'`, 'i'),
      );
    }
    expect(ddl).toContain("COMMENT='Workflow修复处理项'");
  });
});
