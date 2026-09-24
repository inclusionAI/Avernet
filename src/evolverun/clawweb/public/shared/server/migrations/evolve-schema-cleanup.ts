import type { IDatabase } from '../db.js';
import { evolveTables } from './evolve-schema.js';

type Row = Record<string, unknown>;
const names = evolveTables.map(sql => /CREATE TABLE IF NOT EXISTS (\w+)/.exec(sql)![1]);
const removed: Record<string, string[]> = {
  ce_stage_developments: ['stage_skill_id'],
  ce_skill_assets: ['current_package_ref', 'current_package_sha256'],
  ce_skill_versions: ['source_version_no'],
  ce_skill_events: ['event_id', 'version_from_no', 'version_to_no'],
  ce_skill_audit_events: ['event_id'],
};

async function hasColumn(db: IDatabase, table: string, column: string): Promise<boolean> {
  if (db.dbType === 'sqlite') {
    const rows = await db.query<{ name: string }>(`PRAGMA table_info(${table})`);
    return rows.some(row => row.name === column);
  }
  const rows = await db.query(`SELECT COLUMN_NAME FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = ? AND COLUMN_NAME = ?`, [table, column]);
  return rows.length > 0;
}

function replaceStageIds(value: unknown, ids: Map<string, string>): unknown {
  if (Array.isArray(value)) return value.map(item => replaceStageIds(item, ids));
  if (!value || typeof value !== 'object') return value;
  return Object.fromEntries(Object.entries(value).map(([key, item]) => [key,
    key === 'stageSkillId' && typeof item === 'string' ? (ids.get(item) ?? item) : replaceStageIds(item, ids)]));
}

function referencedStageIds(value: unknown): string[] {
  if (Array.isArray(value)) return value.flatMap(referencedStageIds);
  if (!value || typeof value !== 'object') return [];
  return Object.entries(value).flatMap(([key, item]) =>
    key === 'stageSkillId' && typeof item === 'string' ? [item] : referencedStageIds(item));
}

/** Managed databases must stop feature writes before running this migration.
 * Build complete copies first; SQLite swaps in a transaction, MySQL in one
 * atomic RENAME TABLE. Old tables remain available until references are updated.
 */
