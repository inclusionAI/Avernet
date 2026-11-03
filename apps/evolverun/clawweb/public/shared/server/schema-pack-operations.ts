import type { IDatabase } from './db.js';
import type { migrations } from './schema.js';
import { sqliteDialect } from './db/dialect.js';

/** ClawWeb-only recovery journal. No consumer-facing workflow tables are changed.
 * Managed databases must apply v149 with a DDL-enabled account before rollout;
 * a denied CREATE must never be recorded as an applied migration. All indexes
 * are inline so ZDAS cannot silently skip a standalone CREATE INDEX.
 */
// VARCHAR(256) preserves the existing 255-character ID contract: the shared
// MySQL renderer shortens literal VARCHAR(255). The unique lock uses the numeric
// workflow row ID, so ZDAS does not need a truncated or oversized string index.
const canonicalDdl: string[] = [
  `CREATE TABLE IF NOT EXISTS clawweb_pack_operations (
  request_id VARCHAR(190) PRIMARY KEY COMMENT 'ClawWeb操作请求ID，建议UUID',
  workflow_id VARCHAR(256) NOT NULL COMMENT '操作所属工作流',
  actor_id VARCHAR(256) NOT NULL COMMENT '发起操作的用户',
  kind VARCHAR(16) NOT NULL COMMENT '操作类型save或deploy',
  input_hash VARCHAR(128) NOT NULL COMMENT '完整请求内容摘要',
  attempt BIGINT NOT NULL DEFAULT 1 COMMENT '操作尝试代数',
  phase VARCHAR(16) NOT NULL COMMENT 'preparing|prepared|pushing|unknown|completed|failed',
  lock_workflow_id BIGINT DEFAULT NULL COMMENT '未完成操作锁定的workflow_specs行ID，不自动过期',
  baseline_json MEDIUMTEXT NOT NULL COMMENT 'workflow_specs和全部active部署核心字段基线',
  payload_json LONGTEXT COMMENT '精确Git对象、恢复bundle及候选YAML的JSON',
  result_json LONGTEXT COMMENT '完成响应JSON',
  error_json LONGTEXT COMMENT '失败或结果未知的恢复信息JSON',
  created_at_ms BIGINT NOT NULL COMMENT '创建时间毫秒',
  updated_at_ms BIGINT NOT NULL COMMENT '最后修改时间毫秒，不作为锁过期时间',
  UNIQUE INDEX uk_clawweb_pack_operation_lock (lock_workflow_id)
) COMMENT='ClawWeb专用Pack操作与恢复日志'`,
];

/** SQLite-ready SQL for isolated fixtures; production uses the canonical migration below. */
export const packOperationDdl: string[] = canonicalDdl.map(ddl => sqliteDialect.renderDdl(ddl));

export const packOperationMigrations: typeof migrations = [{
  version: 149,
  description: 'ClawWeb-only durable pack operation journal and workflow operation lock',
  sql: canonicalDdl,
}];

/** MySQL must not inherit a non-transactional default engine. ZDAS uses its
 * native transactional storage; SQLite receives its normal dialect rendering. */
export function renderPackOperationDdl(db: Pick<IDatabase, 'dbType' | 'dialect'>, ddl: string): string {
  const rendered = db.dialect.renderDdl(ddl);
  return db.dbType === 'mysql' ? `${rendered} ENGINE=InnoDB` : rendered;
}

type UniqueIndexColumn = { index_name: string; column_name: string; prefix_length: number | null };

