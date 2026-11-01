// @vitest-environment node
import { describe, expect, it, vi } from 'vitest';
import Database from 'better-sqlite3';
import { SqliteDatabase, runMigrations, type IDatabase } from '@avernet/clawweb-shared/server/db';
import { packOperationDdl, packOperationMigrations, renderPackOperationDdl } from '@avernet/clawweb-shared/server/schema-pack-operations';
import { mysqlDialect, zdasDialect, dialectFor } from '@avernet/clawweb-shared/server/db/dialect';

vi.mock('@avernet/clawweb-shared/server/schema', async original => ({
  ...await original<typeof import('@avernet/clawweb-shared/server/schema')>(),
  migrations: [], sqliteTriggers: [],
}));
vi.mock('@avernet/clawweb-shared/server/schema-workflow-snapshot', () => ({ workflowSnapshotMigrations: [] }));

describe('ClawWeb operation migration 149', () => {
  it('exports raw SQLite fixture DDL with an actual unique unfinished-workflow constraint', () => {
    const raw = new Database(':memory:');
    try {
      for (const ddl of packOperationDdl) raw.exec(ddl);
      const indexes = raw.prepare('PRAGMA index_list(clawweb_pack_operations)').all() as Array<{ name: string; unique: number }>;
      expect(indexes.filter(i => i.unique)).toHaveLength(2); // request PK and persistent workflow lock
      expect(packOperationMigrations.map(m => m.version)).toEqual([149]);
      expect(raw.prepare('PRAGMA table_info(clawweb_pack_operations)').all()).toEqual(expect.arrayContaining([
        expect.objectContaining({ name: 'attempt', type: 'INTEGER', notnull: 1, dflt_value: '1' }),
      ]));
    } finally { raw.close(); }
  });

  it('is mounted by normal db.ts migration and preserves existing rows on rerun', async () => {
    const db = new SqliteDatabase(new Database(':memory:'));
    try {
      await db.exec(`CREATE TABLE schema_version (version INTEGER, description TEXT);
        INSERT INTO schema_version VALUES (148, 'previous');
        CREATE TABLE workflow_deploy_history (id INTEGER PRIMARY KEY, action TEXT);
        INSERT INTO workflow_deploy_history VALUES (1, 'deploy');`);
      await runMigrations(db, 'sqlite');
      await db.exec(`INSERT INTO clawweb_pack_operations
        (request_id, workflow_id, actor_id, kind, input_hash, phase, lock_workflow_id, baseline_json, payload_json, created_at_ms, updated_at_ms)
        VALUES ('request', 'wf', 'actor', 'save', 'hash', 'unknown', 1, '{}', '{"git":{"commit":"exact"}}', 1, 1)`, []);
      await runMigrations(db, 'sqlite');
      expect(await db.query('SELECT phase, payload_json, attempt FROM clawweb_pack_operations')).toEqual([
        { phase: 'unknown', payload_json: '{"git":{"commit":"exact"}}', attempt: 1 },
      ]);
      expect(await db.query('SELECT * FROM workflow_deploy_history')).toEqual([{ id: 1, action: 'deploy' }]);
      expect(await db.query('SELECT version FROM schema_version ORDER BY version')).toEqual([{ version: 148 }, { version: 149 }]);
    } finally { await db.close(); }
  });

  it.each([mysqlDialect, zdasDialect])('renders real inline DDL on $name, not a no-op or prefix unique index', dialect => {
    const ddl = packOperationMigrations.flatMap(m => m.sql.map(sql => renderPackOperationDdl({ dbType: dialect.name, dialect }, sql))).join('\n');
    expect(ddl).toMatch(/CREATE TABLE IF NOT EXISTS clawweb_pack_operations/);
    expect(ddl).toMatch(/UNIQUE INDEX uk_clawweb_pack_operation_lock \(lock_workflow_id\)/);
    expect(ddl).toContain('payload_json LONGTEXT');
    expect(ddl).toContain('attempt BIGINT NOT NULL DEFAULT 1');
    expect(ddl).toContain('workflow_id VARCHAR(256)');
    expect(ddl).toContain('lock_workflow_id BIGINT');
    expect(ddl.includes('ENGINE=InnoDB')).toBe(dialect.name === 'mysql');
    expect(ddl).not.toMatch(/SELECT 0|CREATE (UNIQUE )?INDEX|lock_workflow_id\(\d+\)/);
    expect(ddl).not.toMatch(/ALTER TABLE (workflow_specs|workflow_deploy_history)/);
  });

  const managedColumns = [
    ['request_id', 'varchar', 190, 'NO'], ['workflow_id', 'varchar', 256, 'NO'], ['actor_id', 'varchar', 256, 'NO'],
    ['kind', 'varchar', 16, 'NO'], ['input_hash', 'varchar', 128, 'NO'], ['phase', 'varchar', 16, 'NO'],
    ['attempt', 'bigint', null, 'NO'],
    ['lock_workflow_id', 'bigint', null, 'YES'], ['baseline_json', 'mediumtext', null, 'NO'],
    ['payload_json', 'longtext', null, 'YES'], ['result_json', 'longtext', null, 'YES'], ['error_json', 'longtext', null, 'YES'],
    ['created_at_ms', 'bigint', null, 'NO'], ['updated_at_ms', 'bigint', null, 'NO'],
  ].map(([column_name, data_type, max_length, is_nullable]) => ({ column_name, data_type, max_length, is_nullable }));
  function managed(dbType: 'mysql' | 'zdas', available: boolean, canDdl = false, missingIndex = '', engine = 'InnoDB', badColumn = {}) {
    const exec = vi.fn<IDatabase['exec']>(async sql => {
      if (!canDdl || sql.includes('clawweb_pack_operations')) throw new Error('CREATE command denied');
      return { affectedRows: 1 };
    });
    const query = vi.fn<IDatabase['query']>(async sql => {
      if (sql.includes('MAX(version)')) return [{ v: 148 }];
      if (sql.includes('information_schema.COLUMNS')) return managedColumns.map(column => ({ ...column, ...(column.column_name === badColumn['column_name'] ? badColumn : {}) }));
      if (sql.includes('information_schema.TABLES')) return [{ engine }];
      if (sql.includes('information_schema.STATISTICS')) return [
        { index_name: 'PRIMARY', column_name: 'request_id', prefix_length: null },
        { index_name: 'uk_clawweb_pack_operation_lock', column_name: 'lock_workflow_id', prefix_length: null },
      ].filter(row => row.column_name !== missingIndex);
      if (sql.includes('clawweb_pack_operations')) {
        if (!available) throw new Error('table does not exist');
        return [];
      }
      throw new Error(`Unexpected query: ${sql}`);
    });
    const db: IDatabase = { dbType, dialect: dialectFor(dbType), exec, query, transaction: fn => fn(db), close: async () => {} };
    return { db, exec, query };
  }

  it.each(['mysql', 'zdas'] as const)('does not claim success when %s runtime lacks DDL and the table', async type => {
    const { db, exec } = managed(type, false);
    await expect(runMigrations(db, type)).rejects.toThrow(/149.*manually/);
    expect(exec.mock.calls.filter(([sql]) => /INSERT INTO schema_version/.test(sql))).toEqual([]);
  });

  it.each(['mysql', 'zdas'] as const)('accepts manually applied %s schema without writing its ledger', async type => {
    const { db, exec, query } = managed(type, true);
    await expect(runMigrations(db, type)).resolves.toBeUndefined();
    expect(query).toHaveBeenCalledWith(expect.stringContaining('FROM clawweb_pack_operations'));
    expect(exec.mock.calls).toHaveLength(1); // only the existing DDL capability probe
  });

  it.each(['request_id', 'lock_workflow_id'])('rejects readable managed tables missing the %s uniqueness constraint', async column => {
    const { db } = managed('zdas', true, false, column);
    await expect(runMigrations(db, 'zdas')).rejects.toThrow(/149/);
  });

  it('does not record v149 for an existing SQLite table missing its workflow lock constraint', async () => {
    const raw = new Database(':memory:');
    const db = new SqliteDatabase(raw);
    try {
      for (const ddl of packOperationDdl) raw.exec(ddl.replace(', UNIQUE (lock_workflow_id)', ''));
      await db.exec(`CREATE TABLE schema_version (version INTEGER, description TEXT);
        INSERT INTO schema_version VALUES (148, 'previous');`);
      await expect(runMigrations(db, 'sqlite')).rejects.toThrow(/149/);
      expect(await db.query('SELECT version FROM schema_version')).toEqual([{ version: 148 }]);
    } finally { await db.close(); }
  });

  it.each(['request_id', 'lock_workflow_id'])('rejects a partial SQLite unique %s index that does not protect unknown operations', async column => {
    const raw = new Database(':memory:');
    const db = new SqliteDatabase(raw);
    try {
      for (const ddl of packOperationDdl) raw.exec(column === 'lock_workflow_id'
        ? ddl.replace(', UNIQUE (lock_workflow_id)', '')
        : ddl.replace('request_id VARCHAR(190) PRIMARY KEY', 'request_id VARCHAR(190) NOT NULL'));
      await db.exec(`CREATE UNIQUE INDEX partial_lock ON clawweb_pack_operations(${column}) WHERE phase = 'prepared';
        CREATE TABLE schema_version (version INTEGER, description TEXT);
        INSERT INTO schema_version VALUES (149, 'manual');`);
      await expect(runMigrations(db, 'sqlite')).rejects.toThrow(/149/);
    } finally { await db.close(); }
  });

  it('rejects a manually provisioned non-transactional MySQL journal', async () => {
    const { db } = managed('mysql', true, false, '', 'MyISAM');
    await expect(runMigrations(db, 'mysql')).rejects.toThrow(/149/);
  });

  it.each([
    { column_name: 'payload_json', data_type: 'varchar', max_length: 255 },
    { column_name: 'workflow_id', max_length: 190 },
    { column_name: 'lock_workflow_id', is_nullable: 'NO' },
    { column_name: 'request_id', is_nullable: 'YES' },
    { column_name: 'created_at_ms', data_type: 'int' },
    { column_name: 'attempt', data_type: 'varchar' },
    { column_name: 'attempt', is_nullable: 'YES' },
  ])('rejects incompatible managed column metadata %j', async column => {
    const { db } = managed('zdas', true, false, '', 'InnoDB', column);
    await expect(runMigrations(db, 'zdas')).rejects.toThrow(/149/);
  });

  it.each(['mysql', 'zdas'] as const)('propagates %s table CREATE denial without marking v149 applied', async type => {
    const { db, exec } = managed(type, false, true);
    await expect(runMigrations(db, type)).rejects.toThrow('CREATE command denied');
    expect(exec.mock.calls.filter(([sql]) => /INSERT INTO schema_version/.test(sql))).toEqual([]);
  });
});
