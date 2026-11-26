import type { IDatabase } from './db.js';
import type { Migration } from './schema-types.js';

export const distributedLockDdl = `CREATE TABLE IF NOT EXISTS clawweb_distributed_locks (
  id INTEGER PRIMARY KEY AUTOINCREMENT COMMENT '自增主键ID',
  lock_id VARCHAR(64) NOT NULL COMMENT '环境场景租户资源四元组的SHA256',
  environment VARCHAR(16) NOT NULL COMMENT '控制面环境',
  scene VARCHAR(64) NOT NULL COMMENT '加锁业务场景',
  tenant_id VARCHAR(128) NOT NULL COMMENT '租户标识，全局场景使用__global__',
  resource_key VARCHAR(256) NOT NULL COMMENT '场景内业务资源标识',
  owner_token VARCHAR(64) DEFAULT NULL COMMENT '本次持有者随机令牌',
  lease_ms BIGINT NOT NULL DEFAULT 0 COMMENT '当前持有者的租期毫秒',
  expires_at_ms BIGINT NOT NULL DEFAULT 0 COMMENT '数据库时钟下的租约截止时间',
  next_acquire_at_ms BIGINT NOT NULL DEFAULT 0 COMMENT '数据库时钟下最早再次获取时间',
  version BIGINT NOT NULL DEFAULT 0 COMMENT '每次成功写入递增的版本',
  created_at_ms BIGINT NOT NULL COMMENT '数据库创建时间毫秒',
  updated_at_ms BIGINT NOT NULL COMMENT '数据库最后修改时间毫秒',
  UNIQUE INDEX uk_clawweb_distributed_lock (lock_id)
) COMMENT='ClawWeb公共分布式租约锁'`;

const strings = { lock_id: 64, environment: 16, scene: 64, tenant_id: 128, resource_key: 256, owner_token: 64 };
const numbers = ['id', 'lease_ms', 'expires_at_ms', 'next_acquire_at_ms', 'version', 'created_at_ms', 'updated_at_ms'];
const incompatible = () => new Error('Incompatible clawweb_distributed_locks schema; provision scripts/sql/distributed-locks-v158.mysql.sql');

/** No repair or DDL here: a partial table or truncated unique index cannot arbitrate locks. */
export async function assertDistributedLockSchema(db: IDatabase): Promise<void> {
  if (db.dbType === 'noop') throw incompatible();
  if (db.dbType === 'sqlite') {
    const columns = await db.query<{ name: string; type: string; notnull: number; pk: number; hidden: number }>(
      'PRAGMA table_xinfo(clawweb_distributed_locks)');
    for (const [name] of Object.entries(strings)) {
      const c = columns.find(column => column.name === name);
      if (!c || c.hidden !== 0 || c.pk !== 0 || /INT/i.test(c.type) || !/CHAR|TEXT/i.test(c.type)
        || c.notnull !== (name === 'owner_token' ? 0 : 1)) throw incompatible();
    }
    for (const name of numbers) {
      const c = columns.find(column => column.name === name);
      if (!c || c.hidden !== 0 || c.type.toUpperCase() !== 'INTEGER'
        || (name === 'id' ? c.pk !== 1 : c.notnull !== 1 || c.pk !== 0)) throw incompatible();
    }
    const tables = await db.query<{ sql: string }>("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'clawweb_distributed_locks'");
    if (columns.filter(c => c.pk > 0).length !== 1 || !/\bid\s+INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT\b/i.test(tables[0]?.sql ?? '')) throw incompatible();
    const indexes = await db.query<{ name: string; unique: number; partial: number }>('PRAGMA index_list(clawweb_distributed_locks)');
    for (const index of indexes.filter(i => i.unique === 1 && i.partial === 0)) {
      const parts = await db.query<{ name: string }>('SELECT name FROM pragma_index_info(?) ORDER BY seqno', [index.name]);
      if (parts.length === 1 && parts[0].name === 'lock_id') return;
    }
    throw incompatible();
  }
  const columns = await db.query<{
    name: string; data_type: string; capacity: number | string | null; is_nullable: string; extra: string; generation_expression: string | null;
  }>('SELECT COLUMN_NAME AS name, DATA_TYPE AS data_type, CHARACTER_MAXIMUM_LENGTH AS capacity, '
    + 'IS_NULLABLE AS is_nullable, EXTRA AS extra, GENERATION_EXPRESSION AS generation_expression '
    + "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'clawweb_distributed_locks'");
  for (const [name, length] of Object.entries(strings)) {
    const c = columns.find(column => column.name === name);
    if (!c || !['char', 'varchar'].includes(c.data_type.toLowerCase()) || !Number.isFinite(Number(c.capacity)) || Number(c.capacity) < length
      || c.is_nullable !== (name === 'owner_token' ? 'YES' : 'NO')
      || /\bGENERATED\b/i.test(c.extra) || c.generation_expression) throw incompatible();
  }
  for (const name of numbers) {
    const c = columns.find(column => column.name === name);
    if (!c || c.data_type.toLowerCase() !== 'bigint' || c.is_nullable !== 'NO'
      || /\bGENERATED\b/i.test(c.extra) || c.generation_expression
      || (name === 'id' && !/\bauto_increment\b/i.test(c.extra))) throw incompatible();
  }
  const indexes = await db.query<{ name: string; column_name: string; sub_part: number | null; seq: number }>(
    'SELECT INDEX_NAME AS name, COLUMN_NAME AS column_name, SUB_PART AS sub_part, SEQ_IN_INDEX AS seq '
    + "FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'clawweb_distributed_locks' AND NON_UNIQUE = 0");
  for (const [name, primary] of [['id', true], ['lock_id', false]] as const) {
    if (!indexes.some(i => i.column_name === name && i.sub_part == null && Number(i.seq) === 1
      && (primary ? i.name === 'PRIMARY' : i.name !== 'PRIMARY')
      && indexes.filter(other => other.name === i.name).length === 1)) throw incompatible();
  }
  if (db.dbType === 'mysql') {
    const tables = await db.query<{ engine: string }>(
      "SELECT ENGINE AS engine FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'clawweb_distributed_locks'");
    if (tables[0]?.engine.toLowerCase() !== 'innodb') throw incompatible();
  }
}

/** Existing migration runner owns provisioning; request paths only validate. */
export const distributedLockMigrations: readonly Migration[] = [{
  version: 158,
  description: 'Add shared ClawWeb distributed lease locks with an auto-increment primary key',
  sql: [],
  async migrate(db) {
    const ddl = db.dialect.renderDdl(distributedLockDdl);
    await db.exec(db.dbType === 'mysql' ? ddl + ' ENGINE=InnoDB' : ddl);
    await assertDistributedLockSchema(db);
  },
}];
