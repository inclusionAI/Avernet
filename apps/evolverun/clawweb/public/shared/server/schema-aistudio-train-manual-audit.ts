import type { migrations } from './schema.js';
/** Dedicated marker; never adds uniqueness to or rewrites legacy flow_events. */
export function aistudioTrainManualAuditDdl(type: 'sqlite' | 'mysql' | 'zdas'): string[] {
  const mysql = type !== 'sqlite';
  return [`CREATE TABLE IF NOT EXISTS aistudio_train_manual_audits (
  id ${mysql ? "BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT '主键ID'" : 'INTEGER PRIMARY KEY AUTOINCREMENT'},
  event_id VARCHAR(255)${mysql ? ' CHARACTER SET ascii COLLATE ascii_bin' : ''} NOT NULL,
  identity_json TEXT NOT NULL,
  event_row_id BIGINT NULL,
  gmt_create ${mysql ? 'TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP' : 'INTEGER NOT NULL DEFAULT (unixepoch())'},
  gmt_modified ${mysql ? 'TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP' : 'INTEGER NOT NULL DEFAULT (unixepoch())'},
  UNIQUE ${mysql ? 'KEY uk_aistudio_manual_event ' : ''}(event_id)
)`];
}
export const aistudioTrainManualAuditMigrations: typeof migrations = [{
  version: 156, description: 'Atomically deduplicate AIStudio manual poll audit events', sql: [],
  async migrate(db) {
    if (db.dbType === 'noop') throw new Error('AIStudio audits require durable SQL persistence');
    for (const ddl of aistudioTrainManualAuditDdl(db.dbType)) await db.exec(ddl);
  },
}];
