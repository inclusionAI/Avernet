import { createHash } from 'node:crypto';
import type { IDatabase } from '@avernet/clawweb-shared/server/db';

export const WATCH_STATE_OWNER = 'aistudio-train-watch:v1';
export const watchStateDescription = (w: {ownerBotId:string;phase:string;canceled:boolean}): string =>
  `${WATCH_STATE_OWNER}:${!w.canceled && ['waiting','polling'].includes(w.phase) ? 'active' : 'inactive'}:${w.ownerBotId}`;
export const AUDIT_STATE_OWNER = 'aistudio-train-manual-audit:v1';
export const STATE_COLUMNS = 'config_key,config_yaml,version,enabled,description';
export type AIStudioStateRow = {config_key:string;config_yaml:string;version:number|string;enabled:number|string;description:string|null};

/** Full SHA256, RFC4648 base32 alphabet in lowercase, no padding (52 chars).
 * Keep the JSON-array UTF8 preimage identical across Web and engine. */
export function aistudioStateKey(prefix: 'aisw:'|'aisa:', identity: readonly (string|number)[]): string {
  const alphabet = 'abcdefghijklmnopqrstuvwxyz234567';
  let bits = 0, value = 0, hash = '';
  for (const byte of createHash('sha256').update(JSON.stringify(identity), 'utf8').digest()) {
    value = (value << 8) | byte;
    bits += 8;
    while (bits >= 5) { bits -= 5; hash += alphabet[(value >>> bits) & 31]; }
  }
  if (bits) hash += alphabet[(value << (5 - bits)) & 31];
  return prefix + hash;
}
/** Reject CI aliases too: legacy config_key may have a case-insensitive collation. */
export const isReservedAIStudioKey = (key: string): boolean => /^(aisw|aisa):/i.test(key);
export function assertNormalConfigKey(key: string): void {
  if (isReservedAIStudioKey(key)) throw new Error('Reserved AIStudio state namespace');
}
export const NORMAL_CONFIG_SQL = "LOWER(config_key) NOT LIKE 'aisw:%' AND LOWER(config_key) NOT LIKE 'aisa:%'";
export function exactStateSql(db: IDatabase, left: string, right = '?'): string {
  return db.dbType === 'sqlite' ? `${left} = ${right} COLLATE BINARY` : `BINARY ${left} = BINARY ${right}`;
}
export const stateLockSql = (db: IDatabase): string => db.dbType === 'sqlite' ? '' : ' FOR UPDATE';
export function stateVersion(row: Pick<AIStudioStateRow,'version'>): number {
  const version = typeof row.version === 'string' && /^[1-9][0-9]*$/.test(row.version) ? Number(row.version) : row.version;
  if (!Number.isSafeInteger(version) || (version as number) < 1) throw new Error('Corrupt AIStudio state version');
  return version as number;
}
export function parseAIStudioState(row: AIStudioStateRow, prefix: 'aisw:'|'aisa:', owner: string): unknown {
  if (!new RegExp(`^${prefix}[a-z2-7]{52}$`).test(row.config_key) || row.description !== owner
    || (row.enabled !== 0 && row.enabled !== '0') || typeof row.config_yaml !== 'string') throw new Error('Corrupt AIStudio state envelope');
  stateVersion(row);
  return JSON.parse(row.config_yaml);
}
export function uniqueStateConflict(error: unknown): boolean {
  const e = error as {code?:string;errno?:number};
  return e?.code === 'SQLITE_CONSTRAINT_UNIQUE' || e?.code === 'SQLITE_CONSTRAINT_PRIMARYKEY' || e?.code === 'ER_DUP_ENTRY' || e?.errno === 1062;
}

/** Read-only prerequisite, never schema repair. ZDAS v71 may have skipped its
 * standalone index; column presence alone cannot guarantee exactly-once state. */
export async function assertAIStudioStateSchema(db: IDatabase): Promise<void> {
  await db.query(`SELECT id,${STATE_COLUMNS},updated_by,gmt_create,gmt_modified FROM cm_app_config LIMIT 0`);
  type IndexColumn = {index_name:string;column_name:string|null;prefix_length:number|null};
  let columns: IndexColumn[];
  if (db.dbType === 'sqlite') {
    const indexes = await db.query<{name:string}>(
      `SELECT name FROM pragma_index_list('cm_app_config') WHERE "unique" = 1 AND partial = 0`);
    columns = [];
    for (const index of indexes) {
      const fields = await db.query<{name:string|null}>('SELECT name FROM pragma_index_info(?) ORDER BY seqno',[index.name]);
      columns.push(...fields.map(field => ({index_name:index.name,column_name:field.name,prefix_length:null})));
    }
  } else if (db.dbType === 'mysql' || db.dbType === 'zdas') {
    columns = await db.query<IndexColumn>(`SELECT INDEX_NAME AS index_name, COLUMN_NAME AS column_name,
      SUB_PART AS prefix_length FROM information_schema.STATISTICS
      WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'cm_app_config' AND NON_UNIQUE = 0
      ORDER BY INDEX_NAME, SEQ_IN_INDEX`);
  } else {
    throw new Error('AIStudio state requires durable SQL persistence');
  }
  const indexes = new Map<string,IndexColumn[]>();
  for (const column of columns) indexes.set(column.index_name,[...(indexes.get(column.index_name) ?? []),column]);
  if (![...indexes.values()].some(fields => fields.length === 1 && fields[0].column_name === 'config_key' && fields[0].prefix_length === null)) {
    throw new Error('AIStudio state requires an existing full-column unique config_key constraint');
  }
}
