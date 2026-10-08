-- Additive MySQL migration. Apply BEFORE deploying the TC delete/outbox writer.
-- No historical backfill. Pausing the worker must NOT delete this table.
CREATE TABLE IF NOT EXISTS ac_tc_resource_withdrawal (
  event_id VARCHAR(160) NOT NULL,
  res_id VARCHAR(128) NOT NULL,
  tenant VARCHAR(128) NOT NULL,
  status VARCHAR(16) NOT NULL DEFAULT 'pending',
  attempts INT NOT NULL DEFAULT 0,
  retry_count INT NOT NULL DEFAULT 0,
  available_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  lease_token VARCHAR(32) NULL,
  lease_until DATETIME NULL,
  accepted_at DATETIME NULL,
  last_error VARCHAR(64) NOT NULL DEFAULT '',
  replay_actor VARCHAR(128) NOT NULL DEFAULT '',
  replay_reason VARCHAR(512) NOT NULL DEFAULT '',
  PRIMARY KEY (event_id),
  UNIQUE KEY uk_tc_withdrawal_resource (res_id),
  KEY ix_tc_withdrawal_due (tenant, status, available_at),
  KEY ix_tc_withdrawal_lease (tenant, status, lease_until)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
