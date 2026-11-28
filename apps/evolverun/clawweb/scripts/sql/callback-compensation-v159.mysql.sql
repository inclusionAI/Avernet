-- ClawWeb migration v159: success callback compensation.
-- Provision with the managed schema account before enabling execution.
-- Runtime is fail-closed; this DDL does not approve any receiver or authorize delivery.
-- SHA-256 single-column identities keep unique indexes within MySQL/OceanBase limits.
-- Ciphertext requires an injected 32-byte AES-256-GCM protection key; never insert plaintext payloads.

CREATE TABLE IF NOT EXISTS workflow_callback_compensation_previews (
    id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT 'Auto-increment primary key',
    preview_id VARCHAR(64) NOT NULL COMMENT 'Opaque preview identity',
    scope_key VARCHAR(64) NOT NULL COMMENT 'SHA256 environment workflow actor scope',
    expires_ms BIGINT NOT NULL COMMENT 'Preview expiry in Unix milliseconds',
    dto_json LONGTEXT NOT NULL COMMENT 'Only structurally redacted preview DTO',
    frozen_cipher LONGTEXT COMMENT 'AES256GCM protected immutable delivery snapshot',
    gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT 'Created time',
    gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT 'Modified time',
    UNIQUE(preview_id)
  ) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS workflow_callback_compensation_batches (
    id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT 'Auto-increment primary key',
    batch_id VARCHAR(64) NOT NULL COMMENT 'Opaque batch identity',
    environment_key VARCHAR(64) NOT NULL COMMENT 'SHA256 deployment environment',
    scope_key VARCHAR(64) NOT NULL COMMENT 'SHA256 environment workflow actor scope',
    request_key VARCHAR(64) NOT NULL COMMENT 'SHA256 actor scope plus client request key',
    request_hash VARCHAR(64) NOT NULL COMMENT 'SHA256 immutable selection and reason',
    metadata_json LONGTEXT NOT NULL COMMENT 'Batch ownership and operator reason without payload or secret',
    stopped BIGINT NOT NULL DEFAULT 0 COMMENT 'Administrator stopped queued work',
    gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT 'Created time',
    gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT 'Modified time',
    UNIQUE(batch_id),
    UNIQUE(request_key)
  ) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS workflow_callback_compensation_items (
    id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT 'Auto-increment primary key',
    item_id VARCHAR(64) NOT NULL COMMENT 'Opaque frozen item identity',
    batch_id VARCHAR(64) NOT NULL COMMENT 'Opaque batch identity',
    environment_key VARCHAR(64) NOT NULL COMMENT 'SHA256 deployment environment',
    delivery_key VARCHAR(64) NOT NULL COMMENT 'SHA256 env workflow flow generation event receiver without config version',
    flow_id TEXT NOT NULL COMMENT 'Authoritative workflow run identity',
    state VARCHAR(16) NOT NULL COMMENT 'Durable single-attempt delivery state',
    frozen_cipher LONGTEXT NOT NULL COMMENT 'AES256GCM protected immutable delivery snapshot',
    reason VARCHAR(128) COMMENT 'Non-sensitive system reason code',
    http_status BIGINT COMMENT 'HTTP response status only',
    sending_until_ms BIGINT COMMENT 'Sending uncertainty deadline in Unix milliseconds',
    audit_pending BIGINT NOT NULL DEFAULT 0 COMMENT 'Persisted outcome needs original audit repair only',
    audit_id BIGINT COMMENT 'Associated original HTTP audit row',
    gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT 'Created time',
    gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT 'Modified time',
    UNIQUE(item_id),
    UNIQUE(delivery_key),
    INDEX idx_cb_comp_batch(batch_id),
    INDEX idx_cb_comp_work(environment_key, state)
  ) ENGINE=InnoDB;
