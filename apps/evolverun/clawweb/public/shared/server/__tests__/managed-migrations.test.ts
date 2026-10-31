import { beforeEach, describe, expect, it, vi } from 'vitest';
import { runMigrations, type IDatabase } from '../db.js';
import { dialectFor } from '../db/dialect.js';
import { migrations } from '../schema.js';
import { workflowSnapshotMigrations } from '../schema-workflow-snapshot.js';

type Migration = (typeof migrations)[number];

const probes = vi.hoisted(() => ({
  ordinarySql: 'UPDATE migration_probe SET ordinary_sql = 1',
  ordinaryCallback: 'UPDATE migration_probe SET ordinary_callback = 1',
  healingSql: 'UPDATE migration_probe SET healing_sql = 1',
  healingCallback: 'UPDATE migration_probe SET healing_callback = 1',
}));

vi.mock('../schema.js', async importOriginal => {
  const actual = await importOriginal<typeof import('../schema.js')>();
  const ordinary: Migration = {
    version: 2,
    description: 'Ordinary SQL and callback probe',
    sql: [probes.ordinarySql],
    migrate: vi.fn(async (db: IDatabase) => { await db.exec(probes.ordinaryCallback); }),
  };
  const healing: Migration = {
    version: 145,
    description: 'Self-healing SQL and callback probe',
    selfHealing: true,
    sql: [probes.healingSql],
    migrate: vi.fn(async (db: IDatabase) => { await db.exec(probes.healingCallback); }),
  };
  return {
    ...actual,
    migrations: [
      actual.migrations.find(m => m.version === 1)!,
      ordinary,
      actual.migrations.find(m => m.version === 144)!,
      healing,
    ],
  };
});

const ordinary = migrations[1];
const healing = migrations[3];
const ledgerInsert = 'INSERT INTO schema_version (version, description) VALUES (?, ?)';
const tagAlter = 'ALTER TABLE `workflow_deploy_history`';

function createDb(dbType: 'mysql' | 'zdas', options: {
  canDdl: boolean;
  current?: number;
  linkMaxLength?: number;
  failure?: { sqlPrefix: string; error: Error };
}) {
  const exec = vi.fn<IDatabase['exec']>(async sql => {
    if (!options.canDdl && /^CREATE TABLE/i.test(sql)) {
      throw new Error('CREATE command denied');
    }
    if (options.failure && sql.startsWith(options.failure.sqlPrefix)) {
      throw options.failure.error;
    }
    return { affectedRows: 1 };
  });
  const query = vi.fn<(sql: string) => void>();
  async function queryRows<T>(sql: string): Promise<T[]> {
    query(sql);
    if (sql === 'SELECT MAX(version) as v FROM schema_version') {
      if (!options.canDdl) throw new Error('Unexpected schema_version read');
      return [{ v: options.current ?? 0 }] as T[];
    }
    if (sql.includes('information_schema.COLUMNS') && sql.includes("'flow_run_links'")) {
      return ['child_flow_id', 'parent_flow_id', 'parent_node_id', 'child_workflow_id'].map(column_name => ({ column_name, max_length: options.linkMaxLength ?? 255 })) as T[];
    }
    if (sql.includes('information_schema.STATISTICS') && sql.includes("'flow_run_links'")) {
      return [{ index_name: 'PRIMARY', column_name: 'child_flow_id', sub_part: null, non_unique: 0, seq: 1 },
        { index_name: 'idx_flow_run_links_parent', column_name: 'parent_flow_id', sub_part: null, non_unique: 1, seq: 1 }] as T[];
    }
    if (sql.includes('information_schema.TABLES')) return [] as T[];
    if (sql.includes('information_schema.COLUMNS')) return [{ IS_NULLABLE: 'NO' }] as T[];
    throw new Error(`Unexpected query: ${sql}`);
  }
  const db: IDatabase = {
    dbType,
    dialect: dialectFor(dbType),
    exec,
    query: queryRows,
    transaction: async fn => fn(db),
    close: async () => {},
  };
  return { db, exec, query };
}

beforeEach(() => { vi.clearAllMocks(); });

