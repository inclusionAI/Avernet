import type { Migration } from './schema-types.js';

/** SHA-256 identities keep composite unique indexes below MySQL/OceanBase key limits.
 * Ciphertext is AES-256-GCM; neither plaintext payload nor signing secret belongs here.
 * Millisecond lease/expiry fields are BIGINT, not dialect-rendered timestamp columns.
 */
export const callbackCompensationDdl = [
  `CREATE TABLE IF NOT EXISTS workflow_callback_compensation_previews (
    id INTEGER PRIMARY KEY AUTOINCREMENT COMMENT 'Auto-increment primary key',
    preview_id VARCHAR(64) NOT NULL COMMENT 'Opaque preview identity',
    scope_key VARCHAR(64) NOT NULL COMMENT 'SHA256 environment workflow actor scope',
    expires_ms BIGINT NOT NULL COMMENT 'Preview expiry in Unix milliseconds',
    dto_json LONGTEXT NOT NULL COMMENT 'Only structurally redacted preview DTO',
    frozen_cipher LONGTEXT COMMENT 'AES256GCM protected immutable delivery snapshot',
    gmt_create INTEGER NOT NULL DEFAULT (unixepoch()) COMMENT 'Created time',
    gmt_modified INTEGER NOT NULL DEFAULT (unixepoch()) COMMENT 'Modified time',
    UNIQUE(preview_id)
  )`,
  `CREATE TABLE IF NOT EXISTS workflow_callback_compensation_batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT COMMENT 'Auto-increment primary key',
    batch_id VARCHAR(64) NOT NULL COMMENT 'Opaque batch identity',
    environment_key VARCHAR(64) NOT NULL COMMENT 'SHA256 deployment environment',
    scope_key VARCHAR(64) NOT NULL COMMENT 'SHA256 environment workflow actor scope',
    request_key VARCHAR(64) NOT NULL COMMENT 'SHA256 actor scope plus client request key',
    request_hash VARCHAR(64) NOT NULL COMMENT 'SHA256 immutable selection and reason',
    metadata_json LONGTEXT NOT NULL COMMENT 'Batch ownership and operator reason without payload or secret',
    stopped INTEGER NOT NULL DEFAULT 0 COMMENT 'Administrator stopped queued work',
    gmt_create INTEGER NOT NULL DEFAULT (unixepoch()) COMMENT 'Created time',
    gmt_modified INTEGER NOT NULL DEFAULT (unixepoch()) COMMENT 'Modified time',
    UNIQUE(batch_id),
    UNIQUE(request_key)
  )`,
  `CREATE TABLE IF NOT EXISTS workflow_callback_compensation_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT COMMENT 'Auto-increment primary key',
    item_id VARCHAR(64) NOT NULL COMMENT 'Opaque frozen item identity',
    batch_id VARCHAR(64) NOT NULL COMMENT 'Opaque batch identity',
    environment_key VARCHAR(64) NOT NULL COMMENT 'SHA256 deployment environment',
    delivery_key VARCHAR(64) NOT NULL COMMENT 'SHA256 env workflow flow generation event receiver without config version',
    flow_id TEXT NOT NULL COMMENT 'Authoritative workflow run identity',
    state VARCHAR(16) NOT NULL COMMENT 'Durable single-attempt delivery state',
    frozen_cipher LONGTEXT NOT NULL COMMENT 'AES256GCM protected immutable delivery snapshot',
    reason VARCHAR(128) COMMENT 'Non-sensitive system reason code',
    http_status INTEGER COMMENT 'HTTP response status only',
    sending_until_ms BIGINT COMMENT 'Sending uncertainty deadline in Unix milliseconds',
    audit_pending INTEGER NOT NULL DEFAULT 0 COMMENT 'Persisted outcome needs original audit repair only',
    audit_id BIGINT COMMENT 'Associated original HTTP audit row',
    gmt_create INTEGER NOT NULL DEFAULT (unixepoch()) COMMENT 'Created time',
    gmt_modified INTEGER NOT NULL DEFAULT (unixepoch()) COMMENT 'Modified time',
    UNIQUE(item_id),
    UNIQUE(delivery_key),
    INDEX idx_cb_comp_batch(batch_id),
    INDEX idx_cb_comp_work(environment_key, state)
  )`,
];
export const callbackCompensationMigrations: readonly Migration[] = [{
  version: 159,
  description: 'Add protected, fail-closed workflow success callback compensation',
  sql: callbackCompensationDdl,
}];
