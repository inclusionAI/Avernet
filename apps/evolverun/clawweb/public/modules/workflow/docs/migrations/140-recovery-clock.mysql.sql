-- Apply before enabling engine recovery clocks. Preserve these tables on rollback.
CREATE TABLE IF NOT EXISTS flow_recovery_clocks (
      id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT '主键ID',
      flow_id VARCHAR(190) NOT NULL COMMENT 'flow_id',
      owner_instance_id VARCHAR(190) NOT NULL COMMENT 'owner_instance_id',
      boot_id VARCHAR(190) NOT NULL COMMENT 'boot_id',
      timeout_epoch BIGINT NOT NULL COMMENT 'timeout_epoch',
      clock_seq BIGINT NOT NULL COMMENT 'clock_seq',
      elapsed_ms BIGINT NOT NULL COMMENT 'elapsed_ms',
      phase VARCHAR(32) NOT NULL COMMENT 'phase',
      retry_status VARCHAR(32) COMMENT 'retry_status',
      gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT 'gmt_create',
      gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT 'gmt_modified',
      UNIQUE (flow_id)
    );

CREATE TABLE IF NOT EXISTS flow_recovery_retries (
      id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT '主键ID',
      flow_id VARCHAR(190) NOT NULL COMMENT 'flow_id',
      request_id VARCHAR(190) NOT NULL COMMENT 'request_id',
      idempotency_key VARCHAR(64) NOT NULL COMMENT 'idempotency_key',
      request_json TEXT NOT NULL COMMENT 'request_json',
      gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT 'gmt_create',
      gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT 'gmt_modified',
      UNIQUE (idempotency_key)
    );