describe.each(['mysql', 'zdas'] as const)('runMigrations on %s', dbType => {
  it('skips ordinary SQL/callbacks and ledger access without DDL permission, but runs repairs', async () => {
    const { db, exec, query } = createDb(dbType, { canDdl: false });

    await expect(runMigrations(db, dbType)).resolves.toBeUndefined();

    expect(ordinary.migrate).not.toHaveBeenCalled();
    expect(healing.migrate).toHaveBeenCalledExactlyOnceWith(db);
    // Only the capability probe may touch schema_version; no ledger reads/writes.
    expect(exec.mock.calls.map(([sql]) => sql)).toEqual([
      expect.stringMatching(/^CREATE TABLE IF NOT EXISTS schema_version\b/),
      expect.stringContaining(tagAlter),
      probes.healingCallback,
      probes.healingSql,
    ]);
    expect(query).toHaveBeenCalledTimes(1);
    expect(query).toHaveBeenCalledWith(expect.stringContaining('information_schema.COLUMNS'));
  });

  it('continues after the real v144 ALTER is denied, without recording progress', async () => {
    const denied = new Error('ALTER command denied');
    const { db, exec, query } = createDb(dbType, {
      canDdl: false, failure: { sqlPrefix: tagAlter, error: denied },
    });

    await expect(runMigrations(db, dbType)).resolves.toBeUndefined();

    expect(exec).toHaveBeenCalledWith(expect.stringContaining(tagAlter));
    expect(exec).toHaveBeenLastCalledWith(probes.healingSql);
    expect(healing.migrate).toHaveBeenCalledExactlyOnceWith(db);
    expect(ordinary.migrate).not.toHaveBeenCalled();
    expect(exec.mock.calls.filter(([sql]) => sql.includes('schema_version'))).toHaveLength(1);
    expect(query.mock.calls.some(([sql]) => sql.includes('schema_version'))).toBe(false);
  });

  it.each([0, 1, 2, 145, 146, 147, 148, 200])('preserves version gating at %i when DDL is available', async current => {
    const { db, exec, query } = createDb(dbType, { canDdl: true, current });

    await runMigrations(db, dbType);

    expect(query).toHaveBeenCalledWith('SELECT MAX(version) as v FROM schema_version');
    expect(exec.mock.calls.filter(([sql]) => sql === ledgerInsert).map(([, params]) => params)).toEqual(
      [...migrations, ...workflowSnapshotMigrations].filter(m => m.selfHealing || m.version > current).map(m => [m.version, m.description]),
    );
    const firstDdl = db.dialect.renderDdl(migrations[0].sql[0]);
    expect(exec.mock.calls.some(([sql]) => sql === firstDdl)).toBe(current < 1);
    expect(ordinary.migrate).toHaveBeenCalledTimes(current < 2 ? 1 : 0);
    expect(exec.mock.calls.some(([sql]) => sql === probes.ordinarySql)).toBe(current < 2);
    expect(exec).toHaveBeenCalledWith(expect.stringContaining(tagAlter));
    expect(healing.migrate).toHaveBeenCalledExactlyOnceWith(db);
    expect(exec).toHaveBeenCalledWith(probes.healingSql);
  });

  it('does not record v148 when the post-DDL schema remains too narrow', async () => {
    const { db, exec } = createDb(dbType, { canDdl: true, current: 147, linkMaxLength: 190 });
    await expect(runMigrations(db, dbType)).rejects.toThrow('identifier columns are not fully provisioned');
    expect(exec).toHaveBeenCalledWith(expect.stringContaining('ALTER TABLE flow_run_links'));
    expect(exec).not.toHaveBeenCalledWith(ledgerInsert, [148, expect.any(String)]);
  });

  it.each([
    { canDdl: true, sqlPrefix: 'ALTER TABLE flow_run_links', version: 148 },
    { canDdl: true, sqlPrefix: probes.ordinaryCallback, version: 2 },
    { canDdl: true, sqlPrefix: probes.ordinarySql, version: 2 },
    { canDdl: false, sqlPrefix: probes.healingCallback, version: 145 },
    { canDdl: false, sqlPrefix: probes.healingSql, version: 145 },
  ])('propagates non-tolerated errors from $sqlPrefix', async ({ canDdl, sqlPrefix, version }) => {
    const error = new Error('Migration write failed');
    const { db, exec } = createDb(dbType, {
      canDdl, current: 1, failure: { sqlPrefix, error },
    });

    await expect(runMigrations(db, dbType)).rejects.toBe(error);

    expect(exec).toHaveBeenCalledWith(expect.stringContaining(sqlPrefix));
    expect(exec).not.toHaveBeenCalledWith(ledgerInsert, [version, expect.any(String)]);
  });
});

it.each(['mysql', 'zdas'] as const)('does not add automation history columns or indexes on %s', async dbType => {
  const { db, exec } = createDb(dbType, { canDdl: true, current: 145 });
  await runMigrations(db, dbType);
  expect(exec.mock.calls.some(([sql]) => /ALTER TABLE workflow_repair_release_records/i.test(sql))).toBe(false);
});