type ColumnDefinition = { column_name: string; data_type: string; max_length: number | null; is_nullable: string };
async function assertManagedColumns(db: IDatabase): Promise<void> {
  const columns = await db.query<ColumnDefinition>(`SELECT COLUMN_NAME AS column_name, DATA_TYPE AS data_type,
    CHARACTER_MAXIMUM_LENGTH AS max_length, IS_NULLABLE AS is_nullable FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'clawweb_pack_operations'`);
  const byName = new Map(columns.map(column => [column.column_name, column]));
  const required: Array<[string, string[], number, boolean]> = [
    ['request_id', ['varchar'], 190, false], ['workflow_id', ['varchar'], 255, false],
    ['actor_id', ['varchar'], 255, false], ['kind', ['varchar'], 16, false],
    ['input_hash', ['varchar'], 128, false], ['attempt', ['bigint'], 0, false], ['phase', ['varchar'], 16, false],
    ['lock_workflow_id', ['bigint'], 0, true], ['baseline_json', ['mediumtext', 'longtext'], 0, false],
    ['payload_json', ['longtext'], 0, true], ['result_json', ['longtext'], 0, true], ['error_json', ['longtext'], 0, true],
    ['created_at_ms', ['bigint'], 0, false], ['updated_at_ms', ['bigint'], 0, false],
  ];
  for (const [name, types, capacity, nullable] of required) {
    const column = byName.get(name);
    if (!column || !types.includes(column.data_type.toLowerCase())
      || (capacity > 0 && Number(column.max_length) < capacity)
      || column.is_nullable.toUpperCase() !== (nullable ? 'YES' : 'NO')) {
      throw new Error(`Incompatible pack operation column: ${name}`);
    }
  }
}

/** Read-only validation: CREATE IF NOT EXISTS must not bless a partial/manual
 * schema. Both full-column uniqueness constraints are correctness requirements.
 */
export async function assertPackOperationSchema(db: IDatabase): Promise<void> {
  try {
    await db.query(`SELECT request_id, workflow_id, actor_id, kind, input_hash, phase, attempt,
      lock_workflow_id, baseline_json, payload_json, result_json, error_json,
      created_at_ms, updated_at_ms FROM clawweb_pack_operations WHERE 1 = 0`);
    if (db.dbType !== 'sqlite') await assertManagedColumns(db);
    if (db.dbType === 'mysql') {
      const tables = await db.query<{ engine: string }>(`SELECT ENGINE AS engine FROM information_schema.TABLES
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'clawweb_pack_operations'`);
      if (tables[0]?.engine?.toLowerCase() !== 'innodb') throw new Error('Pack operations require transactional InnoDB storage');
    }
    let columns: UniqueIndexColumn[];
    if (db.dbType === 'sqlite') {
      const indexes = await db.query<{ name: string }>(
        `SELECT name FROM pragma_index_list('clawweb_pack_operations') WHERE "unique" = 1 AND partial = 0`);
      columns = [];
      for (const index of indexes) {
        const fields = await db.query<{ name: string }>('SELECT name FROM pragma_index_info(?) ORDER BY seqno', [index.name]);
        columns.push(...fields.map(field => ({ index_name: index.name, column_name: field.name, prefix_length: null })));
      }
    } else {
      columns = await db.query<UniqueIndexColumn>(`SELECT INDEX_NAME AS index_name, COLUMN_NAME AS column_name,
        SUB_PART AS prefix_length FROM information_schema.STATISTICS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'clawweb_pack_operations' AND NON_UNIQUE = 0
        ORDER BY INDEX_NAME, SEQ_IN_INDEX`);
    }
    const indexes = new Map<string, UniqueIndexColumn[]>();
    for (const column of columns) indexes.set(column.index_name, [...(indexes.get(column.index_name) ?? []), column]);
    for (const required of ['request_id', 'lock_workflow_id']) {
      if (![...indexes.values()].some(fields => fields.length === 1 && fields[0].column_name === required && fields[0].prefix_length === null)) {
        throw new Error(`Missing full-column unique constraint on ${required}`);
      }
    }
  } catch (cause) {
    throw new Error('ClawWeb pack operations require migration 149. Apply packOperationMigrations SQL via renderPackOperationDdl manually with a DDL-enabled account; do not use SQLite fixture packOperationDdl on MySQL/ZDAS.', { cause });
  }
}
