import type { IDatabase } from '../db.js';

interface SqliteColumnRow {
  name: string;
}

interface InformationSchemaNameRow {
  COLUMN_NAME: string;
}

interface InformationSchemaTableRow {
  TABLE_NAME: string;
}

interface InformationSchemaTypeRow {
  DATA_TYPE: string;
}

interface SqliteIndexRow {
  name: string;
}

interface InformationSchemaIndexRow {
  INDEX_NAME: string;
}

async function tableExists(db: IDatabase, table: string): Promise<boolean> {
  if (db.dbType === 'sqlite') {
    const rows = await db.query<SqliteColumnRow>(
      `SELECT name AS name FROM sqlite_master WHERE type = 'table' AND name = ?`,
      [table],
    );
    return rows.length > 0;
  }

  const rows = await db.query<InformationSchemaTableRow>(
    `SELECT TABLE_NAME FROM information_schema.TABLES
     WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = ?`,
    [table],
  );
  return rows.length > 0;
}

async function hasColumn(db: IDatabase, table: string, column: string): Promise<boolean> {
  if (db.dbType === 'sqlite') {
    const rows = await db.query<SqliteColumnRow>(`PRAGMA table_info(${table})`);
    return rows.some((row) => row.name.toLowerCase() === column.toLowerCase());
  }

  const rows = await db.query<InformationSchemaNameRow>(
    `SELECT COLUMN_NAME FROM information_schema.COLUMNS
     WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = ? AND COLUMN_NAME = ?`,
    [table, column],
  );
  return rows.length > 0;
}

async function hasIndex(db: IDatabase, table: string, indexName: string): Promise<boolean> {
  if (db.dbType === 'sqlite') {
    const rows = await db.query<SqliteIndexRow>(`PRAGMA index_list(${table})`);
    return rows.some((row) => row.name.toLowerCase() === indexName.toLowerCase());
  }

  const rows = await db.query<InformationSchemaIndexRow>(
    `SELECT INDEX_NAME FROM information_schema.STATISTICS
     WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = ? AND INDEX_NAME = ?`,
    [table, indexName],
  );
  return rows.length > 0;
}

async function addColumnIfMissing(
  db: IDatabase,
  table: string,
  column: string,
  ddl: string,
): Promise<boolean> {
  if (await hasColumn(db, table, column)) return false;
  await db.exec(db.dialect.renderDdl(ddl));
  return true;
}

async function ensureIndexOnExistingTable(
  db: IDatabase,
  table: string,
  indexName: string,
  ddl: string,
): Promise<void> {
  if (!await hasIndex(db, table, indexName)) {
    await db.exec(db.dialect.renderDdl(ddl));
  }
}

export async function reconcileRunArchiveCreatedAt(db: IDatabase): Promise<void> {
  if (!await tableExists(db, 'run_archive_records')) return;
  if (db.dbType === 'sqlite') return;

  const rows = await db.query<InformationSchemaTypeRow>(
    `SELECT DATA_TYPE FROM information_schema.COLUMNS
     WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'run_archive_records' AND COLUMN_NAME = 'created_at'`,
  );
  const createdType = rows[0]?.DATA_TYPE.toUpperCase();
  if (!createdType) throw new Error('run_archive_records.created_at is missing before migration');
  if (createdType === 'TIMESTAMP' || createdType === 'DATETIME') return;

  await db.exec(`ALTER TABLE run_archive_records MODIFY COLUMN created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP`);
}

export async function reconcilePreBaselineSkillHistory(db: IDatabase): Promise<void> {
  for (const table of ['ce_skill_audit_events', 'ce_skill_events']) {
    if (!await tableExists(db, table)) continue;
    const hasExternalColumn = await hasColumn(db, table, 'external_skill_id');
    const hasOcbColumn = await hasColumn(db, table, 'ocb_skill_id');
    if (hasExternalColumn && !hasOcbColumn) {
      await db.exec(
        db.dialect.renderDdl(`ALTER TABLE ${table} RENAME COLUMN external_skill_id TO ocb_skill_id`),
      );
    }
  }

  if (await tableExists(db, 'ce_skill_audit_events')) {
    const eventIdAdded = await addColumnIfMissing(
      db,
      'ce_skill_audit_events',
      'event_id',
      'ALTER TABLE ce_skill_audit_events ADD COLUMN event_id VARCHAR(64) NULL',
    );
    if (eventIdAdded) {
      await db.exec('UPDATE ce_skill_audit_events SET event_id = idempotency_key WHERE event_id IS NULL');
      await ensureIndexOnExistingTable(
        db,
        'ce_skill_audit_events',
        'uk_ce_skill_audit_event',
        'CREATE UNIQUE INDEX IF NOT EXISTS uk_ce_skill_audit_event ON ce_skill_audit_events (event_id)',
      );
    }
  }

  if (await tableExists(db, 'ce_skill_events')) {
    const eventIdAdded = await addColumnIfMissing(
      db,
      'ce_skill_events',
      'event_id',
      'ALTER TABLE ce_skill_events ADD COLUMN event_id VARCHAR(64) NULL',
    );
    const versionFromAdded = await addColumnIfMissing(
      db,
      'ce_skill_events',
      'version_from_no',
      'ALTER TABLE ce_skill_events ADD COLUMN version_from_no INTEGER',
    );
    const versionToAdded = await addColumnIfMissing(
      db,
      'ce_skill_events',
      'version_to_no',
      'ALTER TABLE ce_skill_events ADD COLUMN version_to_no INTEGER',
    );

    if (eventIdAdded || versionFromAdded || versionToAdded) {
      await ensureIndexOnExistingTable(
        db,
        'ce_skill_events',
        'uk_ce_skill_event_event',
        'CREATE UNIQUE INDEX IF NOT EXISTS uk_ce_skill_event_event ON ce_skill_events (event_id)',
      );
      await ensureIndexOnExistingTable(
        db,
        'ce_skill_events',
        'uk_ce_skill_event_task_id',
        'CREATE UNIQUE INDEX IF NOT EXISTS uk_ce_skill_event_task_id ON ce_skill_events (task_id)',
      );
    }
  }
}

export async function reconcileProviderNeutralSkillIdentifierColumns(db: IDatabase): Promise<void> {
  for (const table of ['ce_skill_assets', 'ce_skill_audit_events', 'ce_skill_events']) {
    if (!await tableExists(db, table)) {
      throw new Error(`Cannot migrate Skill identifiers in v132: table ${table} is missing`);
    }

    const hasOcbColumn = await hasColumn(db, table, 'ocb_skill_id');
    const hasExternalColumn = await hasColumn(db, table, 'external_skill_id');
    if (hasOcbColumn && hasExternalColumn) {
      throw new Error(`Cannot migrate Skill identifiers in v132: ${table} has both identifier columns`);
    }
    if (!hasOcbColumn && !hasExternalColumn) {
      throw new Error(`Cannot migrate Skill identifiers in v132: ${table} is missing identifier columns`);
    }
    if (hasOcbColumn) {
      await db.exec(
        db.dialect.renderDdl(`ALTER TABLE ${table} RENAME COLUMN ocb_skill_id TO external_skill_id`),
      );
    }
  }
}
