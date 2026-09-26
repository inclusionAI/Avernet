CREATE TABLE IF NOT EXISTS bcs_session_registry (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  env TEXT NOT NULL,
  session_id TEXT NOT NULL,
  session_type TEXT NOT NULL,
  current_msg_seq INTEGER,
  gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(env, session_id),
  CHECK ((session_type = 'group' AND current_msg_seq IS NULL)
    OR (session_type = 'direct_a2a' AND current_msg_seq IS NOT NULL AND current_msg_seq >= 0))
);
INSERT INTO bcs_session_registry (env, session_id, session_type, current_msg_seq)
SELECT env, session_id, 'group', NULL FROM bcs_group_sessions;
DROP INDEX IF EXISTS uk_messages_session_seq;
CREATE UNIQUE INDEX uk_messages_session_seq ON bcs_messages(env, session_id, session_seq);
ALTER TABLE bcs_chat_runs ADD COLUMN delivery_id TEXT;
ALTER TABLE bcs_chat_runs ADD COLUMN source_message_id TEXT;
CREATE INDEX idx_chat_runs_delivery ON bcs_chat_runs(env, delivery_id);
