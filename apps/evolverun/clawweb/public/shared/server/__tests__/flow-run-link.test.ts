import { describe, expect, it, vi } from 'vitest';
import Database from 'better-sqlite3';
import { SqliteDatabase, runMigrations, type IDatabase } from '../db.js';
import { dialectFor } from '../db/dialect.js';
import { workflowSnapshotMigrations } from '../schema-workflow-snapshot.js';
import { flowRunLinkMysqlUpgradeDdl } from '../schema-flow-run-links.js';
import { FlowRunLinkRepository } from '../repositories/flow-run-link-repository.js';

const migration = workflowSnapshotMigrations.find(item => item.version === 147)!;
const upgrade = workflowSnapshotMigrations.find(item => item.version === 148)!;
const link = { childFlowId: 'child', parentFlowId: 'parent', parentNodeId: 'node', parentAttempt: null, childWorkflowId: 'wf' };
const mysqlSchemaQuery = async (sql: string) => sql.includes('COLUMNS')
  ? ['child_flow_id', 'parent_flow_id', 'parent_node_id', 'child_workflow_id'].map(column_name => ({ column_name, max_length: 255 }))
  : [{ index_name: 'PRIMARY', column_name: 'child_flow_id', sub_part: null, non_unique: 0, seq: 1 },
    { index_name: 'idx_flow_run_links_parent', column_name: 'parent_flow_id', sub_part: null, non_unique: 1, seq: 1 }];


