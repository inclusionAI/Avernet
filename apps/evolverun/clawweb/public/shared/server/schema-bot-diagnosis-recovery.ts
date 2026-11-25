import type { Migration } from './schema-types.js';

/** Target Bot environment is part of the identity; this is shared control-plane data. */
export const botDiagnosisRecoveryDdl = `CREATE TABLE IF NOT EXISTS ce_bot_diagnosis_recovery_config (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bot_id VARCHAR(128) CHARACTER SET latin1 COLLATE latin1_bin NOT NULL,
    entity_id VARCHAR(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    env VARCHAR(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    diagnosis_enabled INTEGER NOT NULL DEFAULT 0,
    recovery_enabled INTEGER NOT NULL DEFAULT 0,
    config_json TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    updated_by VARCHAR(255),
    gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
    gmt_modified INTEGER NOT NULL DEFAULT (unixepoch()),
    UNIQUE(bot_id, entity_id, env)
  )`;
export const botDiagnosisRecoveryMigrations: readonly Migration[] = [{
  version: 154,
  description: 'Add per-Bot diagnosis and recovery configuration',
  sql: [],
  migrate: async db => {
    const ddl = db.dbType === 'sqlite'
      ? botDiagnosisRecoveryDdl.replace(/CHARACTER SET (?:latin1|utf8mb4) COLLATE (?:latin1_bin|utf8mb4_bin)/g, 'COLLATE BINARY')
      : botDiagnosisRecoveryDdl;
    await db.exec(db.dialect.renderDdl(ddl));
  },
}];
