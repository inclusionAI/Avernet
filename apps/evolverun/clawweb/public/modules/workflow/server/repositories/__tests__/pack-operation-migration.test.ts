// @vitest-environment node
import { describe, expect, it, vi } from 'vitest';
import { runMigrations, type IDatabase } from '@avernet/clawweb-shared/server/db';
import { packOperationDdl, packOperationMigrations, renderPackOperationDdl } from '@avernet/clawweb-shared/server/schema-pack-operations';
import { dialectFor, mysqlDialect, zdasDialect } from '@avernet/clawweb-shared/server/db/dialect';

vi.mock('@avernet/clawweb-shared/server/schema', async original => ({
  ...await original<typeof import('@avernet/clawweb-shared/server/schema')>(),
  migrations: [], sqliteTriggers: [],
}));
vi.mock('@avernet/clawweb-shared/server/schema-workflow-snapshot', () => ({ workflowSnapshotMigrations: [] }));

describe('retired ClawWeb operation migration 149', () => {
  it('reserves 149 without reusing it in active migration lists', async () => {
    const schema = await vi.importActual<typeof import('@avernet/clawweb-shared/server/schema')>('@avernet/clawweb-shared/server/schema');
    const snapshots = await vi.importActual<typeof import('@avernet/clawweb-shared/server/schema-workflow-snapshot')>('@avernet/clawweb-shared/server/schema-workflow-snapshot');
    expect(packOperationMigrations.map(m => m.version)).toEqual([149]);
    expect([...schema.migrations, ...snapshots.workflowSnapshotMigrations].filter(m => m.version === 149)).toEqual([]);
  });

  it('retains historical fixture DDL without applying it', () => {
    expect(packOperationDdl.join('\n')).toMatch(/CREATE TABLE IF NOT EXISTS clawweb_pack_operations/);
    expect(packOperationDdl.join('\n')).toMatch(/UNIQUE\s*\(lock_workflow_id\)/);
  });

  it.each([mysqlDialect, zdasDialect])('retains historical $name rendering without applying it', dialect => {
    const ddl = packOperationMigrations.flatMap(m => m.sql.map(sql => renderPackOperationDdl({ dbType: dialect.name, dialect }, sql))).join('\n');
    expect(ddl).toMatch(/CREATE TABLE IF NOT EXISTS clawweb_pack_operations/);
    expect(ddl).toMatch(/UNIQUE INDEX uk_clawweb_pack_operation_lock \(lock_workflow_id\)/);
    expect(ddl.includes('ENGINE=InnoDB')).toBe(dialect.name === 'mysql');
  });

  it.each(['sqlite', 'mysql', 'zdas'] as const)(
    'starts on %s without a pack table before or after v149, regardless of DDL permission',
    async dbType => {
      for (const canDdl of [false, true]) {
        for (const current of [0, 148, 149, 200]) {
          const exec = vi.fn<IDatabase['exec']>(async sql => {
            if (sql.includes('clawweb_pack_operations')) throw new Error('pack DDL must not run');
            if (!canDdl && sql.includes('schema_version')) throw new Error('CREATE command denied');
            return { affectedRows: 1 };
          });
          const query = vi.fn<IDatabase['query']>(async sql => {
            if (sql.includes('clawweb_pack_operations')) throw new Error('table does not exist');
            if (sql.includes('MAX(version)')) return [{ v: current }];
            if (sql === "SELECT type, name, tbl_name, sql FROM sqlite_master WHERE type IN ('table', 'trigger')") return [];
            const required = [
              ['request_key_hash', 64], ['parent_flow_id', 128], ['child_flow_id', 128],
              ['child_workflow_id', 128], ['request_fingerprint', 64],
            ] as const;
            if (sql === 'PRAGMA table_xinfo(ordinary_run_requests)') return [
              ...required.map(([name]) => ({ name, type: 'TEXT', notnull: 1, pk: Number(name === 'request_key_hash'), dflt_value: null, hidden: 0 })),
              ...['parent_run_marker', 'child_run_marker'].map(name => ({ name, type: 'TEXT', notnull: 0, pk: 0, dflt_value: null, hidden: 0 })),
            ];
            if (sql === 'PRAGMA table_xinfo(flow_runs)') return [
              { name: 'ordinary_run_marker', type: 'TEXT', notnull: 0, pk: 0, dflt_value: null, hidden: 0 },
            ];
            if (sql === 'PRAGMA index_list(ordinary_run_requests)') return [{ name: 'child_unique', unique: 1, partial: 0 }];
            if (sql === 'SELECT name FROM pragma_index_info(?)') return [{ name: 'child_flow_id' }];
            if (sql.includes('information_schema.TABLES') && sql.includes("'ordinary_run_requests'")) return [{ engine: 'InnoDB' }];
            if (sql.includes('information_schema.COLUMNS') && sql.includes("'ordinary_run_requests'")) return required.map(([column_name, max_length]) => ({
              column_name, max_length, data_type: 'varchar', is_nullable: 'NO', column_default: null, extra: '', generation_expression: null,
            }));
            if (sql.includes('information_schema.STATISTICS') && sql.includes("'ordinary_run_requests'")) return [
              { index_name: 'PRIMARY', column_name: 'request_key_hash', non_unique: 0, sub_part: null, seq: 1 },
              { index_name: 'child_unique', column_name: 'child_flow_id', non_unique: 0, sub_part: null, seq: 1 },
            ];
            if (sql.includes('information_schema.COLUMNS') && sql.includes('COLUMN_NAME AS name') && sql.includes('TABLE_NAME = ?')) return [];
            throw new Error(`Unexpected query: ${sql}`);
          });
          const db: IDatabase = { dbType, dialect: dialectFor(dbType), exec, query, transaction: fn => fn(db), close: async () => {} };
          await expect(runMigrations(db, dbType)).resolves.toBeUndefined();
          expect([...exec.mock.calls, ...query.mock.calls].some(([sql]) => sql.includes('clawweb_pack_operations'))).toBe(false);
          expect(exec.mock.calls.some(([sql, params]) => sql.includes('INSERT INTO schema_version') && params?.[0] === 149)).toBe(false);
          const recorded = exec.mock.calls.filter(([sql]) => sql.includes('INSERT INTO schema_version')).map(([, params]) => params?.[0]);
          expect(recorded).toEqual(canDdl && current < 150 && dbType !== 'zdas' ? [150, 151] : []);
        }
      }
    },
  );
});
