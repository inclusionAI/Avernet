import type { IDatabase } from './db.js';
import type { migrations } from './schema.js';
import { aistudioTrainManualAuditMigrations } from './schema-aistudio-train-manual-audit.js';

// Business columns mirror ClawMind's train-watch-store. Epoch seconds stay BIGINT
// in MySQL/ZDAS; only the audit columns are database-native timestamps.
const columns = `watch_id VARCHAR(64) NOT NULL,
  flow_id VARCHAR(255) NOT NULL,
  workflow_id VARCHAR(255) NOT NULL,
  node_id VARCHAR(255) NOT NULL,
  attempt BIGINT NOT NULL,
  owner_bot_id VARCHAR(255) NOT NULL,
  mode VARCHAR(255) NOT NULL,
  record_id VARCHAR(255) NOT NULL,
  detail_url VARCHAR(1024),
  phase VARCHAR(255) NOT NULL,
  last_status VARCHAR(255),
  submitted_at BIGINT NOT NULL,
  first_poll_at BIGINT NOT NULL,
  next_poll_at BIGINT NOT NULL,
  timeout_at BIGINT NOT NULL,
  poll_interval_seconds BIGINT NOT NULL,
  last_polled_at BIGINT,
  last_poll_source VARCHAR(255),
  error_count BIGINT NOT NULL,
  last_error TEXT,
  lease_owner VARCHAR(255),
  lease_expires_at BIGINT,
  resolution_status VARCHAR(255),
  resolution_started_at BIGINT,
  resolved_at BIGINT,
  canceled BIGINT NOT NULL,
  manual_requested_at BIGINT,
  manual_requested_by VARCHAR(255),
  last_manual_requested_at BIGINT,
  version BIGINT NOT NULL`;
const indexes = [
  'INDEX idx_aistudio_watch_due (owner_bot_id, phase, next_poll_at)',
  'INDEX idx_aistudio_watch_timeout (owner_bot_id, phase, timeout_at)',
  'INDEX idx_aistudio_watch_manual (owner_bot_id, manual_requested_at)',
];
const exactColumns = new Set(['watch_id','flow_id','workflow_id','node_id','owner_bot_id','mode','record_id','phase','last_poll_source','resolution_status']);
/** Also the deployment DDL source for managed accounts that cannot CREATE tables.
 * IDs are ASCII and case-sensitive. Full-column unique indexes must never use
 * prefix indexes: two different node identities must not collide. */
export function aistudioTrainDdl(type: 'sqlite' | 'mysql' | 'zdas'): string[] {
  const mysql = type !== 'sqlite';
  const business = mysql ? columns.replace(/(\w+) VARCHAR\((\d+)\)/g, (match, name) =>
    exactColumns.has(name) ? `${match} CHARACTER SET ascii COLLATE ascii_bin` : match) : columns;
  const table = `CREATE TABLE IF NOT EXISTS aistudio_train_watches (
  id ${mysql ? "BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT '主键ID'" : 'INTEGER PRIMARY KEY AUTOINCREMENT'},
  ${business},
  gmt_create ${mysql ? 'TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP' : 'INTEGER NOT NULL DEFAULT (unixepoch())'},
  gmt_modified ${mysql ? 'TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP' : 'INTEGER NOT NULL DEFAULT (unixepoch())'},
  UNIQUE ${mysql ? 'KEY uk_aistudio_watch_id ' : ''}(watch_id),
  UNIQUE ${mysql ? 'KEY uk_aistudio_watch_attempt ' : ''}(flow_id, node_id, attempt)${mysql ? `,\n  ${indexes.join(',\n  ')}` : ''}
)`;
  return [table, ...(mysql ? [] : indexes.map(index => `CREATE ${index.replace('INDEX ', 'INDEX IF NOT EXISTS ').replace(' (', ' ON aistudio_train_watches (')}`))];
}
export const aistudioTrainMigrations: typeof migrations = [{
  version: 155,
  description: 'Persist AIStudio training watches and manual poll mailbox',
  sql: [],
  async migrate(db: IDatabase) {
    if (db.dbType === 'noop') throw new Error('AIStudio watches require durable SQL persistence');
    // Already dialect-specific: do not apply legacy VARCHAR shortening or prefix indexes.
    for (const ddl of aistudioTrainDdl(db.dbType)) await db.exec(ddl);
  },
}, ...aistudioTrainManualAuditMigrations];
