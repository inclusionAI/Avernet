import { afterEach, describe, expect, it } from 'vitest';
import Database from 'better-sqlite3';
import { SqliteDatabase } from '../db/sqlite-database.js';
import { workflowAiDraftMigrations } from '../schema-workflow-ai-draft.js';
import { migrations } from '../schema.js';
let raw: Database.Database | undefined;
afterEach(() => { raw?.close(); raw = undefined; });
describe('Retired Editor AI draft table migration', () => {
  it('reserves the already-shipped version without any create, alter or drop SQL', () => {
    expect(migrations.filter(m => m.version === 153)).toEqual(workflowAiDraftMigrations);
    expect(workflowAiDraftMigrations).toHaveLength(1);
    expect(workflowAiDraftMigrations[0].sql).toEqual([]);
    expect(workflowAiDraftMigrations[0].migrate).toBeUndefined();
  });
  it('does not create a draft table in a fresh database', async () => {
    raw = new Database(':memory:'); const db = new SqliteDatabase(raw);
    for (const migration of workflowAiDraftMigrations) for (const sql of migration.sql) await db.exec(db.dialect.renderDdl(sql));
    expect(raw.prepare("SELECT name FROM sqlite_master WHERE name = 'workflow_ai_drafts'").all()).toEqual([]);
  });
  it('does not drop or modify a table provisioned by an earlier release', async () => {
    raw = new Database(':memory:'); const db = new SqliteDatabase(raw);
    raw.exec("CREATE TABLE workflow_ai_drafts (legacy_record TEXT); INSERT INTO workflow_ai_drafts VALUES ('retained')");
    for (const migration of workflowAiDraftMigrations) for (const sql of migration.sql) await db.exec(db.dialect.renderDdl(sql));
    expect(raw.prepare('SELECT * FROM workflow_ai_drafts').all()).toEqual([{ legacy_record: 'retained' }]);
  });
});
