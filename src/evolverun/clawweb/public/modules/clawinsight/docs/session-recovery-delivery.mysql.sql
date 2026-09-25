-- Provision in the existing ClawInsight database before enabling session recovery.
CREATE TABLE IF NOT EXISTS insight_session_recovery_delivery (
    id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT '主键ID',
    event_id VARBINARY(512) NOT NULL,
    delivery_key VARBINARY(128) NOT NULL,
    request_fingerprint VARCHAR(64) NOT NULL,
    started_at_ms BIGINT NOT NULL,
    settle_after_ms BIGINT NOT NULL,
    outcome_json TEXT DEFAULT NULL,
    callback_delivered BIGINT NOT NULL DEFAULT 0,
    UNIQUE INDEX uk_recovery_delivery (event_id, delivery_key)
  ) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;
