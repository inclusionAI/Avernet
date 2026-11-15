import { afterEach, describe, expect, it } from 'vitest';
import Database from 'better-sqlite3';
import { SqliteDatabase } from '../db/sqlite-database.js';
import { workflowAiDraftMigrations } from '../schema-workflow-ai-draft.js';
import { migrations } from '../schema.js';
import { dialectFor } from '../db/dialect.js';
let raw: Database.Database | undefined;
afterEach(() => { raw?.close(); raw = undefined; });
describe('Workflow AI draft durable migration', () => {
  it('registers a unique monotonic migration and creates durable idempotency and polling indexes', async () => {
    expect(migrations.filter(m => m.version === 153)).toEqual(workflowAiDraftMigrations);
    expect(migrations.filter(m => m.version >= 153)).toEqual(workflowAiDraftMigrations);
    expect(new Set(workflowAiDraftMigrations.map(m => m.version)).size).toBe(workflowAiDraftMigrations.length);
    raw = new Database(':memory:'); const db = new SqliteDatabase(raw);
    for (let twice = 0; twice < 2; twice++) for (const migration of workflowAiDraftMigrations) for (const sql of migration.sql) await db.exec(db.dialect.renderDdl(sql));
    const indexes = raw.prepare("PRAGMA index_list('workflow_ai_drafts')").all() as Array<{ name: string; unique: number }>;
    const requestIndex = indexes.find(index => index.unique === 1
      && JSON.stringify(raw!.prepare(`PRAGMA index_info('${index.name}')`).all().map((column: { name: string }) => column.name)) === JSON.stringify(['request_key']));
    expect(requestIndex).toBeDefined();
    expect(indexes).toContainEqual(expect.objectContaining({ name: 'idx_workflow_ai_draft_pending' }));
    const columns = raw.prepare("PRAGMA table_info('workflow_ai_drafts')").all() as Array<{ name: string }>;
    for (const column of ['actor_id', 'workflow_id', 'request_key', 'request_digest', 'envelope_json', 'state_version']) expect(columns.map(c => c.name)).toContain(column);
  });
  it.each(['mysql', 'zdas'] as const)('renders indexed short SHA256 request keys without prefix uniqueness on %s', dbType => {
    const ddl = dialectFor(dbType).renderDdl(workflowAiDraftMigrations[0].sql[0]);
    expect(ddl).toContain('UNIQUE INDEX uk_workflow_ai_draft_request (request_key)');
    expect(ddl).toContain('MEDIUMTEXT'); expect(ddl).toContain('gmt_create'); expect(ddl).toContain('gmt_modified');
    expect(ddl).not.toMatch(/request_key\(\d+\)/);
  });
});
