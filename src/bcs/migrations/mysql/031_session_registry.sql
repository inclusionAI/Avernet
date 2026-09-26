-- Session identity outlives individual ChatRuns and Group deletion.
CREATE TABLE bcs_session_registry (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  env VARCHAR(64) NOT NULL,
  session_id VARCHAR(128) NOT NULL,
  session_type VARCHAR(32) NOT NULL,
  current_msg_seq BIGINT NULL,
  gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_session_registry (env, session_id)
) DEFAULT CHARSET=utf8mb4;
INSERT INTO bcs_session_registry (env, session_id, session_type, current_msg_seq)
SELECT env, session_id, 'group', NULL FROM bcs_group_sessions;
ALTER TABLE bcs_messages DROP INDEX uk_session_seq,
  ADD UNIQUE KEY uk_session_seq (env, session_id, session_seq);
ALTER TABLE bcs_chat_runs ADD COLUMN delivery_id VARCHAR(128) NULL,
  ADD COLUMN source_message_id VARCHAR(64) NULL,
  ADD KEY idx_env_delivery (env, delivery_id);