export async function cleanupEvolveSchema(db: IDatabase): Promise<void> {
  const legacy = await hasColumn(db, 'ce_stage_developments', 'stage_skill_id');
  const recovering = await hasColumn(db, 'ce_stage_developments_v134', 'stage_skill_id');
  if (!legacy && !recovering) {
    // The development backup is removed first, only after reference updates.
    // A later cleanup failure must not leave obsolete tables on retry.
    for (const table of names) await db.exec(`DROP TABLE IF EXISTS ${table}_v134`);
    return;
  }
  const source = (table: string) => recovering && !legacy ? `${table}_v134` : table;
  const rows = new Map<string, Row[]>();
  for (const table of names) rows.set(table, await db.query<Row>(`SELECT * FROM ${source(table)}`));
  const sequences = new Map<string, number>();
  for (const table of names) {
    const sequence = db.dbType === 'sqlite'
      ? (await db.query<{ next_id: number }>('SELECT seq + 1 AS next_id FROM sqlite_sequence WHERE name = ?', [source(table)]))[0]
      : (await db.query<{ next_id: number }>(`SELECT AUTO_INCREMENT AS next_id FROM information_schema.TABLES
          WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = ?`, [source(table)]))[0];
    sequences.set(table, Number(sequence?.next_id ?? 1));
  }
  const developments = rows.get('ce_stage_developments')!;
  const implementations = rows.get('ce_stage_skill_implementations')!;
  // A binding may legitimately reference an unavailable/deleted draft. Keep
  // that failure local to the binding, rather than invalidating all config.
  // Read original config rows so a retry allocates exactly the same IDs.
  const bindingIds = rows.get('ce_app_config')!
    .filter(row => row.config_key === 'skill_task_stage_bindings')
    .flatMap(row => referencedStageIds(JSON.parse(String(row.config_json))));
  const keys = [...new Set([...developments, ...implementations].map(row => String(row.stage_skill_id)).concat(bindingIds))].sort();
  const ids = new Map<string, string>();
  const used = new Set(keys.filter(key => /^[1-9][0-9]*$/.test(key) && Number.isSafeInteger(Number(key))).map(Number));
  let nextId = 1;
  for (const key of keys) {
    if (/^[1-9][0-9]*$/.test(key) && Number.isSafeInteger(Number(key))) ids.set(key, key);
    else {
      while (used.has(nextId)) nextId++;
      ids.set(key, String(nextId)); used.add(nextId++);
    }
  }
  const lastId = [...used].reduce((largest, id) => Math.max(largest, id), 0);
  const versions = rows.get('ce_skill_versions')!;
  for (const asset of rows.get('ce_skill_assets')!) {
    const version = versions.find(row => row.asset_id === asset.asset_id && Number(row.version_no) === Number(asset.current_version_no));
    if (!version || version.package_ref !== asset.current_package_ref || version.package_sha256 !== asset.current_package_sha256) {
      throw new Error(`Cannot remove inconsistent current Skill snapshot: ${asset.asset_id}`);
    }
  }
  // Refuse to discard historical values that cannot be reconstructed exactly.
  for (const [table, references] of [
    ['ce_skill_versions', [['source_version_id', 'source_version_no']]],
    ['ce_skill_events', [['version_from_id', 'version_from_no'], ['version_to_id', 'version_to_no']]],
  ] as const) {
    for (const row of rows.get(table)!) for (const [reference, number] of references) {
      if (row[number] == null) continue;
      const version = versions.find(item => item.version_id === row[reference]);
      if (!version || Number(version.version_no) !== Number(row[number])) {
        throw new Error(`Cannot remove inconsistent version snapshot: ${table}.${number}, row ${row.id}`);
      }
    }
  }
  const copy = async (tx: IDatabase) => {
    if (legacy) {
      for (let i = 0; i < names.length; i++) {
        const table = names[i];
        const temporary = `${table}_v135`;
        // Only migration-owned staging tables are replaced on retry.
        await tx.exec(`DROP TABLE IF EXISTS ${temporary}`);
        await tx.exec(tx.dialect.renderDdl(evolveTables[i].replace(table, temporary)));
        for (const original of rows.get(table)!) {
          const row = { ...original };
          if (table === 'ce_stage_developments') row.id = Number(ids.get(String(row.stage_skill_id)));
          if (table === 'ce_stage_skill_implementations') row.stage_skill_id = ids.get(String(row.stage_skill_id));
          for (const column of removed[table] ?? []) delete row[column];
          for (const column of ['gmt_create', 'gmt_modified', 'started_at', 'completed_at']) {
            if (row[column] != null && tx.dbType !== 'sqlite' && typeof row[column] === 'number') {
              row[column] = tx.dialect.epochToDb(row[column] as number);
            }
          }
          const columns = Object.keys(row);
          await tx.exec(`INSERT INTO ${temporary} (${columns.join(', ')}) VALUES (${columns.map(() => '?').join(', ')})`, Object.values(row));
        }
        const count = (await tx.query<{ count: number | string }>(`SELECT COUNT(*) AS count FROM ${temporary}`))[0];
        if (Number(count.count) !== rows.get(table)!.length) throw new Error(`Evolve migration row count mismatch: ${table}`);
        // Preserve allocation history so deleted row IDs are never reused.
        const next = table === 'ce_stage_developments' ? lastId + 1 : sequences.get(table)!;
        if (tx.dbType === 'sqlite') {
          const sequence = await tx.exec('UPDATE sqlite_sequence SET seq = ? WHERE name = ?', [next - 1, temporary]);
          if (!sequence.affectedRows) await tx.exec('INSERT INTO sqlite_sequence(name, seq) VALUES (?, ?)', [temporary, next - 1]);
        } else await tx.exec(`ALTER TABLE ${temporary} AUTO_INCREMENT = ${next}`);
      }
      // Legacy implementations can predate the development table. Reserve their
      // group IDs without inventing development records or workflow choices.
      if (tx.dbType === 'sqlite') {
        for (const table of names) {
          await tx.exec(`ALTER TABLE ${table} RENAME TO ${table}_v134`);
          await tx.exec(`ALTER TABLE ${table}_v135 RENAME TO ${table}`);
        }
      } else {
        await tx.exec(`RENAME TABLE ${names.flatMap(table => [`${table} TO ${table}_v134`, `${table}_v135 TO ${table}`]).join(', ')}`);
      }
    }
    // Rewrite platform-owned references only, never business inputs or output.
    for (const row of await tx.query<Row>('SELECT id, config_json FROM ce_app_config WHERE config_key = ?', ['skill_task_stage_bindings'])) {
      const value = replaceStageIds(JSON.parse(String(row.config_json)), ids);
      await tx.exec('UPDATE ce_app_config SET config_json = ? WHERE id = ?', [JSON.stringify(value), row.id]);
    }
    for (const row of await tx.query<Row>('SELECT id, config_json FROM ce_tasks')) {
      const value = JSON.parse(String(row.config_json)) as Record<string, unknown>;
      if (!value?.stageExtensions) continue;
      value.stageExtensions = replaceStageIds(value.stageExtensions, ids);
      await tx.exec('UPDATE ce_tasks SET config_json = ? WHERE id = ?', [JSON.stringify(value), row.id]);
    }
    for (const table of ['ce_stage_developments', ...names.filter(name => name !== 'ce_stage_developments')]) {
      await tx.exec(`DROP TABLE IF EXISTS ${table}_v134`);
    }
  };
  if (db.dbType === 'sqlite') await db.transaction(copy);
  else await copy(db);
}