describe('independent flow run links', () => {
  it('creates and upgrades SQLite through the migration runner and is idempotent', async () => {
    const raw = new Database(':memory:');
    const db = new SqliteDatabase(raw);
    // Existing installation at the reserved v146; no unrelated tables are needed.
    raw.exec('CREATE TABLE schema_version (version INTEGER, description TEXT); INSERT INTO schema_version VALUES (146, \'existing\')');
    await runMigrations(db, 'sqlite');
    await runMigrations(db, 'sqlite');
    const repo = new FlowRunLinkRepository(db);
    await repo.insert(link);
    expect(await repo.findByChild('child')).toEqual(link);
    expect(await repo.listByParent('parent', 20)).toEqual([link]);
    await expect(repo.insert({ ...link, parentFlowId: 'other' })).rejects.toThrow();
    expect(await repo.findByChild('child')).toEqual(link);
    expect(raw.prepare("SELECT name FROM sqlite_master WHERE type='index' AND name='idx_flow_run_links_parent'").get()).toBeTruthy();
    await db.close();
  });

  it('creates the association schema on a fresh SQLite install', async () => {
    const raw = new Database(':memory:'); const db = new SqliteDatabase(raw);
    await runMigrations(db, 'sqlite');
    await new FlowRunLinkRepository(db).insert(link);
    expect(await new FlowRunLinkRepository(db).findByChild('child')).toEqual(link);
    expect(raw.prepare('SELECT MAX(version) AS version FROM schema_version').get()).toEqual({ version: 148 });
    await db.close();
  });

  it('accepts 255-character IDs with common prefixes and rejects 256 for every field', async () => {
    const raw = new Database(':memory:'); const db = new SqliteDatabase(raw);
    for (const ddl of migration.sql) await db.exec(db.dialect.renderDdl(ddl));
    const repo = new FlowRunLinkRepository(db);
    const long = { childFlowId: 'c'.repeat(254) + '1', parentFlowId: 'p'.repeat(255),
      parentNodeId: 'n'.repeat(255), parentAttempt: 1, childWorkflowId: 'w'.repeat(255) };
    const other = { ...long, childFlowId: 'c'.repeat(254) + '2' };
    await repo.insert(long); await repo.insert(other);
    expect(await repo.findByChild(long.childFlowId)).toEqual(long);
    expect(await repo.listByParent(long.parentFlowId, 20)).toHaveLength(2);
    for (const key of ['childFlowId', 'parentFlowId', 'parentNodeId', 'childWorkflowId'] as const) {
      await expect(repo.insert({ ...long, [key]: 'x'.repeat(256) })).rejects.toThrow('Invalid flow run link');
    }
    await db.close();
  });

  it('upgrades a v147 SQLite table in place without losing rows or enforcing its old VARCHAR declaration', async () => {
    const raw = new Database(':memory:'); const db = new SqliteDatabase(raw);
    raw.exec(`CREATE TABLE schema_version (version INTEGER, description TEXT);
      INSERT INTO schema_version VALUES (147, 'old');
      CREATE TABLE flow_run_links (child_flow_id VARCHAR(190) NOT NULL PRIMARY KEY,
        parent_flow_id VARCHAR(190) NOT NULL, parent_node_id VARCHAR(190) NOT NULL,
        parent_attempt INTEGER, child_workflow_id VARCHAR(190) NOT NULL, gmt_create INTEGER NOT NULL DEFAULT (unixepoch()));
      CREATE INDEX idx_flow_run_links_parent ON flow_run_links(parent_flow_id);
      INSERT INTO flow_run_links (child_flow_id,parent_flow_id,parent_node_id,child_workflow_id) VALUES ('old','parent','node','wf');`);
    await runMigrations(db, 'sqlite'); await runMigrations(db, 'sqlite');
    const repo = new FlowRunLinkRepository(db);
    expect(await repo.findByChild('old')).toEqual({ ...link, childFlowId: 'old' });
    const long = { ...link, childFlowId: 'c'.repeat(255), parentFlowId: 'p'.repeat(255),
      parentNodeId: 'n'.repeat(255), childWorkflowId: 'w'.repeat(255) };
    await repo.insert(long); expect(await repo.findByChild(long.childFlowId)).toEqual(long);
    expect(raw.prepare('SELECT MAX(version) AS version FROM schema_version').get()).toEqual({ version: 148 });
    await db.close();
  });

  it.each(['mysql', 'zdas'] as const)('uses full-width explicit %s upgrade and rejects truncated indexes', async dbType => {
    const exec = vi.fn<IDatabase['exec']>().mockResolvedValue({ affectedRows: 0 });
    const columns = ['child_flow_id', 'parent_flow_id', 'parent_node_id', 'child_workflow_id']
      .map(column_name => ({ column_name, max_length: 255 }));
    const indexes = [{ index_name: 'PRIMARY', column_name: 'child_flow_id', sub_part: null, non_unique: 0, seq: 1 },
      { index_name: 'idx_flow_run_links_parent', column_name: 'parent_flow_id', sub_part: null, non_unique: 1, seq: 1 }];
    const query = vi.fn(async (sql: string) => sql.includes('COLUMNS') ? columns : indexes);
    const db = { dbType, exec, query } as unknown as IDatabase;
    await upgrade.migrate!(db);
    expect(exec).toHaveBeenCalledExactlyOnceWith(flowRunLinkMysqlUpgradeDdl);
    expect(flowRunLinkMysqlUpgradeDdl.match(/VARCHAR\(255\)/g)).toHaveLength(4);
    indexes[0].sub_part = 190 as unknown as null;
    await expect(upgrade.migrate!(db)).rejects.toThrow('indexes are not fully provisioned');
    indexes[0].sub_part = null; columns[1].max_length = 190;
    await expect(upgrade.migrate!(db)).rejects.toThrow('identifier columns are not fully provisioned');
    exec.mockRejectedValueOnce(new Error('index too large'));
    await expect(upgrade.migrate!(db)).rejects.toThrow('index too large');
  });

  it('rolls back both child and link when a transaction fails', async () => {
    const raw = new Database(':memory:'); const db = new SqliteDatabase(raw);
    for (const ddl of migration.sql) await db.exec(db.dialect.renderDdl(ddl));
    raw.exec('CREATE TABLE child_runs (id TEXT PRIMARY KEY)');
    await expect(db.transaction(async tx => {
      await tx.exec('INSERT INTO child_runs VALUES (?)', ['child']);
      await new FlowRunLinkRepository(tx).insert(link);
      throw new Error('abort');
    })).rejects.toThrow('abort');
    expect(raw.prepare('SELECT * FROM child_runs').all()).toEqual([]);
    expect(await new FlowRunLinkRepository(db).findByChild('child')).toBeNull();
    await db.close();
  });

  it('commits a child run and its link together in a successful transaction', async () => {
    const raw = new Database(':memory:'); const db = new SqliteDatabase(raw);
    for (const ddl of migration.sql) await db.exec(db.dialect.renderDdl(ddl));
    raw.exec('CREATE TABLE flow_runs (flow_id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL)');
    await db.transaction(async tx => {
      await tx.exec('INSERT INTO flow_runs (flow_id, workflow_id) VALUES (?, ?)', [link.childFlowId, link.childWorkflowId]);
      await new FlowRunLinkRepository(tx).insert({ ...link, parentAttempt: 2 });
    });
    expect(raw.prepare('SELECT * FROM flow_runs').all()).toEqual([{ flow_id: 'child', workflow_id: 'wf' }]);
    expect(await new FlowRunLinkRepository(db).findByChild('child')).toEqual({ ...link, parentAttempt: 2 });
    await db.close();
  });

  it('rolls back child creation when the link conflicts with an existing child', async () => {
    const raw = new Database(':memory:'); const db = new SqliteDatabase(raw);
    for (const ddl of migration.sql) await db.exec(db.dialect.renderDdl(ddl));
    raw.exec('CREATE TABLE flow_runs (flow_id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL)');
    await new FlowRunLinkRepository(db).insert(link);
    await expect(db.transaction(async tx => {
      await tx.exec('INSERT INTO flow_runs (flow_id, workflow_id) VALUES (?, ?)', [link.childFlowId, link.childWorkflowId]);
      await new FlowRunLinkRepository(tx).insert({ ...link, parentFlowId: 'different-parent' });
    })).rejects.toThrow();
    expect(raw.prepare('SELECT * FROM flow_runs').all()).toEqual([]);
    expect(await new FlowRunLinkRepository(db).findByChild('child')).toEqual(link);
    await db.close();
  });

  it.each(['mysql', 'zdas'] as const)('uses parameterized %s writes and propagates driver errors', async dbType => {
    const exec = vi.fn<IDatabase['exec']>().mockResolvedValue({ affectedRows: 1 });
    const repo = new FlowRunLinkRepository({ dbType, exec, query: mysqlSchemaQuery } as unknown as IDatabase);
    await repo.insert({ ...link, parentAttempt: 2 });
    expect(exec).toHaveBeenCalledExactlyOnceWith(
      expect.stringMatching(/^INSERT INTO flow_run_links .* VALUES \(\?, \?, \?, \?, \?\)$/),
      ['child', 'parent', 'node', 2, 'wf'],
    );
    const error = new Error('driver error'); exec.mockRejectedValueOnce(error);
    await expect(repo.insert(link)).rejects.toBe(error);
  });

  it.each(['mysql', 'zdas'] as const)('renders initial %s DDL with full uniqueness and parent index', dbType => {
    const ddl = dialectFor(dbType).renderDdl(migration.sql[0]);
    expect(ddl).toMatch(/child_flow_id VARCHAR\(190\) NOT NULL PRIMARY KEY/i);
    expect(ddl).toMatch(/parent_attempt BIGINT/i);
    expect(ddl).toMatch(/gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP/i);
    expect(ddl).toMatch(/INDEX idx_flow_run_links_parent \(parent_flow_id\)/i);
    expect(ddl).not.toMatch(/unixepoch|AUTOINCREMENT/i);
  });

  it.each(['mysql', 'zdas'] as const)('refuses %s writes before SQL when external schema is too narrow', async dbType => {
    const exec = vi.fn<IDatabase['exec']>().mockResolvedValue({ affectedRows: 1 });
    const query = async (sql: string) => sql.includes('COLUMNS')
      ? ['child_flow_id', 'parent_flow_id', 'parent_node_id', 'child_workflow_id'].map(column_name => ({ column_name, max_length: 190 }))
      : [];
    const repo = new FlowRunLinkRepository({ dbType, exec, query } as unknown as IDatabase);
    await expect(repo.insert({ ...link, childFlowId: 'x'.repeat(255) })).rejects.toThrow('identifier columns are not fully provisioned');
    expect(exec).not.toHaveBeenCalled();
  });

  it('rejects a reported no-op insert instead of returning success', async () => {
    const db = { dbType: 'mysql', exec: async () => ({ affectedRows: 0 }), query: mysqlSchemaQuery } as unknown as IDatabase;
    await expect(new FlowRunLinkRepository(db).insert(link)).rejects.toThrow('persistence failed');
  });

  it('validates write boundaries and propagates failures', async () => {
    const raw = new Database(':memory:'); const db = new SqliteDatabase(raw);
    for (const ddl of migration.sql) await db.exec(db.dialect.renderDdl(ddl));
    const repo = new FlowRunLinkRepository(db);
    for (const bad of [{ ...link, childFlowId: 'x'.repeat(256) }, { ...link, parentAttempt: 0 }, { ...link, childFlowId: 'parent' }]) {
      await expect(repo.insert(bad)).rejects.toThrow('Invalid flow run link');
    }
    await expect(repo.listByParent('parent', 201)).rejects.toThrow();
    raw.exec('DROP TABLE flow_run_links');
    await expect(repo.insert(link)).rejects.toThrow();
    await db.close();
  });
});
