/** Additive schema; deploy before enabling engine recovery clocks. */
export const recoveryClockMigration = {
  version: 140,
  description: 'Persist owned workflow recovery clocks and retry idempotency keys',
  sql: [
    `CREATE TABLE IF NOT EXISTS flow_recovery_clocks (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      flow_id VARCHAR(255) NOT NULL COMMENT 'flow_id',
      owner_instance_id VARCHAR(255) NOT NULL COMMENT 'owner_instance_id',
      boot_id VARCHAR(255) NOT NULL COMMENT 'boot_id',
      timeout_epoch BIGINT NOT NULL COMMENT 'timeout_epoch',
      clock_seq BIGINT NOT NULL COMMENT 'clock_seq',
      elapsed_ms BIGINT NOT NULL COMMENT 'elapsed_ms',
      phase VARCHAR(32) NOT NULL COMMENT 'phase',
      retry_status VARCHAR(32) COMMENT 'retry_status',
      gmt_create INTEGER NOT NULL DEFAULT (unixepoch()) COMMENT 'gmt_create',
      gmt_modified INTEGER NOT NULL DEFAULT (unixepoch()) COMMENT 'gmt_modified',
      UNIQUE (flow_id)
    )`,
    `CREATE TABLE IF NOT EXISTS flow_recovery_retries (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      flow_id VARCHAR(255) NOT NULL COMMENT 'flow_id',
      request_id VARCHAR(255) NOT NULL COMMENT 'request_id',
      idempotency_key VARCHAR(64) NOT NULL COMMENT 'idempotency_key',
      request_json TEXT NOT NULL COMMENT 'request_json',
      gmt_create INTEGER NOT NULL DEFAULT (unixepoch()) COMMENT 'gmt_create',
      gmt_modified INTEGER NOT NULL DEFAULT (unixepoch()) COMMENT 'gmt_modified',
      UNIQUE (idempotency_key)
    )`,
    `CREATE TRIGGER IF NOT EXISTS trg_flow_recovery_clocks_update AFTER UPDATE ON flow_recovery_clocks
      FOR EACH ROW BEGIN UPDATE flow_recovery_clocks SET gmt_modified = (unixepoch()) WHERE id = NEW.id; END`,
  ],
};
