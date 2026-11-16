import { beforeEach, describe, expect, it, vi } from 'vitest';
import { runMigrations, type IDatabase } from '../db.js';
import { dialectFor } from '../db/dialect.js';
import { migrations } from '../schema.js';
import { workflowSnapshotMigrations } from '../schema-workflow-snapshot.js';
import { flowRunLinkMysqlUpgradeDdl } from '../schema-flow-run-links.js';
import { ordinaryRunRequestDdl, ordinaryRunRequestMigrations } from '../schema-ordinary-run-request.js';

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
    const allowed = [
      ...migrations.flatMap(m => m.sql.map(ddl => dialectFor(dbType).renderDdl(ddl))),
      ...workflowSnapshotMigrations.flatMap(m => m.sql.map(ddl => dialectFor(dbType).renderDdl(ddl))),
      probes.ordinaryCallback, probes.healingCallback, tagAlter, flowRunLinkMysqlUpgradeDdl,
      `${dialectFor(dbType).renderDdl(ordinaryRunRequestDdl)} ENGINE=InnoDB`,
      'ALTER TABLE flow_runs ADD COLUMN ordinary_run_marker VARCHAR(64) DEFAULT NULL',
      'ALTER TABLE ordinary_run_requests ADD COLUMN parent_run_marker VARCHAR(64) DEFAULT NULL',
      'ALTER TABLE ordinary_run_requests ADD COLUMN child_run_marker VARCHAR(64) DEFAULT NULL',
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
    if (sql.includes("TABLE_NAME = 'workflow_evolution_analysis_runs'") && sql.includes('information_schema.TABLES')) return [] as T[];
    if (sql.includes("TABLE_NAME = 'workflow_deploy_history'") && sql.includes("COLUMN_NAME = 'tag_name'") && sql.includes('information_schema.COLUMNS')) return [{ IS_NULLABLE: 'NO' }] as T[];
    if (sql.includes('information_schema.TABLES') && sql.includes("'ordinary_run_requests'")) return [{ engine: 'InnoDB' }] as T[];
    if (sql.includes('information_schema.COLUMNS') && sql.includes("'ordinary_run_requests'")) return [
      ['request_key_hash', 64], ['parent_flow_id', 128], ['child_flow_id', 128],
      ['child_workflow_id', 128], ['request_fingerprint', 64],
    ].map(([column_name, max_length]) => ({ column_name, max_length, data_type: 'varchar', is_nullable: 'NO' })) as T[];
    if (sql.includes('information_schema.STATISTICS') && sql.includes("'ordinary_run_requests'")) return [
      { index_name: 'PRIMARY', column_name: 'request_key_hash', non_unique: 0, sub_part: null, seq: 1 },
      { index_name: 'child', column_name: 'child_flow_id', non_unique: 0, sub_part: null, seq: 1 },
    ] as T[];
    if (sql.includes('information_schema.COLUMNS') && sql.includes('COLUMN_NAME AS name') && sql.includes('TABLE_NAME = ?')) return [] as T[];
    if (sql.includes('information_schema.COLUMNS') && sql.includes("'flow_run_links'")) {
      return ['child_flow_id', 'parent_flow_id', 'parent_node_id', 'child_workflow_id'].map(column_name => ({ column_name, max_length: options.linkMaxLength ?? 255 })) as T[];
    }
    if (sql.includes('information_schema.STATISTICS') && sql.includes("'flow_run_links'")) {
      return [{ index_name: 'PRIMARY', column_name: 'child_flow_id', sub_part: null, non_unique: 0, seq: 1 },
        { index_name: 'idx_flow_run_links_parent', column_name: 'parent_flow_id', sub_part: null, non_unique: 1, seq: 1 }] as T[];
    }
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
    // A missing pack table is never read or inspected at startup.
    expect(query.mock.calls.map(([sql]) => sql)).toEqual([
      expect.stringContaining("COLUMN_NAME = 'tag_name'"),
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

  it.each([0, 1, 2, 145, 146, 147, 148, 149, 150, 200])('preserves version gating at %i when DDL is available', async current => {
    const { db, exec, query } = createDb(dbType, { canDdl: true, current });

    await runMigrations(db, dbType);

    expect(query).toHaveBeenCalledWith('SELECT MAX(version) as v FROM schema_version');
    expect(exec.mock.calls.filter(([sql]) => sql === ledgerInsert).map(([, params]) => params)).toEqual(
      [...migrations, ...workflowSnapshotMigrations, ...ordinaryRunRequestMigrations]
        .filter(m => (m.selfHealing || m.version > current) && (!('supportedDbTypes' in m) || (m.supportedDbTypes as readonly string[]).includes(dbType)))
        .map(m => [m.version, m.description]),
    );
    expect([...exec.mock.calls, ...query.mock.calls].some(([sql]) => sql.includes('clawweb_pack_operations'))).toBe(false);
    expect(exec.mock.calls.some(([sql, params]) => sql === ledgerInsert && params?.[0] === 149)).toBe(false);
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


it('does not record ordinary request migration when MySQL DDL fails', async () => {
  const error = new Error('ordinary request DDL failed');
  const { db, exec } = createDb('mysql', { canDdl: true, current: 149,
    failure: { sqlPrefix: 'CREATE TABLE IF NOT EXISTS ordinary_run_requests', error } });
  await expect(runMigrations(db, 'mysql')).rejects.toBe(error);
  expect(exec).not.toHaveBeenCalledWith(ledgerInsert, [150, expect.any(String)]);
});


it('does not record v151 when MySQL marker DDL fails', async () => {
  const error = new Error('marker DDL failed');
  const { db, exec } = createDb('mysql', { canDdl: true, current: 150,
    failure: { sqlPrefix: 'ALTER TABLE flow_runs ADD COLUMN ordinary_run_marker', error } });
  await expect(runMigrations(db, 'mysql')).rejects.toBe(error);
  expect(exec).not.toHaveBeenCalledWith(ledgerInsert, [151, expect.any(String)]);
});

it('defers v151 on an explicit ALTER permission denial without applying or recording later migrations, and retries after provisioning', async () => {
  const later: Migration = { version: 152, description: 'future migration', sql: ['UPDATE migration_probe SET future = 1'] };
  migrations.push(later);
  try {
    const denied = Object.assign(new Error("ALTER command denied to user for table 'flow_runs'"), { code: 'ER_TABLEACCESS_DENIED_ERROR', errno: 1142 });
    const first = createDb('mysql', { canDdl: true, current: 150, failure: { sqlPrefix: 'ALTER TABLE flow_runs', error: denied } });
    await expect(runMigrations(first.db, 'mysql')).resolves.toBeUndefined();
    expect(first.exec).not.toHaveBeenCalledWith(ledgerInsert, [151, expect.any(String)]);
    expect(first.exec).not.toHaveBeenCalledWith(ledgerInsert, [152, expect.any(String)]);
    expect(first.exec).not.toHaveBeenCalledWith(later.sql[0]);
    const second = createDb('mysql', { canDdl: true, current: 150 });
    const originalQuery = second.db.query;
    second.db.query = async <T>(sql: string, params?: unknown[]): Promise<T[]> => {
      if (sql.includes('COLUMN_NAME AS name')) return (params?.[0] === 'flow_runs'
        ? ['ordinary_run_marker'] : ['parent_run_marker', 'child_run_marker']).map(name =>
          ({ name, data_type: 'varchar', max_length: 64, is_nullable: 'YES', column_default: null })) as T[];
      return originalQuery<T>(sql, params);
    };
    const originalExec = second.db.exec;
    second.db.exec = async (sql, params) => sql === later.sql[0] ? { affectedRows: 1 } : originalExec(sql, params);
    await runMigrations(second.db, 'mysql');
    expect(second.exec.mock.calls.filter(([sql]) => sql === ledgerInsert).map(([, params]) => params?.[0]).filter(version => Number(version) >= 151)).toEqual([151, 152]);
  } finally { migrations.pop(); }
});

it('defers v150 on explicit table CREATE denial, blocks higher versions, and resumes after provisioning', async () => {
  const later: Migration = { version: 152, description: 'future migration', sql: ['UPDATE migration_probe SET future = 1'] };
  migrations.push(later);
  try {
    const denied = Object.assign(new Error("CREATE command denied to user for table 'ordinary_run_requests'"), { code: 'ER_TABLEACCESS_DENIED_ERROR', errno: 1142 });
    const first = createDb('mysql', { canDdl: true, current: 149, failure: { sqlPrefix: 'CREATE TABLE IF NOT EXISTS ordinary_run_requests', error: denied } });
    await expect(runMigrations(first.db, 'mysql')).resolves.toBeUndefined();
    for (const version of [150, 151, 152]) expect(first.exec).not.toHaveBeenCalledWith(ledgerInsert, [version, expect.any(String)]);
    expect(first.exec).not.toHaveBeenCalledWith(later.sql[0]);
    const second = createDb('mysql', { canDdl: true, current: 149 });
    const exec = second.db.exec;
    second.db.exec = async (sql, params) => sql === later.sql[0] ? { affectedRows: 1 } : exec(sql, params);
    await runMigrations(second.db, 'mysql');
    expect(second.exec.mock.calls.filter(([sql]) => sql === ledgerInsert).map(([, params]) => params?.[0])
      .filter(version => Number(version) >= 150)).toEqual([150, 151, 152]);
  } finally { migrations.pop(); }
});

it('defers an incompatible v151 column and never records or executes later migrations', async () => {
  const later: Migration = { version: 152, description: 'future migration', sql: ['UPDATE migration_probe SET future = 1'] };
  migrations.push(later);
  try {
    const { db, exec } = createDb('mysql', { canDdl: true, current: 150 });
    const originalQuery = db.query;
    db.query = async <T>(sql: string, params?: unknown[]): Promise<T[]> => sql.includes('COLUMN_NAME AS name')
      ? [{ name: 'ordinary_run_marker', data_type: 'varchar', max_length: 4, is_nullable: 'YES', column_default: null }] as T[]
      : originalQuery<T>(sql, params);
    const onSqliteDeferred = vi.fn();
    await expect(runMigrations(db, 'mysql', onSqliteDeferred)).resolves.toBeUndefined();
    expect(onSqliteDeferred).not.toHaveBeenCalled();
    expect(exec).not.toHaveBeenCalledWith(ledgerInsert, [151, expect.any(String)]);
    expect(exec).not.toHaveBeenCalledWith(ledgerInsert, [152, expect.any(String)]);
    expect(exec).not.toHaveBeenCalledWith(later.sql[0]);
  } finally { migrations.pop(); }
});

it('propagates a v151 ledger write failure rather than treating it as schema incompatibility', async () => {
  const error = new Error('ledger write failed');
  const { db } = createDb('mysql', { canDdl: true, current: 150 });
  const originalExec = db.exec;
  db.exec = async (sql, params) => {
    if (sql === ledgerInsert && params?.[0] === 151) throw error;
    return originalExec(sql, params);
  };
  await expect(runMigrations(db, 'mysql')).rejects.toBe(error);
});

it('does not record v150 or execute later migrations when existing MySQL storage is incompatible', async () => {
  const later: Migration = { version: 152, description: 'future migration', sql: ['UPDATE migration_probe SET future = 1'] };
  migrations.push(later);
  try {
    const { db, exec } = createDb('mysql', { canDdl: true, current: 149 });
    const original = db.query;
    db.query = async <T>(sql: string, params?: unknown[]): Promise<T[]> => {
      if (sql.includes('information_schema') && sql.includes('ordinary_run_requests')) return [];
      return original<T>(sql, params);
    };
    const onSqliteDeferred = vi.fn();
    await runMigrations(db, 'mysql', onSqliteDeferred);
    expect(onSqliteDeferred).not.toHaveBeenCalled();
    for (const version of [150, 151, 152]) expect(exec).not.toHaveBeenCalledWith(ledgerInsert, [version, expect.any(String)]);
    expect(exec).not.toHaveBeenCalledWith(later.sql[0]);
  } finally { migrations.pop(); }
});

it.each(['TABLES', 'COLUMNS', 'STATISTICS'])('propagates MySQL v150 %s metadata failures, without recording progress', async metadata => {
  const error = Object.assign(new Error('metadata unavailable'), { code: 'PROTOCOL_CONNECTION_LOST' });
  const { db, exec } = createDb('mysql', { canDdl: true, current: 149 });
  const original = db.query;
  db.query = async <T>(sql: string, params?: unknown[]): Promise<T[]> => {
    if (sql.includes(`information_schema.${metadata}`) && sql.includes("'ordinary_run_requests'")) throw error;
    return original<T>(sql, params);
  };
  await expect(runMigrations(db, 'mysql')).rejects.toBe(error);
  expect(exec).not.toHaveBeenCalledWith(ledgerInsert, [150, expect.any(String)]);
  expect(exec).not.toHaveBeenCalledWith(ledgerInsert, [151, expect.any(String)]);
});

it('propagates v150 ledger write failure and does not run v151', async () => {
  const error = new Error('ledger write failed');
  const { db, exec } = createDb('mysql', { canDdl: true, current: 149 });
  const original = db.exec;
  db.exec = async (sql, params) => {
    if (sql === ledgerInsert && params?.[0] === 150) throw error;
    return original(sql, params);
  };
  await expect(runMigrations(db, 'mysql')).rejects.toBe(error);
  expect(exec).not.toHaveBeenCalledWith(expect.stringContaining('ALTER TABLE flow_runs ADD COLUMN ordinary_run_marker'));
});

it.each(['missing', 'nullable', 'narrow', 'engine', 'prefix'])
  ('defers MySQL v150 on %s metadata, stops later migrations, and retries after repair', async defect => {
    const later: Migration = { version: 152, description: 'future migration', sql: ['UPDATE migration_probe SET future = 1'] };
    migrations.push(later);
    try {
      const first = createDb('mysql', { canDdl: true, current: 149 });
      const original = first.db.query;
      first.db.query = async <T>(sql: string, params?: unknown[]): Promise<T[]> => {
        const rows = await original<T>(sql, params);
        if (!sql.includes("'ordinary_run_requests'")) return rows;
        if (sql.includes('information_schema.TABLES') && defect === 'engine') return [{ engine: 'MyISAM' }] as T[];
        if (sql.includes('information_schema.COLUMNS')) {
          const cols = rows as Array<{ column_name: string; max_length: number; is_nullable: string }>;
          if (defect === 'missing') return cols.filter(c => c.column_name !== 'request_fingerprint') as T[];
          if (defect === 'nullable') cols[0].is_nullable = 'YES';
          if (defect === 'narrow') cols[1].max_length = 127;
        }
        if (sql.includes('information_schema.STATISTICS') && defect === 'prefix')
          (rows as Array<{ sub_part: number | null }>)[1].sub_part = 8;
        return rows;
      };
      await runMigrations(first.db, 'mysql');
      for (const version of [150, 151, 152]) expect(first.exec).not.toHaveBeenCalledWith(ledgerInsert, [version, expect.any(String)]);
      expect(first.exec).not.toHaveBeenCalledWith(later.sql[0]);
      const repaired = createDb('mysql', { canDdl: true, current: 149 });
      const execute = repaired.db.exec;
      repaired.db.exec = async (sql, params) => sql === later.sql[0] ? { affectedRows: 1 } : execute(sql, params);
      await runMigrations(repaired.db, 'mysql');
      expect(repaired.exec.mock.calls.filter(([sql]) => sql === ledgerInsert).map(([, params]) => params?.[0])
        .filter(version => Number(version) >= 150)).toEqual([150, 151, 152]);
    } finally { migrations.pop(); }
  });

it('defers v150 for an extra required MySQL column without a default', async () => {
  const { db, exec } = createDb('mysql', { canDdl: true, current: 149 });
  const original = db.query;
  db.query = async <T>(sql: string, params?: unknown[]): Promise<T[]> => {
    const rows = await original<T>(sql, params);
    return sql.includes('information_schema.COLUMNS') && sql.includes("'ordinary_run_requests'")
      ? [...rows, { column_name: 'extra', data_type: 'varchar', max_length: 8, is_nullable: 'NO', column_default: null, extra: '' }] as T[] : rows;
  };
  await runMigrations(db, 'mysql');
  expect(exec).not.toHaveBeenCalledWith(ledgerInsert, [150, expect.any(String)]);
  expect(exec).not.toHaveBeenCalledWith(ledgerInsert, [151, expect.any(String)]);
});

it('does not record v150 when a supplied MySQL association column is generated', async () => {
  const { db, exec } = createDb('mysql', { canDdl: true, current: 149 });
  const original = db.query;
  db.query = async <T>(sql: string, params?: unknown[]): Promise<T[]> => {
    const rows = await original<T>(sql, params);
    if (sql.includes('information_schema.COLUMNS') && sql.includes("'ordinary_run_requests'"))
      Object.assign((rows as Array<{ column_name: string }>).find(c => c.column_name === 'request_fingerprint')!,
        { extra: 'VIRTUAL GENERATED', generation_expression: "'fixed'" });
    return rows;
  };
  await runMigrations(db, 'mysql');
  expect(exec).not.toHaveBeenCalledWith(ledgerInsert, [150, expect.any(String)]);
});
