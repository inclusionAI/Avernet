import { beforeEach, describe, expect, it, vi } from 'vitest';
import { runMigrations, type IDatabase } from '../db.js';
import { dialectFor } from '../db/dialect.js';
import { migrations } from '../schema.js';
import { workflowSnapshotMigrations } from '../schema-workflow-snapshot.js';
import { packOperationMigrations, renderPackOperationDdl } from '../schema-pack-operations.js';

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

const packColumns = [
  ['request_id', 'varchar', 190, 'NO'], ['workflow_id', 'varchar', 256, 'NO'],
  ['actor_id', 'varchar', 256, 'NO'], ['kind', 'varchar', 16, 'NO'],
  ['input_hash', 'varchar', 128, 'NO'], ['phase', 'varchar', 16, 'NO'],
  ['attempt', 'bigint', null, 'NO'],
  ['lock_workflow_id', 'bigint', null, 'YES'], ['baseline_json', 'mediumtext', 16777215, 'NO'],
  ['payload_json', 'longtext', 4294967295, 'YES'], ['result_json', 'longtext', 4294967295, 'YES'],
  ['error_json', 'longtext', 4294967295, 'YES'], ['created_at_ms', 'bigint', null, 'NO'],
  ['updated_at_ms', 'bigint', null, 'NO'],
].map(([column_name, data_type, max_length, is_nullable]) => ({ column_name, data_type, max_length, is_nullable }));
const packIndexes = [
  { index_name: 'PRIMARY', column_name: 'request_id', prefix_length: null },
  { index_name: 'uk_clawweb_pack_operation_lock', column_name: 'lock_workflow_id', prefix_length: null },
];
const packRead = 'FROM clawweb_pack_operations WHERE 1 = 0';

