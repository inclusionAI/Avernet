import { expect, it } from 'vitest';
import { migrations } from '../schema.js';
import { reconcileRunArchiveCreatedAt } from '../migrations/evolve-schema-reconciliation.js';
import { dialectFor } from '../db/dialect.js';
import type { IDatabase } from '../db.js';

class RunArchiveTestDatabase implements IDatabase {
  readonly dbType = 'mysql' as const;
  readonly dialect = dialectFor('mysql');
  readonly execStatements: string[] = [];

  constructor(
    private readonly createdType: string | null,
    private readonly tablePresent = true,
  ) {}

  async query<T>(sql: string): Promise<T[]> {
    if (sql.includes('information_schema.TABLES')) {
      return this.tablePresent ? [{ TABLE_NAME: 'run_archive_records' } as T] : [];
    }
    if (sql.includes('information_schema.COLUMNS')) {
      return this.createdType ? [{ DATA_TYPE: this.createdType } as T] : [];
    }
    return [];
  }

  async exec(sql: string) {
    this.execStatements.push(sql);
    return { affectedRows: 1 };
  }

  async transaction<T>(fn: (db: IDatabase) => Promise<T>) {
    return fn(this);
  }

  async close() {}
}

it('defines an indexable run archive creation time in both v121 reconcile paths', () => {
  for (const version of [121, 131]) {
    const migration = migrations.find(item => item.version === version)!;
    expect(migration.migrate).toBeDefined();
    expect(migration.sql[0]).toContain('created_at TIMESTAMP NOT NULL');
    expect(migration.sql.join('\n')).not.toContain('created_at TEXT NOT NULL');
  }
});

it.each(['mysql', 'zdas'] as const)('renders %s without a text index column', type => {
  const rendered = [121, 131]
    .map(version => migrations.find(item => item.version === version)!.sql)
    .flat()
    .map(sql => dialectFor(type).renderDdl(sql))
    .join('\n');
  expect(rendered).toContain('created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP');
  if (type === 'mysql') {
    expect(rendered).toContain('idx_run_archive_created ON run_archive_records (created_at)');
  } else {
    expect(rendered).toContain('SELECT 0 -- ZDAS: standalone CREATE INDEX skipped (inline only)');
  }
  expect(rendered).not.toContain('created_at TEXT');
});
it('repairs a partially applied run archive creation timestamp column', async () => {
  const db = new RunArchiveTestDatabase('TEXT');
  await reconcileRunArchiveCreatedAt(db);
  expect(db.execStatements).toEqual([
    'ALTER TABLE run_archive_records MODIFY COLUMN created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP',
  ]);
});

it('leaves compatible or missing run archive schemas unchanged', async () => {
  for (const [createdType, tablePresent] of [
    ['TIMESTAMP', true],
    ['DATETIME', true],
    ['TEXT', false],
  ] as const) {
    const db = new RunArchiveTestDatabase(createdType, tablePresent);
    await reconcileRunArchiveCreatedAt(db);
    expect(db.execStatements).toEqual([]);
  }
});

it('fails clearly when the run archive creation timestamp is missing', async () => {
  const db = new RunArchiveTestDatabase(null);
  await expect(reconcileRunArchiveCreatedAt(db)).rejects.toThrow(
    'run_archive_records.created_at is missing before migration',
  );
  expect(db.execStatements).toEqual([]);
});