function createDb(dbType: 'mysql' | 'zdas', options: {
  canDdl: boolean;
  current?: number;
  failure?: { sqlPrefix: string; error: Error };
  queryFailure?: { sqlPart: string; error: Error };
  invalidColumns?: boolean;
  invalidIndexes?: boolean;
  invalidEngine?: boolean;
}) {
  const exec = vi.fn<IDatabase['exec']>(async sql => {
    if (!options.canDdl && /^CREATE TABLE/i.test(sql)) {
      throw new Error('CREATE command denied');
    }
    if (options.failure && sql.startsWith(options.failure.sqlPrefix)) {
      throw options.failure.error;
    }
    const allowed = [
      ...migrations.flatMap(m => m.sql.map(ddl => dialectFor(dbType).renderDdl(ddl))),
      ...workflowSnapshotMigrations.flatMap(m => m.sql.map(ddl => dialectFor(dbType).renderDdl(ddl))),
      ...packOperationMigrations.flatMap(m => m.sql.map(ddl => renderPackOperationDdl({ dbType, dialect: dialectFor(dbType) }, ddl))),
      probes.ordinaryCallback, probes.healingCallback, tagAlter,
    ];
    if (!/^CREATE TABLE IF NOT EXISTS schema_version\b/.test(sql)
      && sql !== ledgerInsert && !allowed.some(value => sql === value || (value === tagAlter && sql.startsWith(value)))) {
      throw new Error(`Unexpected exec: ${sql}`);
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
    if (options.queryFailure && sql.includes(options.queryFailure.sqlPart)) throw options.queryFailure.error;
    if (sql.includes(packRead)) return [] as T[];
    if (sql.includes("TABLE_NAME = 'clawweb_pack_operations'")) {
      if (sql.includes('information_schema.COLUMNS')) return (options.invalidColumns ? packColumns.slice(1) : packColumns) as T[];
      if (sql.includes('information_schema.TABLES')) return [{ engine: options.invalidEngine ? 'MyISAM' : 'InnoDB' }] as T[];
      if (sql.includes('information_schema.STATISTICS')) return (options.invalidIndexes ? packIndexes.slice(0, 1) : packIndexes) as T[];
    }
    if (sql.includes("TABLE_NAME = 'workflow_evolution_analysis_runs'") && sql.includes('information_schema.TABLES')) return [] as T[];
    if (sql.includes("TABLE_NAME = 'workflow_deploy_history'") && sql.includes("COLUMN_NAME = 'tag_name'") && sql.includes('information_schema.COLUMNS')) return [{ IS_NULLABLE: 'NO' }] as T[];
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
    // The existing repair read plus only the read-only v149 validation probes.
    expect(query.mock.calls.map(([sql]) => sql)).toEqual([
      expect.stringContaining("COLUMN_NAME = 'tag_name'"),
      expect.stringContaining(packRead),
      expect.stringMatching(/information_schema\.COLUMNS[\s\S]*TABLE_NAME = 'clawweb_pack_operations'/),
      ...(dbType === 'mysql' ? [expect.stringMatching(/information_schema\.TABLES[\s\S]*TABLE_NAME = 'clawweb_pack_operations'/)] : []),
      expect.stringMatching(/information_schema\.STATISTICS[\s\S]*TABLE_NAME = 'clawweb_pack_operations'/),
    ]);
    expect(query.mock.calls.some(([sql]) => sql.includes('schema_version'))).toBe(false);
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

  it.each([0, 1, 2, 145, 146, 148, 149, 200])('preserves version gating at %i when DDL is available', async current => {
    const { db, exec, query } = createDb(dbType, { canDdl: true, current });

    await runMigrations(db, dbType);

    expect(query).toHaveBeenCalledWith('SELECT MAX(version) as v FROM schema_version');
    expect(exec.mock.calls.filter(([sql]) => sql === ledgerInsert).map(([, params]) => params)).toEqual(
      [...migrations, ...workflowSnapshotMigrations, ...packOperationMigrations].filter(m => m.selfHealing || m.version > current).map(m => [m.version, m.description]),
    );
    expect(query).toHaveBeenCalledWith(expect.stringContaining(packRead));
    expect(query).toHaveBeenCalledWith(expect.stringMatching(/information_schema\.COLUMNS[\s\S]*TABLE_NAME = 'clawweb_pack_operations'/));
    expect(query).toHaveBeenCalledWith(expect.stringMatching(/information_schema\.STATISTICS[\s\S]*TABLE_NAME = 'clawweb_pack_operations'/));
    if (dbType === 'mysql') expect(query).toHaveBeenCalledWith(expect.stringMatching(/information_schema\.TABLES[\s\S]*TABLE_NAME = 'clawweb_pack_operations'/));
    const packDdl = renderPackOperationDdl(db, packOperationMigrations[0].sql[0]);
    expect(exec.mock.calls.filter(([sql]) => sql === packDdl)).toHaveLength(current < 149 ? 1 : 0);
    const firstDdl = db.dialect.renderDdl(migrations[0].sql[0]);
    expect(exec.mock.calls.some(([sql]) => sql === firstDdl)).toBe(current < 1);
    expect(ordinary.migrate).toHaveBeenCalledTimes(current < 2 ? 1 : 0);
    expect(exec.mock.calls.some(([sql]) => sql === probes.ordinarySql)).toBe(current < 2);
    expect(exec).toHaveBeenCalledWith(expect.stringContaining(tagAlter));
    expect(healing.migrate).toHaveBeenCalledExactlyOnceWith(db);
    expect(exec).toHaveBeenCalledWith(probes.healingSql);
  });

  it('propagates a denied pack CREATE without recording v149', async () => {
    const error = new Error('CREATE command denied for pack table');
    const { db, exec, query } = createDb(dbType, {
      canDdl: true, current: 148, failure: { sqlPrefix: 'CREATE TABLE IF NOT EXISTS clawweb_pack_operations', error },
    });
    await expect(runMigrations(db, dbType)).rejects.toBe(error);
    expect(exec).not.toHaveBeenCalledWith(ledgerInsert, [149, packOperationMigrations[0].description]);
    expect(query).not.toHaveBeenCalledWith(expect.stringContaining(packRead));
  });

  it.each(['read', 'columns'] as const)('rejects pack %s errors before recording v149', async failure => {
    const error = new Error('pack read failed');
    const { db, exec, query } = createDb(dbType, {
      canDdl: true, current: 148,
      ...(failure === 'read' ? { queryFailure: { sqlPart: packRead, error } } : { invalidColumns: true }),
    });
    const result = runMigrations(db, dbType);
    await expect(result).rejects.toThrow('require migration 149');
    if (failure === 'read') await expect(result).rejects.toMatchObject({ cause: error });
    else await expect(result).rejects.toMatchObject({ cause: expect.objectContaining({ message: 'Incompatible pack operation column: request_id' }) });
    expect(query).toHaveBeenCalledWith(expect.stringContaining(packRead));
    expect(exec).not.toHaveBeenCalledWith(ledgerInsert, [149, packOperationMigrations[0].description]);
  });

  it('rejects a missing full-column unique lock even when v149 is recorded', async () => {
    const { db, exec } = createDb(dbType, { canDdl: true, current: 149, invalidIndexes: true });
    await expect(runMigrations(db, dbType)).rejects.toMatchObject({
      cause: expect.objectContaining({ message: 'Missing full-column unique constraint on lock_workflow_id' }),
    });
    expect(exec.mock.calls.some(([sql]) => sql.includes('clawweb_pack_operations'))).toBe(false);
    expect(exec).not.toHaveBeenCalledWith(ledgerInsert, [149, packOperationMigrations[0].description]);
  });

  it('rejects invalid managed pack schema without ledger access or pack DDL', async () => {
    const { db, exec, query } = createDb(dbType, { canDdl: false, invalidColumns: true });
    await expect(runMigrations(db, dbType)).rejects.toThrow('require migration 149');
    expect(healing.migrate).toHaveBeenCalledExactlyOnceWith(db);
    expect(ordinary.migrate).not.toHaveBeenCalled();
    expect(exec.mock.calls.filter(([sql]) => sql.includes('schema_version'))).toHaveLength(1);
    expect(exec.mock.calls.some(([sql]) => sql.includes('clawweb_pack_operations'))).toBe(false);
    expect(query.mock.calls.some(([sql]) => sql.includes('schema_version'))).toBe(false);
  });

  it.each([
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

    expect(exec).toHaveBeenCalledWith(sqlPrefix);
    expect(exec).not.toHaveBeenCalledWith(ledgerInsert, [version, expect.any(String)]);
  });
});

it.each(['mysql', 'zdas'] as const)('does not add automation history columns or indexes on %s', async dbType => {
  const { db, exec } = createDb(dbType, { canDdl: true, current: 145 });
  await runMigrations(db, dbType);
  expect(exec.mock.calls.some(([sql]) => /ALTER TABLE workflow_repair_release_records/i.test(sql))).toBe(false);
});

it('rejects non-transactional MySQL pack storage before recording v149', async () => {
  const { db, exec } = createDb('mysql', { canDdl: true, current: 148, invalidEngine: true });
  await expect(runMigrations(db, 'mysql')).rejects.toMatchObject({
    cause: expect.objectContaining({ message: 'Pack operations require transactional InnoDB storage' }),
  });
  expect(exec).not.toHaveBeenCalledWith(ledgerInsert, [149, packOperationMigrations[0].description]);
});
